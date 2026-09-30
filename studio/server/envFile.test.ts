/**
 * Testes de `envFile.ts`.
 *
 * O ponto que estes testes protegem e a REGRA DE PRECEDENCIA. Inverter a
 * ordem (arquivo vence o processo) nao quebra nada visivel: o app sobe, o
 * .env e lido, e a chave exportada no shell e ignorada em silencio. E o tipo
 * de bug que so aparece quando alguem tenta rodar com uma chave diferente para
 * um teste e nao entende por que nao muda nada.
 *
 * Uso: npx tsx --test server/envFile.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loadDotEnv, inspectDotEnv } from './envFile.js';

/** Cria um .env temporario e devolve o caminho. */
function tmpEnv(content: string): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'envfile-test-'));
  const file = path.join(dir, '.env');
  fs.writeFileSync(file, content, 'utf-8');
  return file;
}

/** Limpa as chaves que os testes usam, para nao vazar estado entre casos. */
function clearKeys(...keys: string[]): void {
  for (const k of keys) delete process.env[k];
}

test('le chave=valor simples', () => {
  const file = tmpEnv('MEU_TESTE_A=valor1\nMEU_TESTE_B=valor2\n');
  clearKeys('MEU_TESTE_A', 'MEU_TESTE_B');

  const applied = loadDotEnv(file);

  assert.equal(process.env.MEU_TESTE_A, 'valor1');
  assert.equal(process.env.MEU_TESTE_B, 'valor2');
  assert.deepEqual(applied.sort(), ['MEU_TESTE_A', 'MEU_TESTE_B']);

  clearKeys('MEU_TESTE_A', 'MEU_TESTE_B');
});

test('o ambiente do processo VENCE o arquivo', () => {
  // A regra central do modulo. Se este teste falhar, uma chave exportada no
  // shell passa a ser ignorada por causa do .env.
  const file = tmpEnv('MEU_TESTE_C=do_arquivo\n');
  process.env.MEU_TESTE_C = 'do_processo';

  const applied = loadDotEnv(file);

  assert.equal(process.env.MEU_TESTE_C, 'do_processo', 'o arquivo nao pode sobrescrever');
  assert.ok(!applied.includes('MEU_TESTE_C'), 'nao deve reportar como aplicada');

  clearKeys('MEU_TESTE_C');
});

test('ignora comentarios e linhas vazias', () => {
  const file = tmpEnv('# comentario\n\n  \nMEU_TESTE_D=ok\n# outro\n');
  clearKeys('MEU_TESTE_D');

  loadDotEnv(file);

  assert.equal(process.env.MEU_TESTE_D, 'ok');
  clearKeys('MEU_TESTE_D');
});

test('remove aspas envolventes', () => {
  const file = tmpEnv('MEU_TESTE_E="com espaco"\nMEU_TESTE_F=\'com espaco 2\'\n');
  clearKeys('MEU_TESTE_E', 'MEU_TESTE_F');

  loadDotEnv(file);

  assert.equal(process.env.MEU_TESTE_E, 'com espaco');
  assert.equal(process.env.MEU_TESTE_F, 'com espaco 2');

  clearKeys('MEU_TESTE_E', 'MEU_TESTE_F');
});

test('arquivo inexistente nao e erro', () => {
  // Rodar sem .env e cenario legitimo: todas as chaves podem vir do ambiente.
  const applied = loadDotEnv(path.join(os.tmpdir(), 'nao-existe-xyz-123.env'));
  assert.deepEqual(applied, []);
});

test('valor com "=" no meio nao e truncado', () => {
  // Chaves em base64 costumam terminar em "=". Um split ingenuo cortaria.
  const file = tmpEnv('MEU_TESTE_G=abc=def==\n');
  clearKeys('MEU_TESTE_G');

  loadDotEnv(file);

  assert.equal(process.env.MEU_TESTE_G, 'abc=def==');
  clearKeys('MEU_TESTE_G');
});

// ─── inspectDotEnv: diagnostico ──────────────────────────────────────────────
// Existe para separar "nao esta no arquivo" de "esta mas vazia". Os dois
// apareciam como "ausente" antes, e o usuario procurou a linha certa no lugar
// errado.

test('inspectDotEnv separa ausente, vazia e preenchida', () => {
  const file = tmpEnv('CHEIA=valor\nVAZIA=\n');
  clearKeys('CHEIA', 'VAZIA', 'NAO_EXISTE');

  const r = inspectDotEnv(file, ['CHEIA', 'VAZIA', 'NAO_EXISTE']);

  assert.deepEqual(r.filled, ['CHEIA']);
  assert.deepEqual(r.empty, ['VAZIA']);
  assert.deepEqual(r.missing, ['NAO_EXISTE']);
});

test('inspectDotEnv conta o ambiente do processo como preenchida', () => {
  // Consistente com a precedencia: se o processo tem a chave, ela vale, mesmo
  // que o arquivo a declare vazia.
  const file = tmpEnv('DO_AMBIENTE=\n');
  process.env.DO_AMBIENTE = 'do_shell';

  const r = inspectDotEnv(file, ['DO_AMBIENTE']);

  assert.deepEqual(r.filled, ['DO_AMBIENTE']);
  assert.deepEqual(r.empty, []);

  clearKeys('DO_AMBIENTE');
});

test('inspectDotEnv com arquivo inexistente marca tudo como ausente', () => {
  const r = inspectDotEnv(path.join(os.tmpdir(), 'nao-existe-xyz-456.env'), ['A', 'B']);
  assert.deepEqual(r.missing, ['A', 'B']);
  assert.deepEqual(r.empty, []);
  assert.deepEqual(r.filled, []);
});
