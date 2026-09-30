/**
 * Cobertura HTTP das rotas do Studio.
 *
 * ─── Por que este arquivo existe ─────────────────────────────────────────────
 *
 * `studioSmoke.ts` exercita o caminho ponta a ponta, mas pela **API de módulo**:
 * chama `createPlan`, `approvePlan`, `commitPlan` direto. A interface não faz
 * isso — ela fala HTTP. Entre a função e a rota há serialização, status code e
 * formato de corpo, e nada disso era verificado.
 *
 * Determinístico: sem LLM, sem rede externa. A única parte que sai do processo é
 * o preflight, que roda o Python do venv.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

const PORT = 3296;
const URL_VIDEO = 'https://www.youtube.com/watch?v=7OWUenfg2-U';

/** Requisição crua. Devolve status, texto e, quando der, o JSON. */
function request(
  path: string,
  opts: { method?: string; body?: unknown; raw?: boolean } = {},
): Promise<{ status: number; text: string; json: any; headers: http.IncomingHttpHeaders }> {
  return new Promise((resolve) => {
    const payload = opts.body !== undefined ? JSON.stringify(opts.body) : '';
    const req = http.request(
      {
        host: '127.0.0.1',
        port: PORT,
        path,
        method: opts.method ?? (payload ? 'POST' : 'GET'),
        // Sem pool: socket keep-alive pendurado segura o event loop no fim.
        agent: false,
        headers: payload
          ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
          : {},
      },
      (res) => {
        let text = '';
        res.setEncoding('utf8');
        res.on('data', (c) => (text += c));
        res.on('end', () => {
          let json: any = null;
          try {
            json = JSON.parse(text);
          } catch {
            /* resposta não-JSON (ex.: index.html) */
          }
          resolve({ status: res.statusCode ?? 0, text, json, headers: res.headers });
        });
      },
    );
    req.on('error', () => resolve({ status: 0, text: '', json: null, headers: {} }));
    if (payload) req.write(payload);
    req.end();
  });
}

