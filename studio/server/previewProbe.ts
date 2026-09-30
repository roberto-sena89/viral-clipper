/**
 * Verifica o caminho de PRODUÇÃO: o build em `dist/` servido pelo `vite
 * preview` consegue falar com a API?
 *
 * Por que isto não está coberto pelos outros testes: todos eles batem direto na
 * API (porta 3000). A interface construída é servida noutra porta e depende do
 * proxy de `/api`. Se o proxy não valer para o preview, o app abre e toda
 * chamada de API dá 404 — e nenhum teste atual percebe, porque nenhum deles
 * passa pelo preview.
 *
 * O `preview.proxy` cai para `server.proxy` quando não é declarado, então em
 * teoria funciona. "Em teoria" é exatamente o que este script troca por medição.
 *
 * Roda: npx tsx server/probe_preview.ts
 */
import http from 'node:http';
import { spawn, type ChildProcess } from 'node:child_process';

const API_PORT = 3000;
const PREVIEW_PORT = 4173;

process.env.PORT = String(API_PORT);

function get(port: number, path: string): Promise<{ status: number; text: string }> {
  return new Promise((resolve) => {
    const req = http.request(
      { host: '127.0.0.1', port, path, method: 'GET', agent: false },
      (res) => {
        let text = '';
        res.setEncoding('utf8');
        res.on('data', (c) => (text += c));
        res.on('end', () => resolve({ status: res.statusCode ?? 0, text }));
      },
    );
    req.on('error', (e) => resolve({ status: 0, text: String(e.message) }));
    req.end();
  });
}

async function waitFor(port: number, path: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await get(port, path);
    if (r.status > 0) return true;
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

let preview: ChildProcess | null = null;

try {
  // ── 1. API ──────────────────────────────────────────────────────────────
  const { server } = await import('./index.ts');
  if (!(await waitFor(API_PORT, '/api/studio/catalog', 8000))) {
    throw new Error(`a API nao subiu em ${API_PORT}`);
  }
  console.log(`API   : ouvindo em ${API_PORT}`);

  // ── 2. vite preview sobre dist/ ─────────────────────────────────────────
  preview = spawn(
    process.execPath,
    ['node_modules/vite/bin/vite.js', 'preview', '--port', String(PREVIEW_PORT), '--host', '127.0.0.1', '--strictPort'],
    { cwd: process.cwd(), stdio: ['ignore', 'pipe', 'pipe'] },
  );
  let previewLog = '';
  preview.stdout?.on('data', (d) => (previewLog += d.toString()));
  preview.stderr?.on('data', (d) => (previewLog += d.toString()));

  if (!(await waitFor(PREVIEW_PORT, '/', 20000))) {
    throw new Error(`o vite preview nao subiu em ${PREVIEW_PORT}:\n${previewLog}`);
  }
  console.log(`preview: ouvindo em ${PREVIEW_PORT}`);

  let falhas = 0;
  const check = (label: string, ok: boolean, detail = '') => {
    if (!ok) falhas++;
    console.log(`${ok ? 'OK  ' : 'FALHA'} ${label}${detail ? ` — ${detail}` : ''}`);
  };

  // ── 3. A SPA é servida ──────────────────────────────────────────────────
  const raiz = await get(PREVIEW_PORT, '/');
  check('a SPA responde em /', raiz.status === 200, `HTTP ${raiz.status}`);
  check('o HTML tem o ponto de montagem', /id="root"/.test(raiz.text));
  check('o HTML referencia o bundle', /assets\/index-[\w-]+\.js/.test(raiz.text));

  // ── 4. O proxy de /api atravessa para a API ─────────────────────────────
  const viaPreview = await get(PREVIEW_PORT, '/api/studio/options');
  check('GET /api/studio/options pelo preview', viaPreview.status === 200, `HTTP ${viaPreview.status}`);
  let options: any = null;
  try {
    options = JSON.parse(viaPreview.text);
  } catch {
    /* não-JSON */
  }
  check('o corpo veio JSON da API', options?.ok === true);

  // A guarda do mediapipe vale também no caminho de produção.
  const layout = options?.options?.find((o: any) => o.key === 'layout');
  check('descrição de layout sem mediapipe', !!layout && !/mediapipe/i.test(layout.descricao));
  check('descrição de layout nomeia opencv', !!layout && /opencv/i.test(layout.descricao));

  const preflight = await get(PREVIEW_PORT, '/api/studio/preflight');
  check('GET /api/studio/preflight pelo preview', preflight.status === 200, `HTTP ${preflight.status}`);

  // ── 5. O bundle contém os marcadores da interface ───────────────────────
  const bundlePath = raiz.text.match(/assets\/(index-[\w-]+\.js)/)?.[1];
  check('bundle localizado no HTML', !!bundlePath);
  if (bundlePath) {
    const bundle = await get(PREVIEW_PORT, `/assets/${bundlePath}`);
    check('bundle é servido', bundle.status === 200, `HTTP ${bundle.status}`);
    for (const marca of [
      'Confirmação necessária',
      'Executar agora',
      'Nada roda sem este clique',
      '/api/studio/approve/',
      '/api/studio/commit/',
      '/api/studio/events',
      // O freio. Sem esta marca, o botão Parar poderia ficar de fora do bundle
      // de produção (ex.: poda por tree-shaking) e ninguém notaria até precisar
      // parar um render de horas.
      '/api/studio/cancel/',
    ]) {
      check(`bundle contém "${marca}"`, bundle.text.includes(marca));
    }
  }

  console.log(`\n${falhas === 0 ? 'TUDO OK' : `${falhas} FALHA(S)`}\n`);
  preview.kill('SIGTERM');
  server.closeAllConnections();
  server.close();
  process.exit(falhas === 0 ? 0 : 1);
} catch (error) {
  console.error('erro:', error instanceof Error ? error.message : error);
  preview?.kill('SIGTERM');
  process.exit(2);
}
