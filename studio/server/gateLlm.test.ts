/**
 * Teste com LLM: o agente entrega um plano sozinho?
 *
 * Este é o teste que a versão anterior de `gateFlow.test.ts` era — e ele está
 * separado por um motivo. A conversa depende do humor do modelo: medido em 4
 * execuções, 3 entregaram o plano e 1 encerrou o turno depois de
 * `clip_get_options`, sem chamar `clip_plan`.
 *
 * Isso é um defeito real do produto (o agente às vezes não fecha o plano), mas
 * é um defeito de PROMPT, não do formato nem do gate. Misturado com as
 * asserções determinísticas, ele contaminava as duas: uma falha de prompt
 * aparecia como "o parser da UI não extraiu um plano", apontando para o arquivo
 * errado.
 *
 * Aqui a falha é informativa: o diagnóstico diz qual modelo atendeu, quais
 * ferramentas ele chamou e o que ele respondeu.
 *
 * Uso: npm run test:llm      (chama o provedor de verdade; leva ~20-60s)
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { parsePlanResult } from '../src/utils/planParser.ts';
import { cancelAll } from './clipRunner.ts';

const PORT = 3297;
const URL_VIDEO = 'https://www.youtube.com/watch?v=7OWUenfg2-U';

test('chat real: o agente entrega um plano com clip_plan', async (t) => {
  process.env.PORT = String(PORT);
  // Banco em memoria: este teste manda uma mensagem em /api/chat, que CRIA uma
  // sessao. Sem isto ele escrevia no banco de producao e a barra lateral do app
  // enchia de conversas de teste (35 sessoes medidas).
  process.env.STUDIO_DB = ':memory:';
  const { server } = await import('./index.ts');
  t.after(() => {
    cancelAll();
    server.closeAllConnections();
    server.close();
  });
  await new Promise((r) => setTimeout(r, 800));

  let planId: string | null = null;
  let commandSeen: string | null = null;
  let pressedCommit = false;

  // Diagnóstico. Quando o plano não sai, "deveria ter extraído um plano" não diz
  // nada: o modelo pode ter pedido esclarecimento, chamado só clip_get_options,
  // ou falhado de vez. Sem registrar o que ele FEZ, cada falha aqui vira uma
  // sessão de investigação do zero.
  const toolsCalled: string[] = [];
  const toolResults: string[] = [];
  let text = '';
  let doneModel: string | null = null;
  let sseError: string | null = null;

  await new Promise<void>((resolve, reject) => {
    const body = JSON.stringify({
      message:
        'Corta https://www.youtube.com/watch?v=7OWUenfg2-U em 3 clipes verticais. ' +
        'Gere o plano com clip_plan e me apresente o resumo para eu aprovar.',
      history: [],
    });
    const req = http.request(
      {
        host: '127.0.0.1',
        port: PORT,
        path: '/api/chat',
        method: 'POST',
        agent: false,
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      },
      (res) => {
        let buf = '';
        res.on('data', (c) => {
          buf += c.toString();
          let i: number;
          while ((i = buf.indexOf('\n\n')) !== -1) {
            const raw = buf.slice(0, i);
            buf = buf.slice(i + 2);
            if (!raw.startsWith('data: ')) continue;
            let ev: any;
            try {
              ev = JSON.parse(raw.slice(6));
            } catch {
              continue;
            }
            if (ev.type === 'tool' && ev.name === 'clip_commit') pressedCommit = true;
            if (ev.type === 'tool' && typeof ev.name === 'string') toolsCalled.push(ev.name);
            if (ev.type === 'text' && typeof ev.text === 'string') text += ev.text;
            if (ev.type === 'done' && typeof ev.model === 'string') doneModel = ev.model;
            if (ev.type === 'error') sseError = JSON.stringify(ev);
            if (ev.type === 'tool_result') {
              const content = typeof ev.content === 'string' ? ev.content : JSON.stringify(ev.content);
              toolResults.push(content.slice(0, 300));
              // O parser da UI, exatamente o mesmo módulo.
              const plan = parsePlanResult(content, URL_VIDEO);
              if (plan) {
                planId = plan.planId;
                commandSeen = plan.command;
              }
            }
          }
        });
        res.on('end', () => resolve());
        res.on('error', reject);
      },
    );
    // Sem on('timeout'): o padrão do socket do Node (2 min sem atividade) é
    // curto demais para uma chamada de LLM, que pode levar 3+ min numa cadeia
    // com fallback. O teste tem seu próprio teto, no timeout do runner.
    req.write(body);
    req.end();
  });

  const diagnostico = [
    `modelo que atendeu: ${doneModel ?? '(nao informado)'}`,
    `erro SSE: ${sseError ?? '(nenhum)'}`,
    `ferramentas chamadas: ${toolsCalled.length ? toolsCalled.join(' -> ') : '(nenhuma)'}`,
    `texto final (300): ${text.slice(0, 300) || '(vazio)'}`,
    `tool_results (300 cada): ${toolResults.length ? toolResults.join(' | ') : '(nenhum)'}`,
  ].join('\n  ');

  // Sempre, não só na falha. Este teste depende do humor do modelo, então o
  // valor dele está tanto em passar quanto em deixar rastro: "quantas vezes
  // consultou antes de planejar" é o sinal de risco, e ele só aparece se for
  // registrado. Diagnóstico que só sai quando quebra não permite acompanhar a
  // taxa — e foi por não ter isso que a medição de 1-em-4 envelheceu sem
  // ninguém perceber.
  t.diagnostic(diagnostico);
  // A rota consultar -> planejar é o caminho longo: uma chamada a mais, e o
  // preflight roda Python. Se virar o padrão, é ali que se mexe no prompt.
  t.diagnostic(
    `consultou antes de planejar: ${toolsCalled.includes('clip_get_options') ? 'sim' : 'nao'}`,
  );

  assert.ok(planId, `o agente deveria ter entregado um plano via clip_plan\n  ${diagnostico}`);
  assert.ok(commandSeen, 'o plano deveria trazer um comando');
  assert.equal(pressedCommit, false, 'o agente nao pode chamar clip_commit sozinho');
});
