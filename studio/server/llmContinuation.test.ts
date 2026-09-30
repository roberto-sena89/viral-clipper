/**
 * A regra de continuidade do laço: turno vazio depois de ferramenta.
 *
 * ─── O defeito que isto cobre ────────────────────────────────────────────────
 *
 * Medido: em 1 de 10 execuções reais, o modelo chamava `clip_get_options` e
 * encerrava o turno **sem escrever nada**. O usuário pedia "corta em 3 clipes" e
 * recebia uma tela vazia — nem resposta, nem cartão de confirmação. Não havia
 * erro para ele ler: simplesmente nada acontecia.
 *
 * A tentação era corrigir só o prompt. O prompt foi ajustado (o convite à
 * consulta saiu), mas disciplina de prompt reduz a frequência — não elimina.
 * Como "turno vazio depois de ferramenta" nunca é um desfecho válido, dá para
 * tornar o caso impossível em vez de improvável. É o que `continuation` faz.
 *
 * ─── Por que este arquivo NÃO chama LLM ──────────────────────────────────────
 *
 * Um provedor falso local serve um roteiro fixo, então o teste mede o
 * COMPORTAMENTO DO LAÇO, não o humor do modelo. Um teste que dependesse do
 * provedor real não conseguiria reproduzir o defeito sob demanda: ele aparece
 * em 1 de cada 10 execuções, que é o pior lugar possível para se apoiar.
 *
 * A política testada é a REAL (`directorContinuation`, importada de
 * `chatRoute.ts`). Uma cópia da regra aqui provaria o mecanismo e deixaria sem
 * cobertura justamente onde o comportamento mora.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { chat, type LlmModel, type ToolDefinition } from './llm.ts';
import { directorContinuation, fallbackTextIfSilent } from './chatRoute.ts';

/** Uma resposta do provedor falso, no formato OpenAI. */
type Roteiro = { content?: string; tool_calls?: Array<{ name: string; args?: string }> };

/**
 * Provedor falso: serve o roteiro, uma resposta por chamada.
 *
 * Se o laço chamar mais vezes do que o roteiro tem, repete a última — assim um
 * laço descontrolado não trava o teste, e sim aparece em `chamadas.length`.
 */
async function provedorFalso(roteiro: Roteiro[]): Promise<{
  model: LlmModel;
  chamadas: any[];
  fechar: () => void;
}> {
  const chamadas: any[] = [];
  let i = 0;

  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      chamadas.push(JSON.parse(body));
      const passo = roteiro[Math.min(i++, roteiro.length - 1)];
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          choices: [
            {
              message: {
                content: passo.content ?? '',
                tool_calls: (passo.tool_calls ?? []).map((t, n) => ({
                  id: `call_${i}_${n}`,
                  type: 'function',
                  function: { name: t.name, arguments: t.args ?? '{}' },
                })),
              },
            },
          ],
        }),
      );
    });
  });

  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const port = (server.address() as { port: number }).port;

  return {
    model: { id: 'falso', url: `http://127.0.0.1:${port}/chat/completions`, apiKey: 'k' },
    chamadas,
    fechar: () => {
      server.closeAllConnections();
      server.close();
    },
  };
}

/** Ferramentas mínimas: o que importa é o NOME que o laço registra. */
const FERRAMENTAS: ToolDefinition[] = [
  {
    name: 'clip_get_options',
    description: 'cardápio',
    parameters: { type: 'object', properties: {} },
    handler: () => '{"parametros":[]}',
  },
  {
    name: 'clip_plan',
    description: 'gera o plano',
    parameters: { type: 'object', properties: { url: { type: 'string' } } },
    handler: () => '{"plano_id":"abc123"}',
  },
];

// ── 1. A política, isolada ───────────────────────────────────────────────────

test('a política só age no turno vazio depois de ferramenta', () => {
  const base = { turn: 2, continuations: 0, hadText: false, toolsCalled: ['clip_get_options'] };

  // O caso do defeito: agiu.
  assert.ok(
    directorContinuation(base),
    'turno vazio depois de consultar tem de gerar insistência',
  );

  // O modelo respondeu — pode ser pergunta de esclarecimento, que é o
  // comportamento PEDIDO pelo prompt quando falta o count. Não atropelar.
  assert.equal(
    directorContinuation({ ...base, hadText: true }),
    null,
    'se o modelo escreveu, não mexer',
  );

  // Conversa normal, sem ferramenta: "oi", "quanto custa".
  assert.equal(
    directorContinuation({ ...base, toolsCalled: [] }),
    null,
    'conversa sem ferramenta não pode ser empurrada para gerar plano',
  );

  // Já entregou o plano; o texto vazio aqui é só economia do modelo.
  assert.equal(
    directorContinuation({ ...base, toolsCalled: ['clip_get_options', 'clip_plan'] }),
    null,
    'plano já entregue não precisa de insistência',
  );

  // Uma tentativa só — senão vira laço com um modelo teimoso.
  assert.equal(
    directorContinuation({ ...base, continuations: 1 }),
    null,
    'não insistir duas vezes',
  );

  // Ferramenta sem consulta prévia (ex.: clip_status) também não pode ficar
  // sem resposta.
  assert.ok(
    directorContinuation({ ...base, toolsCalled: ['clip_status'] }),
    'qualquer turno vazio depois de ferramenta merece insistência',
  );
});

