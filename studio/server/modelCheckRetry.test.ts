/**
 * Testes da POLITICA DE REPETICAO de `modelCheck.ts`, contra um servidor
 * local. Nada aqui toca a rede real: o que esta sob teste e a decisao de
 * repetir, nao o provedor.
 *
 * Por que esta politica merece teste: o defeito que ela causou nao era um
 * erro, era uma espera. Com TIMEOUT_MS de 60s, sem teto por modelo, e o
 * abort classificado como erro transitorio, UM modelo lento consumia
 * 3 x 60s + backoff = ~185s. O validador parecia travado e o sintoma foi
 * atribuido ao modelo (`openai/gpt-oss-20b estoura o timeout`), nao a esta
 * funcao. Um bug que so se manifesta como lentidao nao aparece em teste de
 * unidade nenhum — a nao ser que o teste cronometre.
 *
 * Uso: npx tsx --test server/modelCheckRetry.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import type { AddressInfo } from 'node:net';

// Encolhe os tetos ANTES de importar o modulo: as constantes sao lidas no
// topo do modulo, entao isto tem de vir primeiro.
//
// Os valores nao sao arbitrarios. O minimo para comecar uma tentativa e
// floor(teto/3) = 1000ms; com timeout de 400ms, cabem DUAS tentativas dentro
// do teto de 3000ms mesmo com o backoff de 1,5s no meio. Com um teto menor
// (2,5s, na primeira versao deste teste) o orcamento cortava depois da
// primeira tentativa e o teste nao exercitava a repeticao que queria testar.
process.env.MODEL_CHECK_TIMEOUT_MS = '400';
process.env.MODEL_CHECK_BUDGET_MS = '3000';

const { checkModel } = await import('./modelCheck.js');
process.env.TEST_MODEL_KEY = 'chave-de-teste';

type Handler = (req: http.IncomingMessage, res: http.ServerResponse) => void;

/** Sobe um servidor efemero e devolve a url completa + um contador de chamadas. */
async function serve(handler: Handler): Promise<{ url: string; calls: () => number; close: () => void }> {
  let calls = 0;
  const server = http.createServer((req, res) => {
    calls++;
    handler(req, res);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}/chat/completions`,
    calls: () => calls,
    close: () => server.close(),
  };
}

function entry(url: string): any {
  return {
    id: 'teste/modelo',
    name: 'Modelo de teste',
    url,
    apiKey: '${TEST_MODEL_KEY}',
    supportsToolCall: true,
  };
}

function jsonMessage(payload: unknown): string {
  return JSON.stringify({ choices: [{ message: payload }] });
}

// ─── O caso que motivou a correcao ───────────────────────────────────────────

test('modelo que trava: o tempo total fica dentro do teto por modelo', async () => {
  // Servidor que manda os headers e NUNCA fecha o corpo. Este e o pior caso:
  // o `fetch` resolve, quem fica pendurado e o `res.text()`, e cada tentativa
  // so termina pelo abort.
  const s = await serve((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.write('{"choices":[');
  });

  const t0 = Date.now();
  const r = await checkModel(entry(s.url));
  const elapsed = Date.now() - t0;
  s.close();

  assert.equal(r.verdict, 'falhou');
  assert.match(r.detail, /timeout de \d+(ms|s)/);

  // Teto de 3s + folga para o ultimo abort e a escrita da resposta.
  assert.ok(
    elapsed < 4000,
    `esperado < 4000ms com teto de 3000ms, medido ${elapsed}ms — a repeticao nao esta limitada pelo orcamento`,
  );
  // Prova de que houve mais de uma tentativa: se o teto nao estivesse
  // funcionando, o laco so pararia em MAX_ATTEMPTS.
  assert.ok(s.calls() >= 2, `esperado mais de uma tentativa, houve ${s.calls()}`);
});

test('o teto por modelo vence MAX_ATTEMPTS quando os dois competem', async () => {
  // Com timeout de 400ms e teto de 3s, 3 tentativas custariam 1,2s + backoff
  // 4,5s = 5,7s. O teto tem de cortar antes de o backoff terminar.
  const s = await serve((_req, res) => {
    res.writeHead(429, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'slow down' } }));
  });

  const t0 = Date.now();
  const r = await checkModel(entry(s.url));
  const elapsed = Date.now() - t0;
  s.close();

  assert.equal(r.verdict, 'falhou');
  assert.match(r.detail, /HTTP 429/);
  assert.ok(elapsed < 4000, `esperado < 4000ms, medido ${elapsed}ms`);
});

// ─── 'sem-tool-call' e amostra ruim, nao erro de configuracao ─────────────────

test('sem tool-call na 1a amostra: tenta de novo e aprova, marcando instavel', async () => {
  // Reproduz o que foi medido em openai/gpt-oss-20b: HTTP 200, resposta
  // valida, zero tool_call. Julgar por uma amostra so reprovava um modelo que
  // funciona.
  const s = await serve((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(jsonMessage({ role: 'assistant', content: 'ok' }));
  });

  const r = await checkModel(entry(s.url));
  s.close();

  assert.equal(r.verdict, 'sem-tool-call', 'duas amostras sem tool_call = reprovado');
  assert.ok(!r.instavel, 'reprovado nao e instavel');
  assert.equal(s.calls(), 2, `esperado exatamente 2 amostras, houve ${s.calls()}`);
});

test('tool-call na 1a amostra: aprovado, sem retry, sem marca de instavel', async () => {
  const s = await serve((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(jsonMessage({ role: 'assistant', tool_calls: [{ function: { name: 'ping' } }] }));
  });

  const r = await checkModel(entry(s.url));
  s.close();

  assert.equal(r.verdict, 'ok');
  assert.ok(!r.instavel, 'aprovado de primeira nao e instavel');
  assert.equal(s.calls(), 1, 'nao deve repetir quando ja deu certo');
});

test('tool-call na 2a amostra: aprovado E marcado como instavel', async () => {
  // A informacao mais util que o teste pode produzir: funciona, mas nao
  // confiavel. Esconder isto atras de um "OK" limpo seria perder o dado.
  let n = 0;
  const s = await serve((_req, res) => {
    n++;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      n === 1
        ? jsonMessage({ role: 'assistant', content: 'vou pensar...' })
        : jsonMessage({ role: 'assistant', tool_calls: [{ function: { name: 'ping' } }] }),
    );
  });

  const r = await checkModel(entry(s.url));
  s.close();

  assert.equal(r.verdict, 'ok');
  assert.equal(r.instavel, true);
  assert.match(r.detail, /instavel/);
  assert.equal(s.calls(), 2);
});

// ─── Erro de configuracao nao se repete ──────────────────────────────────────

test('HTTP 401 nao e repetido', async () => {
  // Repetir um 401 so gasta tempo: a chave continua a mesma. Uma tentativa.
  const s = await serve((_req, res) => {
    res.writeHead(401, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'invalid api key' } }));
  });

  const t0 = Date.now();
  const r = await checkModel(entry(s.url));
  const elapsed = Date.now() - t0;
  s.close();

  assert.equal(r.verdict, 'falhou');
  assert.equal(s.calls(), 1, `401 nao deve repetir, houve ${s.calls()} chamadas`);
  assert.ok(elapsed < 1500, `401 nao deveria esperar backoff, medido ${elapsed}ms`);
});

test('429 e repetido', async () => {
  const s = await serve((_req, res) => {
    res.writeHead(429, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'too many requests' } }));
  });

  await checkModel(entry(s.url));
  s.close();

  assert.ok(s.calls() > 1, `429 deveria ser repetido, houve ${s.calls()} chamada(s)`);
});

// ─── Guardas de configuracao, sem rede ───────────────────────────────────────

test('url sem /chat/completions falha antes de qualquer requisicao', async () => {
  const r = await checkModel({
    id: 'x/y',
    name: 'X',
    url: 'https://exemplo.invalido/v1',
    apiKey: '${TEST_MODEL_KEY}',
  } as any);
  assert.equal(r.verdict, 'falhou');
  assert.match(r.detail, /chat\/completions/);
});

test('variavel de ambiente ausente vira nao-testado, nao falha', async () => {
  const r = await checkModel({
    id: 'x/y',
    name: 'X',
    url: 'https://exemplo.invalido/v1/chat/completions',
    apiKey: '${VAR_QUE_NAO_EXISTE_EM_LUGAR_NENHUM}',
  } as any);
  assert.equal(r.verdict, 'nao-testado');
  assert.match(r.detail, /VAR_QUE_NAO_EXISTE_EM_LUGAR_NENHUM/);
});
