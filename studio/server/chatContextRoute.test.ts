/**
 * O caminho completo: banco -> contexto -> payload do provedor.
 *
 * ─── Por que este teste existe, se já há `chatContext.test.ts` ───────────────
 *
 * Os testes puros provam que `buildHistory` e `composeSystemPrompt` fazem a
 * coisa certa. Eles NÃO provam que alguém as chama. O defeito original era
 * exatamente isso: as funções não existiam, e o `chatRoute` lia o histórico de
 * um campo do corpo da requisição que o cliente nunca enviava. Uma função pura
 * correta e não usada tem o mesmo efeito de uma função errada.
 *
 * Então aqui a rota é exercitada de verdade, com um provedor OpenAI-compatible
 * **falso** em localhost. O que se inspeciona não é a resposta do modelo: é o
 * corpo HTTP que o servidor MANDOU para ele. É o único lugar onde dá para
 * verificar, sem adivinhação, que:
 *
 *   1. o histórico gravado entra no payload, na ordem;
 *   2. a mensagem de agora aparece UMA vez (o servidor grava antes de montar o
 *      contexto, então ela está no banco e seria duplicada sem o filtro);
 *   3. o `systemPrompt` que o usuário escreveu na interface chega ao modelo —
 *      antes, o servidor ignorava o campo em silêncio;
 *   4. o `history` do corpo é IGNORADO. É a prova negativa: se alguém voltar a
 *      ler o campo do cliente, o texto-isca abaixo aparece no payload e o teste
 *      falha. Sem essa isca, voltar ao defeito antigo passaria despercebido.
 *
 * Sem LLM real, sem rede externa, sem banco. Determinístico.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import express from 'express';

import { registerChatRoute } from './chatRoute.ts';
import { DIRECTOR_SYSTEM_PROMPT } from './directorPrompt.ts';

const PROVIDER_PORT = 3295;

/** Isca. Se isto aparecer no payload, o `history` do cliente voltou a ser lido. */
const TEXTO_ISCA = 'TEXTO-DO-CLIENTE-QUE-NAO-PODE-APARECER';

const INSTRUCAO_DO_USUARIO = 'Sempre 4 cortes de 45s com legenda neon.';

/**
 * Provedor OpenAI-compatible de mentira.
 *
 * Guarda cada corpo de requisição que recebe e responde um texto fixo, sem
 * tool_calls — assim o laço do agente termina no primeiro turno e nada do
 * pipeline é tocado (nenhum Python, nenhum render).
 */
function startFakeProvider(port: number): Promise<{ server: http.Server; captured: any[] }> {
  const captured: any[] = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      try {
        captured.push(JSON.parse(body));
      } catch {
        captured.push(null);
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          choices: [{ message: { role: 'assistant', content: 'Certo: 4 cortes.' } }],
        }),
      );
    });
  });
  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => resolve({ server, captured }));
  });
}

/** POST /api/chat consumindo o SSE até o fim. Devolve os eventos recebidos. */
function postChat(port: number, body: unknown): Promise<{ status: number; events: any[] }> {
  return new Promise((resolve) => {
    const payload = JSON.stringify(body);
    const events: any[] = [];
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path: '/api/chat',
        method: 'POST',
        // Sem pool: socket keep-alive pendurado segura o event loop no fim.
        agent: false,
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
        },
      },
      (res) => {
        let buf = '';
        res.setEncoding('utf8');
        res.on('data', (chunk: string) => {
          buf += chunk;
          let i: number;
          while ((i = buf.indexOf('\n\n')) !== -1) {
            const raw = buf.slice(0, i);
            buf = buf.slice(i + 2);
            if (!raw.startsWith('data: ')) continue;
            try {
              events.push(JSON.parse(raw.slice(6)));
            } catch {
              /* frame parcial */
            }
          }
        });
        res.on('end', () => resolve({ status: res.statusCode ?? 0, events }));
      },
    );
    req.on('error', () => resolve({ status: 0, events }));
    req.write(payload);
    req.end();
  });
}

