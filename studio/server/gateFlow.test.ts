/**
 * Teste de integração do gate: formato do plano + recusa de commit sem
 * aprovação.
 *
 * ─── Por que este arquivo NÃO chama o LLM ────────────────────────────────────
 *
 * A primeira versão deste teste pedia a conversa inteira ao modelo para
 * conseguir um plano. Isso o deixava intermitente: o modelo às vezes chamava
 * `clip_get_options` e encerrava o turno sem chamar `clip_plan` — e o teste
 * falhava com "deveria ter extraído um plano", que não é o defeito que ele
 * existe para pegar. Falhava em ~1 de cada 4 execuções.
 *
 * Um teste intermitente é pior que nenhum: ele treina a pessoa a reexecutar até
 * passar, e no dia em que a falha for real ela vai ser ignorada. As duas
 * afirmações que importam aqui não precisam do modelo:
 *
 *   1. o payload que o `clip_plan` devolve é digerível pelo MESMO parser que a
 *      interface usa (`src/utils/planParser.ts`). Se o formato mudar e o
 *      frontend não acompanhar, o card de confirmação some sem aviso — falha
 *      silenciosa, a pior categoria;
 *   2. o servidor RECUSA commit de plano não aprovado (o gate é do servidor,
 *      não do modelo).
 *
 * A parte que depende do modelo (o agente entrega um plano sozinho?) virou
 * `gateLlm.test.ts`, com script próprio (`npm run test:llm`).
 *
 * Não dispara o render de verdade: o objetivo é o gate. O processo é encerrado
 * no cleanup.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { parsePlanResult } from '../src/utils/planParser.ts';
import { buildPlanPayload } from './planPayload.ts';
import { cancelAll, createPlan } from './clipRunner.ts';

const PORT = 3299;
const URL_VIDEO = 'https://www.youtube.com/watch?v=7OWUenfg2-U';

// ── 1. O formato. Sem servidor, sem rede, sem LLM. ──────────────────────────

test('o payload real do clip_plan e digerivel pelo parser da UI', () => {
  const plan = createPlan({ url: URL_VIDEO, params: { count: 3 } });

  // A serializacao REAL: a mesma funcao que o handler do clip_plan chama, e
  // que ja devolve o JSON indentado. Nao ha formato reconstruido aqui — e por
  // isso renomear um campo na producao passa a quebrar este teste.
  const content = buildPlanPayload(plan);

  // Prova de que a serialização é multilinha — é por isso que o parser não
  // pode assumir JSON de uma linha só.
  assert.ok(content.includes('\n'), 'o payload deveria ser JSON indentado');

  const parsed = parsePlanResult(content, URL_VIDEO);
  assert.ok(parsed, 'o parser da UI deveria extrair um plano do payload real');
  assert.equal(parsed.planId, plan.id, 'o plano_id tem de sobreviver ao parser');
  assert.ok(parsed.command.includes(' -m viralclipper'), `comando inesperado: ${parsed.command}`);
  assert.ok(parsed.summary.length > 0, 'o resumo deveria ter linhas');
  assert.ok(
    !parsed.summary.some((l) => /^URL\s*:/i.test(l)),
    'a linha URL: deve ser removida do resumo (o card mostra a URL à parte)',
  );
});

test('o payload de erro NAO vira cartao de confirmacao', () => {
  // Formato real do handler de clip_plan quando validateRequest recusa:
  // JSON.stringify({ erro: 'Parâmetro inválido: ...' }), sem plano_id e sem
  // comando. Se o parser aceitasse isso, a interface mostraria um cartão
  // "Executar" para um plano que não existe.
  const erro = JSON.stringify({ erro: 'Parâmetro inválido: count deve ser >= 1' }, null, 2);
  assert.equal(parsePlanResult(erro, URL_VIDEO), null, 'erro não pode virar plano');
});

// ── 2. O gate. Servidor real, HTTP real, sem LLM. ───────────────────────────

test('commit sem aprovacao e recusado; com aprovacao, aceito', async (t) => {
  process.env.PORT = String(PORT);
  // Banco em memoria: nenhum teste deve escrever no banco de producao.
  process.env.STUDIO_DB = ':memory:';
  const { server } = await import('./index.ts');

  // Fecha o servidor no fim, e a ordem importa: `close()` sozinho para de
  // aceitar conexões novas mas ESPERA as existentes terminarem, e o socket
  // keep-alive do agente global do Node segurava o processo por ~99s depois de
  // o teste já ter passado. `closeAllConnections()` destrói os sockets, e só
  // então o `close()` consegue drenar. Medido: 117s -> 1,8s.
  //
  // `cancelAll()` antes porque o passo 3 COMMITA um plano de verdade, e commit
  // spawna o pipeline. Sem encerrar, o processo filho segurava o event loop por
  // mais de 2 minutos depois de o teste passar.
  t.after(() => {
    cancelAll();
    server.closeAllConnections();
    server.close();
  });
  await new Promise((r) => setTimeout(r, 800));

  const post = (path: string, body?: unknown) =>
    new Promise<{ status: number; json: any }>((resolve) => {
      const payload = body ? JSON.stringify(body) : '';
      const req = http.request(
        {
          host: '127.0.0.1',
          port: PORT,
          path,
          method: 'POST',
          // `agent: false` desliga o pool: com o agente global (keep-alive),
          // cada requisição deixava um socket vivo segurando o event loop.
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

  const plan = createPlan({ url: URL_VIDEO, params: { count: 1 } });

  // ── Recusa sem aprovação ────────────────────────────────────────────────
  const cedo = await post(`/api/studio/commit/${plan.id}`);
  assert.equal(cedo.status, 400, 'commit sem aprovacao deve falhar');
  assert.equal(cedo.json?.ok, false);
  assert.match(String(cedo.json?.error), /aprovado/i, 'a mensagem deve falar de aprovacao');

  // ── Aprovar e então o commit passa ──────────────────────────────────────
  const ok = await post(`/api/studio/approve/${plan.id}`);
  assert.equal(ok.status, 200);
  assert.equal(ok.json?.approved, true);

  const depois = await post(`/api/studio/commit/${plan.id}`);
  assert.equal(depois.status, 200, 'com aprovacao o commit deve ser aceito');
  assert.ok(depois.json?.run_id, 'o commit deve devolver um run_id');
});