test('rotas HTTP do Studio', async (t) => {
  process.env.PORT = String(PORT);
  // Banco em memoria: nenhum teste deve escrever no banco de producao.
  process.env.STUDIO_DB = ':memory:';
  const { server } = await import('./index.ts');
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  await new Promise((r) => setTimeout(r, 800));

  // ── /api/studio/options ─────────────────────────────────────────────────
  const options = await request('/api/studio/options');
  assert.equal(options.status, 200);
  assert.equal(options.json?.ok, true);
  assert.ok(Array.isArray(options.json?.options), 'options deve ser lista');
  assert.ok(options.json.options.length > 0);

  // GUARDA DE REGRESSÃO. A descrição de `layout` é o texto que o MODELO lê
  // (via clip_get_options) e repassa ao usuário. Ela já afirmou que
  // `focus` "só funciona com mediapipe instalado" — o que é falso: o detector
  // é o Haar cascade do OpenCV e está ativo. A afirmação errada foi corrigida
  // no README, no prompt e no preflight, e sobreviveu aqui, que é justamente
  // o lugar de maior alcance. Este teste impede a terceira reincidência.
  //
  // O campo é `descricao` (português) — `optionsForAgent()` traduz o cardápio
  // interno para o formato que o agente consome.
  const layout = options.json.options.find((o: any) => o.key === 'layout');
  assert.ok(layout, 'layout deve estar no cardápio');
  assert.equal(typeof layout.descricao, 'string', 'layout deve ter descrição');
  assert.ok(
    !/mediapipe/i.test(layout.descricao),
    `a descrição de layout não pode citar mediapipe — é o texto que o modelo lê: "${layout.descricao}"`,
  );
  assert.match(layout.descricao, /opencv/i, 'a descrição deve nomear o detector real');

  // O system prompt pode citar mediapipe, mas só para dizer que NÃO se instala.
  assert.ok(typeof options.json.director_prompt === 'string');
  assert.ok(options.json.director_prompt.length > 200, 'director_prompt veio vazio');

  // GUARDA DE REGRESSÃO da memória. O prompt é o único lugar onde o agente
  // aprende que existem as ferramentas de memória e que pergunta repetida é
  // proibida. Se esta seção sair do prompt, as ferramentas continuam existindo
  // (o teste do payload as vê) e o agente simplesmente nunca as usa — falha
  // silenciosa, que é o modo como o defeito original sobreviveu.
  assert.match(
    options.json.director_prompt,
    /lembrar_preferencia/,
    'o prompt tem de ensinar a ferramenta de memória ao agente',
  );
  assert.match(
    options.json.director_prompt,
    /nunca pergunte o que já está respondido/i,
    'o prompt tem de proibir a pergunta repetida',
  );

  // ── /api/studio/catalog ─────────────────────────────────────────────────
  const catalog = await request('/api/studio/catalog');
  assert.equal(catalog.status, 200);
  assert.ok(Array.isArray(catalog.json?.options));

  // ── /api/studio/preflight ───────────────────────────────────────────────
  const preflight = await request('/api/studio/preflight');
  assert.equal(preflight.status, 200);
  assert.equal(typeof preflight.json?.ok, 'boolean');
  assert.ok(Array.isArray(preflight.json?.report?.checks), 'report.checks deve ser lista');

  const caps = preflight.json?.report?.capabilities;
  assert.ok(caps, 'preflight deve expor capabilities');
  // Este é o valor que o agente usa para decidir se pode prometer enquadramento
  // de rosto. Se falhar aqui, a causa provável é o venv ter pulado para
  // opencv-python 5.x, que removeu CascadeClassifier — e a correção é
  // `pip install "opencv-python-headless<5"`, não instalar mediapipe.
  assert.equal(
    caps.faceDetection,
    true,
    'detecção de rosto indisponível: verifique `reframe.available_backend()` no venv',
  );

  // ── /api/studio/plan — válido ───────────────────────────────────────────
  const plan = await request('/api/studio/plan', {
    body: { url: URL_VIDEO, params: { count: 2, target_duration: 40 } },
  });
  assert.equal(plan.status, 200);
  assert.equal(plan.json?.ok, true);
  assert.ok(plan.json?.plan_id, 'deve devolver plan_id');
  assert.equal(plan.json?.requires_approval, true, 'o plano tem de exigir aprovação');
  assert.ok(Array.isArray(plan.json?.argv));
  assert.equal(plan.json.argv[0], '-m');
  assert.equal(plan.json.argv[1], 'viralclipper');
  assert.match(String(plan.json?.command), /viralclipper/);
  assert.ok(Array.isArray(plan.json?.summary) && plan.json.summary.length > 0);

  // ── /api/studio/plan — inválido ─────────────────────────────────────────
  const bad = await request('/api/studio/plan', {
    body: { url: URL_VIDEO, params: { layout: 'diagonal' } },
  });
  assert.ok(bad.status >= 400, `layout inválido deveria falhar, veio ${bad.status}`);
  assert.equal(bad.json?.ok, false);
  assert.ok(bad.json?.error, 'deve explicar o erro para o agente corrigir');

  // ── /api/studio/plan/:id ────────────────────────────────────────────────
  const estado = await request(`/api/studio/plan/${plan.json.plan_id}`);
  assert.equal(estado.status, 200);
  assert.equal(estado.json?.approved, false, 'plano recém-criado não está aprovado');
  assert.equal(estado.json?.consumed, false);

  const inexistente = await request('/api/studio/plan/nao-existe');
  assert.equal(inexistente.status, 404);
  assert.equal(inexistente.json?.ok, false);

  // ── /api/studio/approve — id inexistente ────────────────────────────────
  const approveRuim = await request('/api/studio/approve/nao-existe', { body: {} });
  assert.equal(approveRuim.status, 404);

  // ── /api/studio/events — SSE ────────────────────────────────────────────
  const primeiroFrame = await new Promise<string | null>((resolve) => {
    const req = http.request(
      { host: '127.0.0.1', port: PORT, path: '/api/studio/events', method: 'GET', agent: false },
      (res) => {
        assert.match(String(res.headers['content-type']), /text\/event-stream/);
        res.setEncoding('utf8');
        res.on('data', (chunk: string) => {
          resolve(chunk);
          req.destroy(); // sem isto o canal fica aberto e segura o event loop
        });
      },
    );
    req.on('error', () => resolve(null));
    req.end();
    setTimeout(() => resolve(null), 5000);
  });
  assert.ok(primeiroFrame, 'o canal SSE deveria mandar algo imediatamente');
  assert.match(primeiroFrame, /^data: /, 'o primeiro frame deve ser um evento SSE');
  const evento = JSON.parse(primeiroFrame.replace(/^data: /, '').split('\n')[0]);
  assert.equal(evento.type, 'hello', 'o primeiro evento do canal é hello');

  // ── /api/studio/preferences ─────────────────────────────────────────────
  //
  // Estas duas rotas são o que torna a memória do agente uma ferramenta em vez
  // de um passivo: sem poder LER o que o agente registrou, o usuário não tem
  // como descobrir de onde veio uma preferência errada ("ele sempre quer 10
  // cortes") nem como desfazê-la.
  const prefsVazias = await request('/api/studio/preferences');
  assert.equal(prefsVazias.status, 200);
  assert.equal(prefsVazias.json?.ok, true);
  assert.equal(prefsVazias.json?.count, 0, 'banco em memória: começa sem preferências');

  // A gravação é feita pelo mesmo módulo do banco que o servidor usa (o cache de
  // módulo do ESM garante a instância). É o caminho que a ferramenta
  // `lembrar_preferencia` percorre.
  const db = await import('./db.ts');
  db.setPreference('count', '4');

  const prefsComUma = await request('/api/studio/preferences');
  assert.equal(prefsComUma.json?.count, 1);
  assert.equal(prefsComUma.json?.preferences?.[0]?.key, 'count');
  assert.equal(prefsComUma.json?.preferences?.[0]?.value, '4');

  const esquecer = await request('/api/studio/preferences/count', { method: 'DELETE' });
  assert.equal(esquecer.status, 200);
  assert.equal(esquecer.json?.ok, true);
  assert.equal(esquecer.json?.preferences?.length, 0, 'a preferência tem de sumir da lista');

  const esquecerDeNovo = await request('/api/studio/preferences/count', { method: 'DELETE' });
  assert.equal(esquecerDeNovo.status, 404, 'apagar o que não existe é 404, não sucesso');

  // ── A API NÃO serve a interface, de propósito ───────────────────────────
  //
  // `GET /` devolve 404. Quem serve a SPA é o Vite (dev) ou o `vite preview`
  // (build), e ambos fazem proxy de `/api` para cá. Em `vite preview` isso
  // funciona sem configuração extra porque `preview.proxy` cai para
  // `server.proxy` quando não é declarado — verificado no bundle do Vite:
  // `proxy: preview?.proxy ?? server.proxy`.
  //
  // O teste fixa esse contrato para que ninguém "conserte" a API adicionando um
  // catch-all estático: isso mudaria o dono da rota `/` e poderia mascarar
  // 404 de API como HTML.
  const raiz = await request('/');
  assert.equal(raiz.status, 404, 'a API é API-only; a SPA é servida pelo Vite');

  const rotaDesconhecida = await request('/api/nao-existe');
  assert.equal(rotaDesconhecida.status, 404, 'rota de API desconhecida deve dar 404');
});