test('a rota manda o histórico do banco e o systemPrompt do usuário ao modelo', async (t) => {
  const provider = await startFakeProvider(PROVIDER_PORT);
  t.after(() => provider.server.close());

  // Projeto de mentira: só o models.json importa. A chave é literal (sem
  // `${VAR}`) para o teste não depender de nenhuma variável de ambiente.
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-chatctx-'));
  fs.mkdirSync(path.join(root, '.codebuddy'), { recursive: true });
  fs.writeFileSync(
    path.join(root, '.codebuddy', 'models.json'),
    JSON.stringify({
      models: [
        {
          id: 'modelo-falso',
          name: 'Modelo Falso',
          url: `http://127.0.0.1:${PROVIDER_PORT}/v1/chat/completions`,
          apiKey: 'chave-falsa',
        },
      ],
    }),
  );
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  const prefs: Array<{ key: string; value: string }> = [{ key: 'count', value: '4' }];
  const gravadas: Array<{ role: string; content: string }> = [];

  const app = express();
  app.use(express.json());

  registerChatRoute(app, {
    projectRoot: () => root,
    ensureSession: (sessionId) => sessionId ?? 'sessao-teste',
    saveUserMessage: (_sessionId, content) => {
      gravadas.push({ role: 'user', content });
      return 'msg-atual';
    },
    saveAssistantMessage: (_sessionId, content) => {
      gravadas.push({ role: 'assistant', content });
    },
    // O que o BANCO devolveria: a conversa inteira, INCLUINDO a mensagem de
    // agora — porque o servidor a grava antes de montar o contexto. É essa
    // inclusão que torna o filtro por id necessário.
    getMessages: () => [
      { id: 'm1', role: 'user', content: 'Corta https://youtu.be/abc' },
      { id: 'm2', role: 'assistant', content: 'Quantos cortes você quer?' },
      { id: 'msg-atual', role: 'user', content: '4' },
    ],
    preferences: {
      list: () => prefs,
      remember: (key, value) => {
        prefs.push({ key, value });
      },
      forget: (key) => {
        const i = prefs.findIndex((p) => p.key === key);
        if (i < 0) return false;
        prefs.splice(i, 1);
        return true;
      },
    },
  });

  const server = app.listen(PROVIDER_PORT + 1, '127.0.0.1');
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  await new Promise((r) => setTimeout(r, 300));

  const resposta = await postChat(PROVIDER_PORT + 1, {
    sessionId: 'sessao-teste',
    message: '4',
    model: 'modelo-falso',
    systemPrompt: INSTRUCAO_DO_USUARIO,
    history: [{ role: 'user', content: TEXTO_ISCA }],
  });

  assert.equal(resposta.status, 200);
  const erro = resposta.events.find((e) => e.type === 'error');
  assert.equal(erro, undefined, `a rota devolveu erro: ${JSON.stringify(erro)}`);

  assert.equal(provider.captured.length, 1, 'o provedor deveria ter recebido 1 chamada');
  const payload = provider.captured[0];
  assert.ok(payload, 'o provedor recebeu um corpo que não é JSON');
  const messages = payload.messages as Array<{ role: string; content: string | null }>;

  // ── 1. O system prompt ──────────────────────────────────────────────────
  assert.equal(messages[0].role, 'system', 'a primeira mensagem é o system prompt');
  assert.ok(
    messages[0].content?.startsWith(DIRECTOR_SYSTEM_PROMPT),
    'o prompt do Diretor tem de estar no payload — o protocolo não é substituível',
  );
  assert.ok(
    messages[0].content?.includes(INSTRUCAO_DO_USUARIO),
    'o systemPrompt escrito na interface tem de chegar ao modelo (era ignorado)',
  );
  assert.ok(
    messages[0].content?.includes('- `count`: 4'),
    'as preferências registradas têm de chegar ao modelo',
  );

  // ── 2. O histórico ──────────────────────────────────────────────────────
  assert.deepEqual(
    messages.slice(1).map((m) => [m.role, m.content]),
    [
      ['user', 'Corta https://youtu.be/abc'],
      ['assistant', 'Quantos cortes você quer?'],
      ['user', '4'],
    ],
    'o histórico tem de vir do banco, em ordem, com a mensagem de agora no fim',
  );

  assert.equal(
    messages.filter((m) => m.content === '4').length,
    1,
    'a mensagem de agora não pode aparecer duas vezes (o servidor grava antes de montar)',
  );

  // ── 3. O campo do cliente é ignorado ────────────────────────────────────
  assert.ok(
    !messages.some((m) => (m.content ?? '').includes(TEXTO_ISCA)),
    'o `history` do corpo da requisição não pode ser lido: o contexto é do servidor',
  );

  // ── 4. As ferramentas, incluindo as de memória ──────────────────────────
  const nomes = (payload.tools as Array<{ function: { name: string } }>).map((t) => t.function.name);
  assert.ok(nomes.includes('clip_plan'), `faltou clip_plan em ${JSON.stringify(nomes)}`);
  assert.ok(
    nomes.includes('lembrar_preferencia'),
    `faltou a ferramenta de memória em ${JSON.stringify(nomes)}`,
  );
  assert.ok(nomes.includes('esquecer_preferencia'), 'faltou a ferramenta de esquecer');

  // ── 5. O turno termina com resposta salva ───────────────────────────────
  assert.ok(
    resposta.events.some((e) => e.type === 'done'),
    'o turno tem de fechar com done',
  );
  assert.equal(gravadas.length, 2, 'a mensagem do usuário e a do assistente são gravadas');
  assert.equal(gravadas[0].role, 'user');
  assert.equal(gravadas[1].role, 'assistant');
  assert.ok(gravadas[1].content.length > 0, 'a resposta não pode ser gravada vazia');
});

