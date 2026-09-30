/**
 * Trava de execução única: dois renders não podem rodar ao mesmo tempo.
 *
 * ─── O que este arquivo existe para impedir ──────────────────────────────────
 *
 * `work_path()` do lado Python é `output/_work` para QUALQUER execução — sem id
 * de run, sem lock. Dois processos escrevem os mesmos `source_audio.webm` e
 * `analysis.wav`. O sintoma não é erro: é resultado errado em silêncio. Medido,
 * ao disparar dois planos em paralelo: um run analisou 1127 s de um vídeo de
 * 793,5 s, porque reaproveitou o áudio que o outro processo tinha acabado de
 * escrever. Passou por todos os guardas seguintes e viraria clipe publicado.
 *
 * ─── Por que o teste olha o ESTADO do plano, e não só o status code ──────────
 *
 * Um 400 sozinho não prova nada: um bug que recusasse todo commit passaria. As
 * duas asserções que dão sentido ao teste são as outras:
 *
 *   - depois da recusa, o plano B continua `approved: true` e `consumed: false`
 *     (recusar não pode queimar o plano — o usuário não deveria precisar gerar
 *     outro só porque tentou cedo demais);
 *   - depois que A termina, o commit de B é ACEITO (a trava é uma trava, não um
 *     bloqueio permanente).
 *
 * Determinístico no que importa: sem LLM. Ele commita um plano de verdade — é a
 * única forma de existir um run vivo para a trava recusar — e mata em seguida,
 * como o `cancelRun.test.ts`. Por isso o cleanup chama `cancelAll()` antes de
 * fechar o servidor: um filho vivo segura o event loop e o `node --test` fica
 * pendurado.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { cancelAll, subscribe, type RunEvent } from './clipRunner.ts';

const PORT = 3298;
const URL_VIDEO = 'https://www.youtube.com/watch?v=7OWUenfg2-U';

/** Requisição crua, sem pool de sockets (keep-alive segura o event loop). */
function pedir(
  path: string,
  method: 'GET' | 'POST' = 'GET',
  body?: unknown,
): Promise<{ status: number; json: any }> {
  return new Promise((resolve) => {
    const payload = body !== undefined ? JSON.stringify(body) : '';
    const req = http.request(
      {
        host: '127.0.0.1',
        port: PORT,
        path,
        method,
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

/** Cria um plano, aprova e devolve o id. Não commita. */
async function planoAprovado(): Promise<string> {
  const plano = await pedir('/api/studio/plan', 'POST', {
    url: URL_VIDEO,
    params: { count: 1 },
  });
  assert.equal(plano.status, 200, 'o plano deveria ser criado');
  const id: string = plano.json?.plan_id;
  assert.ok(id, 'o plano precisa de id');

  const aprovado = await pedir(`/api/studio/approve/${id}`, 'POST');
  assert.equal(aprovado.status, 200, 'o plano deveria ser aprovado');
  return id;
}

test('um segundo render não começa enquanto o primeiro está vivo', async (t) => {
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

  // ── 1. O primeiro render, para haver um run vivo ────────────────────────
  const planoA = await planoAprovado();
  const commitA = await pedir(`/api/studio/commit/${planoA}`, 'POST');
  assert.equal(commitA.status, 200, 'o primeiro commit deveria disparar o processo');
  const runA: string = commitA.json?.run_id;
  assert.ok(runA, 'o commit deve devolver um run_id');
  assert.ok(
    await esperar(eventos, (e) => e.type === 'started' && e.runId === runA),
    'o processo A deveria ter começado',
  );

  // ── 2. O segundo plano é preparado e aprovado normalmente ───────────────
  //
  // Criar e aprovar NÃO são bloqueados de propósito: o usuário pode preparar o
  // próximo plano enquanto o atual roda. O que não pode é disparar.
  const planoB = await planoAprovado();

  const commitB = await pedir(`/api/studio/commit/${planoB}`, 'POST');
  assert.equal(commitB.status, 400, 'commitar com um run vivo deve ser recusado');
  assert.equal(commitB.json?.ok, false);
  assert.match(
    String(commitB.json?.error),
    /em andamento/i,
    'a mensagem precisa dizer que já existe uma execução rodando',
  );
  assert.match(
    String(commitB.json?.error),
    new RegExp(runA),
    'a mensagem precisa nomear o run que está bloqueando — é o que o botão Parar usa',
  );

  // ── 3. A recusa não queimou o plano ─────────────────────────────────────
  const estadoB = await pedir(`/api/studio/plan/${planoB}`);
  assert.equal(estadoB.status, 200);
  assert.equal(estadoB.json?.approved, true, 'o plano B continua aprovado');
  assert.equal(
    estadoB.json?.consumed,
    false,
    'a recusa não pode consumir o plano: senão o usuário teria de gerar outro',
  );

  // ── 4. O run A termina ──────────────────────────────────────────────────
  assert.equal((await pedir(`/api/studio/cancel/${runA}`, 'POST')).status, 200);
  assert.ok(
    await esperar(eventos, (e) => e.type === 'exit' && e.runId === runA),
    'o run A deveria ter terminado',
  );

  // ── 5. A trava libera ───────────────────────────────────────────────────
  //
  // Sem esta etapa, um bug que bloqueasse TODO commit depois do primeiro
  // passaria no teste. A trava é uma trava, não um bloqueio permanente.
  const commitB2 = await pedir(`/api/studio/commit/${planoB}`, 'POST');
  assert.equal(commitB2.status, 200, 'com A terminado, o plano B deve poder rodar');
  const runB: string = commitB2.json?.run_id;
  assert.ok(runB, 'o segundo commit deve devolver um run_id');
  assert.notEqual(runB, runA, 'cada execução tem o próprio run_id');

  assert.equal(
    (await pedir(`/api/studio/cancel/${runB}`, 'POST')).status,
    200,
    'e ele também tem de poder ser parado',
  );
});
