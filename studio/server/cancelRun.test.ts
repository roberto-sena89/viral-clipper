/**
 * Cancelamento de execução: a rota HTTP e o freio.
 *
 * ─── O que este arquivo existe para impedir ──────────────────────────────────
 *
 * `cancelRun` e `cancelAll` existiam no `clipRunner` desde o começo e nenhuma
 * rota HTTP os expunha — só os testes os chamavam. Na prática: um render
 * disparado por engano rodava até o fim sozinho, e com `RUN_TIMEOUT_MS` de 6
 * horas "até o fim" é uma tarde inteira de CPU. A interface não tinha freio.
 *
 * Testar a função `cancelRun` diretamente NÃO pegaria esse defeito: ela sempre
 * funcionou. O que faltava era o caminho HTTP, então é ele que este teste
 * percorre.
 *
 * Determinístico: sem LLM, sem rede externa. Ele COMMITA um plano de verdade
 * (é a única forma de existir um processo para cancelar) e mata em seguida.
 * Por isso o cleanup chama `cancelAll()` antes de fechar o servidor: um filho
 * vivo segura o event loop e o `node --test` ficaria pendurado.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { cancelAll, subscribe, type RunEvent } from './clipRunner.ts';

const PORT = 3297;
const URL_VIDEO = 'https://www.youtube.com/watch?v=7OWUenfg2-U';

/** Requisição crua, sem pool de sockets (keep-alive segura o event loop). */
function post(path: string, body?: unknown): Promise<{ status: number; json: any }> {
  return new Promise((resolve) => {
    const payload = body !== undefined ? JSON.stringify(body) : '';
    const req = http.request(
      {
        host: '127.0.0.1',
        port: PORT,
        path,
        method: 'POST',
        agent: false,
        headers: payload
          ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
          : {},
      },
      (res) => {
        let b = '';
        res.on('data', (c) => (b += c));
        res.on('end', () => {
          let json: any = null;
          try {
            json = JSON.parse(b);
          } catch {
            /* resposta sem corpo JSON */
          }
          resolve({ status: res.statusCode ?? 0, json });
        });
      },
    );
    req.on('error', () => resolve({ status: 0, json: null }));
    if (payload) req.write(payload);
    req.end();
  });
}

/** Espera um evento aparecer na lista, sem dormir em loop apertado. */
async function esperar(
  eventos: RunEvent[],
  predicado: (e: RunEvent) => boolean,
  limiteMs = 15_000,
): Promise<RunEvent | null> {
  const inicio = Date.now();
  while (Date.now() - inicio < limiteMs) {
    const achado = eventos.find(predicado);
    if (achado) return achado;
    await new Promise((r) => setTimeout(r, 50));
  }
  return null;
}