test('uma segunda mensagem carrega a primeira na frente', async (t) => {
  // O sintoma relatado: "responde '4' e o agente pergunta de novo". Aqui a
  // segunda rodada é montada com o banco JÁ contendo o turno anterior, e o
  // payload tem de mostrar a pergunta do agente e a resposta do usuário antes da
  // mensagem nova. É a diferença entre um agente que continua e um que reinicia.
  const provider = await startFakeProvider(PROVIDER_PORT + 2);
  t.after(() => provider.server.close());

  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-chatctx2-'));
  fs.mkdirSync(path.join(root, '.codebuddy'), { recursive: true });
  fs.writeFileSync(
    path.join(root, '.codebuddy', 'models.json'),
    JSON.stringify({
      models: [
        {
          id: 'modelo-falso',
          url: `http://127.0.0.1:${PROVIDER_PORT + 2}/v1/chat/completions`,
          apiKey: 'chave-falsa',
        },
      ],
    }),
  );
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  // Banco simulado com o turno 1 já gravado. `saveUserMessage` devolve o id da
  // mensagem do turno 2, que também está na lista — como em produção.
  const banco = [
    { id: 't1-u', role: 'user' as const, content: 'Corta https://youtu.be/abc' },
    { id: 't1-a', role: 'assistant' as const, content: 'Quantos cortes você quer?' },
    { id: 't2-u', role: 'user' as const, content: '4' },
  ];

  const app = express();
  app.use(express.json());
  registerChatRoute(app, {
    projectRoot: () => root,
    ensureSession: (sessionId) => sessionId ?? 'sessao-teste',
    saveUserMessage: () => 't2-u',
    saveAssistantMessage: () => {},
    getMessages: () => banco,
    preferences: { list: () => [], remember: () => {}, forget: () => false },
  });

  const server = app.listen(PROVIDER_PORT + 3, '127.0.0.1');
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  await new Promise((r) => setTimeout(r, 300));

  await postChat(PROVIDER_PORT + 3, {
    sessionId: 'sessao-teste',
    message: '4',
    model: 'modelo-falso',
  });

  const messages = provider.captured[0]?.messages as Array<{ role: string; content: string }>;
  assert.deepEqual(
    messages.slice(1).map((m) => m.content),
    ['Corta https://youtu.be/abc', 'Quantos cortes você quer?', '4'],
    'o turno anterior tem de estar no contexto do turno atual',
  );
});
