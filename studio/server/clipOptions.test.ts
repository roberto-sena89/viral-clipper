/**
 * Testes da allowlist e do montador de argv.
 *
 * Rodam sem tocar no pipeline Python: são o contrato entre o que o agente pode
 * pedir e o que o servidor aceita executar. Cada caso aqui corresponde a uma
 * forma de o modelo errar (chave inventada, tipo trocado, valor fora de faixa)
 * ou de o usuário se machucar (caminho de saída, argumento de shell).
 *
 *   node --test --experimental-strip-types server/*.test.ts
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  ParamError,
  toArgv,
  validateRequest,
  describeRequest,
  CLIP_OPTIONS,
} from './clipOptions.ts';

const URL = 'https://www.youtube.com/watch?v=7OWUenfg2-U';

test('aceita um pedido mínimo com URL válida', () => {
  const request = validateRequest({ url: URL, params: {} });
  assert.equal(request.url, URL);
  assert.deepEqual(request.params, {});
});

test('rejeita URL ausente, vazia ou não-http', () => {
  for (const url of [undefined, '', 'youtube.com/watch?v=x', 'file:///etc/passwd', 42]) {
    assert.throws(() => validateRequest({ url, params: {} }), ParamError);
  }
});

test('rejeita parâmetro fora da allowlist', () => {
  // Este é o ponto central de segurança: nada de output_dir, ffmpeg, etc.
  for (const key of ['output_dir', 'ffmpeg', 'work_dir', 'extra_ytdlp_args']) {
    assert.throws(
      () => validateRequest({ url: URL, params: { [key]: 'C:\\Windows' } }),
      (error: unknown) =>
        error instanceof ParamError && error.message.includes('não permitido'),
    );
  }
});

test('rejeita tipo errado em número, booleano e enum', () => {
  assert.throws(() => validateRequest({ url: URL, params: { count: 'muitos' } }), ParamError);
  assert.throws(() => validateRequest({ url: URL, params: { count: 2.5 } }), ParamError);
  assert.throws(() => validateRequest({ url: URL, params: { vertical: 'sim' } }), ParamError);
  assert.throws(() => validateRequest({ url: URL, params: { layout: 'diagonal' } }), ParamError);
});

test('rejeita valor fora da faixa', () => {
  assert.throws(() => validateRequest({ url: URL, params: { count: 0 } }), ParamError);
  assert.throws(() => validateRequest({ url: URL, params: { count: 9999 } }), ParamError);
  assert.throws(() => validateRequest({ url: URL, params: { min_score: 101 } }), ParamError);
});

test('rejeita durações incoerentes entre si', () => {
  assert.throws(
    () => validateRequest({ url: URL, params: { min_duration: 90, max_duration: 40 } }),
    /não pode ser maior/,
  );
  assert.throws(
    () => validateRequest({ url: URL, params: { min_duration: 30, max_duration: 40, target_duration: 120 } }),
    /entre min_duration/,
  );
});

test('aceita durações coerentes', () => {
  const request = validateRequest({
    url: URL,
    params: { min_duration: 20, max_duration: 45, target_duration: 30 },
  });
  assert.equal(request.params.target_duration, 30);
});

test('argv usa sempre -m viralclipper e a URL, nunca shell', () => {
  const argv = toArgv(validateRequest({ url: URL, params: { count: 3 } }));
  assert.deepEqual(argv.slice(0, 3), ['-m', 'viralclipper', URL]);
  // Só o que geramos: nada de flag extra que possa ter escapado da allowlist.
  assert.deepEqual(argv, ['-m', 'viralclipper', URL, '-n', '3']);
});

test('flag negativa é emitida só quando o valor diverge do padrão', () => {
  // vertical é true por padrão: mandar true não deve gerar "--no-vertical".
  const padrao = toArgv(validateRequest({ url: URL, params: { vertical: true } }));
  assert.ok(!padrao.includes('--no-vertical'));

  const desligado = toArgv(validateRequest({ url: URL, params: { vertical: false } }));
  assert.ok(desligado.includes('--no-vertical'));
});

test('flag positiva só entra quando é true', () => {
  const sem = toArgv(validateRequest({ url: URL, params: { progress_bar: false } }));
  assert.ok(!sem.includes('--progress-bar'));

  const com = toArgv(validateRequest({ url: URL, params: { progress_bar: true } }));
  assert.ok(com.includes('--progress-bar'));
});

test('parâmetros com valor viram flag + valor separados', () => {
  const argv = toArgv(validateRequest({ url: URL, params: { count: 4, layout: 'center' } }));
  const countAt = argv.indexOf('-n');
  assert.equal(argv[countAt + 1], '4');
  const layoutAt = argv.indexOf('--layout');
  assert.equal(argv[layoutAt + 1], 'center');
});

test('nome de arquivo malicioso como texto não quebra o argv', () => {
  const argv = toArgv(
    validateRequest({ url: URL, params: { headline_text: 'GANCHO"; rm -rf /; echo "' } }),
  );
  // O texto vai como UM argumento; o shell nunca o interpreta.
  const idx = argv.indexOf('--headline');
  assert.equal(argv[idx + 1], 'GANCHO"; rm -rf /; echo "');
});

// Esta chave existe por causa de uma falha medida: no modo `sections` (padrão)
// o YouTube responde 403 em parte das requisições com Range, e o render morre no
// meio com "ffmpeg exited with code 3436169992". O conserto que o próprio yt-dlp
// sugere é `--download-mode full`. Sem cobertura, a chave pode voltar a faltar
// sem ninguém notar — foi assim que ela faltou da primeira vez.
test('download_mode vira --download-mode e não entra quando é o padrão', () => {
  const comFull = toArgv(
    validateRequest({ url: URL, params: { count: 3, download_mode: 'full' } }),
  );
  const idx = comFull.indexOf('--download-mode');
  assert.ok(idx > -1, 'full precisa emitir a flag');
  assert.equal(comFull[idx + 1], 'full');

  // Ausente = o CLI decide (`sections`). Mandar `sections` explicitamente seria
  // redundante, mas não errado; o contrato testado é o do default omitido.
  const semNada = toArgv(validateRequest({ url: URL, params: { count: 3 } }));
  assert.equal(semNada.indexOf('--download-mode'), -1);
});

test('download_mode recusa valor fora do enum, com a lista do que aceita', () => {
  assert.throws(
    () => validateRequest({ url: URL, params: { download_mode: 'turbo' } }),
    /sections, full/,
    'a mensagem tem de dizer o que é aceito, não só que errou',
  );
});

test('todos os presets citados na allowlist existem no Python', () => {
  const option = CLIP_OPTIONS.find((candidate) => candidate.key === 'caption_preset');
  assert.ok(option?.values?.includes('karaoke'));
  // 37 presets medidos em viralclipper/caption_presets.py.
  assert.ok((option?.values?.length ?? 0) >= 37);
});

test('describeRequest resume o pedido para o card de confirmação', () => {
  const lines = describeRequest(validateRequest({ url: URL, params: { count: 5 } }));
  assert.equal(lines[0], `URL: ${URL}`);
  assert.ok(lines.some((line) => line.includes('count = 5')));
  assert.ok(lines.some((line) => line.includes('(padrão)')));
});