test('a interface consegue parar um render em andamento', async (t) => {
  process.env.PORT = String(PORT);
  // Banco em memoria: nenhum teste deve escrever no banco de producao.
  process.env.STUDIO_DB = ':memory:';
  const { server } = await import('./index.ts');

  const eventos: RunEvent[] = [];
  const cancelarInscricao = subscribe((e) => eventos.push(e));

  t.after(() => {
    cancelarInscricao();
    cancelAll();
    server.closeAllConnections();
    server.close();
  });
  await new Promise((r) => setTimeout(r, 800));

  // ── 1. runId desconhecido: 404, não 500 ─────────────────────────────────
  //
  // A interface chama esta rota quando o usuário clica em Parar. Se o render
  // terminou sozinho um instante antes, o esperado é um "não há o que
  // cancelar" legível — não um erro de servidor.
  const fantasma = await post('/api/studio/cancel/nao-existe');
  assert.equal(fantasma.status, 404, 'cancelar run inexistente deve dar 404');
  assert.equal(fantasma.json?.ok, false);
  assert.match(
    String(fantasma.json?.error),
    /não encontrada|já terminou/i,
    'o erro deve dizer que não há execução para cancelar',
  );

  // ── 2. Um render de verdade, para haver o que parar ─────────────────────
  const plano = await post('/api/studio/plan', {
    url: URL_VIDEO,
    params: { count: 1 },
  });
  assert.equal(plano.status, 200, 'o plano deveria ser criado');
  assert.ok(plano.json?.plan_id);

  const aprovado = await post(`/api/studio/approve/${plano.json.plan_id}`);
  assert.equal(aprovado.status, 200);

  const commit = await post(`/api/studio/commit/${plano.json.plan_id}`);
  assert.equal(commit.status, 200, 'o commit deveria disparar o processo');
  const runId: string = commit.json?.run_id;
  assert.ok(runId, 'o commit deve devolver um run_id');

  // `commitPlan` registra o filho no mapa `running` de forma síncrona, antes
  // de responder. Então quando este await resolve já existe processo para
  // cancelar — não há corrida aqui.
  const iniciado = await esperar(eventos, (e) => e.type === 'started' && e.runId === runId);
  assert.ok(iniciado, 'o processo deveria ter começado');

  // ── 3. O freio ──────────────────────────────────────────────────────────
  const cancelamento = await post(`/api/studio/cancel/${runId}`);
  assert.equal(cancelamento.status, 200, 'cancelar um run vivo deve dar 200');
  assert.equal(cancelamento.json?.ok, true);
  assert.equal(cancelamento.json?.cancelled, true);

  // O pedido é confirmado pelo evento `cancelled`, emitido ANTES de o processo
  // morrer — é ele que faz o painel saber o motivo antes de o `exit` chegar.
  const pedido = await esperar(eventos, (e) => e.type === 'cancelled' && e.runId === runId);
  assert.ok(pedido, 'deveria emitir o evento cancelled');

  // ── 4. O processo morreu de verdade ─────────────────────────────────────
  //
  // `exit` só é emitido no handler de `close` do filho, que por definição só
  // roda depois que o sistema operacional recolheu o processo. Ou seja: este
  // evento é a prova de que o render parou, não de que o pedido saiu.
  const saida = await esperar(eventos, (e) => e.type === 'exit' && e.runId === runId);
  assert.ok(
    saida,
    `o processo não terminou em 15s. Eventos vistos: ${eventos.map((e) => e.type).join(', ') || 'nenhum'}`,
  );
  assert.equal(
    saida.cancelled,
    true,
    'o exit precisa vir marcado como cancelado — é o que separa "cancelado" de "falhou" no painel',
  );

  // A ordem importa: o pedido antes do fim. Invertido, o painel não teria como
  // saber que o código não-zero foi intencional.
  assert.ok(
    eventos.indexOf(pedido) < eventos.indexOf(saida),
    'o evento cancelled deve preceder o exit',
  );

  // Guarda contra um teste que passa sem provar nada.
  //
  // A prova principal de que houve processo de verdade é o par acima: o evento
  // `cancelled` só é emitido por `cancelRun`, que só age se encontrar um filho
  // vivo no mapa `running` — e o `exit` veio depois. Isso já é suficiente.
  //
  // Esta linha cobre a sobra: se o Python não existir, o `spawn` falha, mas a
  // falha chega assíncrona. Dependendo do tempo, o `error` pode cair DEPOIS do
  // POST de cancelamento — e aí o cancel responde 200, o `exit` chega com
  // `cancelled: true` e o teste passaria sem ter matado nada. Verificado: com
  // `VIRCAL_PYTHON` inválido o caso comum é falhar antes, no 404 do passo 3;
  // esta é a rede para a corrida oposta.
  assert.ok(
    !eventos.some((e) => e.type === 'error' && e.runId === runId),
    `o processo nem chegou a subir: ${JSON.stringify(eventos.filter((e) => e.runId === runId))}`,
  );

  // ── 5. Cancelar de novo: já terminou ────────────────────────────────────
  const denovo = await post(`/api/studio/cancel/${runId}`);
  assert.equal(denovo.status, 404, 'depois do fim não há o que cancelar');
  assert.equal(denovo.json?.ok, false);
});