// ── 2. O laço: o defeito não acontece mais ───────────────────────────────────

test('turno vazio após clip_get_options: o laço insiste e o plano sai', async (t) => {
  const p = await provedorFalso([
    { tool_calls: [{ name: 'clip_get_options' }] },
    { content: '' }, // <── o defeito: consultou e ficou calado
    { tool_calls: [{ name: 'clip_plan', args: '{"url":"https://youtu.be/x"}' }] },
    { content: 'Plano pronto. Aprove?' },
  ]);
  t.after(() => p.fechar());

  const ferramentas: string[] = [];
  const r = await chat([p.model], [{ role: 'user', content: 'corta em 3 clipes' }], FERRAMENTAS, {
    continuation: directorContinuation,
    onEvent: (e) => {
      if (e.type === 'tool') ferramentas.push(e.name);
    },
  });

  assert.deepEqual(
    ferramentas,
    ['clip_get_options', 'clip_plan'],
    'o laço tinha de continuar até o plano sair, em vez de encerrar calado',
  );
  assert.match(r.text, /Aprove/, 'a resposta final tinha de chegar ao usuário');
  assert.ok(p.chamadas.length >= 4, `esperava >= 4 chamadas, veio ${p.chamadas.length}`);

  // A insistência tem de estar no histórico enviado ao modelo — é ela que faz
  // ele voltar atrás, não um efeito colateral do laço.
  const insistencia = p.chamadas[3].messages.find(
    (m: any) => m.role === 'user' && /encerrou o turno sem escrever nada/.test(m.content ?? ''),
  );
  assert.ok(insistencia, 'a mensagem de insistência deveria ter sido enviada ao modelo');
  assert.match(
    insistencia.content,
    /clip_plan/,
    'a insistência precisa dizer o que fazer, não só que algo deu errado',
  );
});

// ── 3. O controle: sem a regra, o defeito acontece ───────────────────────────

test('sem a política, o mesmo roteiro encerra calado (o defeito)', async (t) => {
  const p = await provedorFalso([
    { tool_calls: [{ name: 'clip_get_options' }] },
    { content: '' },
    { tool_calls: [{ name: 'clip_plan' }] },
  ]);
  t.after(() => p.fechar());

  const r = await chat([p.model], [{ role: 'user', content: 'corta em 3 clipes' }], FERRAMENTAS, {});

  // Sem `continuation` o laço encerra no turno vazio. Este é o comportamento
  // que existia em produção — e a razão de o teste 2 não passar por acidente:
  // se a opção fosse ignorada, este aqui e o de cima teriam o mesmo resultado.
  assert.equal(p.chamadas.length, 2, 'sem a regra, o laço para no turno vazio');
  assert.equal(r.text, '', 'o usuário recebe string vazia — nem resposta, nem plano');
});

// ── 5. A rede final: nunca entregar tela em branco ───────────────────────────

test('resposta vazia nunca chega ao usuário', () => {
  // Texto normal passa intacto.
  assert.equal(fallbackTextIfSilent('Plano pronto.', 2), 'Plano pronto.');

  // O caso do defeito, quando a insistência não bastou.
  const comFerramenta = fallbackTextIfSilent('', 2);
  assert.match(comFerramenta, /não consegui concluir/i);
  assert.ok(
    !/^\s*$/.test(comFerramenta),
    'silêncio total é o único desfecho sobre o qual o usuário não pode agir',
  );

  // Modelo mudo de vez, sem chamar ferramenta nenhuma.
  const semFerramenta = fallbackTextIfSilent('', 0);
  assert.match(semFerramenta, /Não recebi resposta/i);

  // Só espaço em branco conta como vazio — senão " " passaria como resposta.
  assert.equal(fallbackTextIfSilent('   \n ', 1), fallbackTextIfSilent('', 1));
});

// ── 6. Uma tentativa só ──────────────────────────────────────────────────────

test('modelo que fica calado duas vezes encerra o turno, sem laço', async (t) => {
  const p = await provedorFalso([
    { tool_calls: [{ name: 'clip_get_options' }] },
    { content: '' },
    { content: '' },
  ]);
  t.after(() => p.fechar());

  await chat([p.model], [{ role: 'user', content: 'corta' }], FERRAMENTAS, {
    continuation: directorContinuation,
  });

  // turno 1: ferramenta; turno 2: vazio -> insistência; turno 3: vazio -> desiste.
  assert.equal(
    p.chamadas.length,
    3,
    `esperava 3 chamadas (uma insistência só), veio ${p.chamadas.length}`,
  );
});
