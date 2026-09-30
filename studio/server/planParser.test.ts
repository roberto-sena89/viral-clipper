/**
 * Testes do parser de plano.
 *
 * O risco que estes testes cobrem: o cartão de confirmação aparecer quando não
 * deveria. Se `parsePlanResult` aceitar um erro de validação como se fosse um
 * plano, o usuário vê um botão "Executar" para um `plan_id` que não existe no
 * servidor — clica e leva 404. Pior: um plano vazio aprovado sem o usuário
 * saber o que aprovou. Por isso o parser é estrito: sem `plano_id` E
 * `comando`, devolve null.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parsePlanResult, urlFromPlanInput, isDisplayableCommand } from '../src/utils/planParser.ts';

const PLAN_OK = JSON.stringify({
  plano_id: '5442c90f',
  comando:
    '"C:\\Users\\USUARIO\\viral-clipper\\.venv\\Scripts\\python.exe" -m viralclipper https://youtu.be/x --count 4',
  resumo: ['4 clipes verticais', 'legenda karaoke', '~45s cada'],
  requer_aprovacao: true,
  proximo_passo: 'Apresente este resumo ao usuário e peça a confirmação.',
});

test('extrai um plano válido', () => {
  const plan = parsePlanResult(PLAN_OK, 'https://youtu.be/x');
  assert.ok(plan, 'deveria reconhecer o plano');
  assert.equal(plan.planId, '5442c90f');
  assert.equal(plan.url, 'https://youtu.be/x');
  assert.equal(plan.summary.length, 3);
  assert.equal(plan.summary[0], '4 clipes verticais');
  // Sempre nasce não aprovado. A aprovação é evento do usuário, nunca default.
  assert.equal(plan.approved, false);
  assert.equal(plan.consumed, false);
});

test('recusa erro de validação do ParamError', () => {
  const erro = JSON.stringify({ erro: 'Parâmetro inválido: count deve ser inteiro entre 1 e 50' });
  assert.equal(parsePlanResult(erro), null);
});

test('recusa plano sem comando', () => {
  const semComando = JSON.stringify({ plano_id: 'abc123', resumo: ['algo'] });
  assert.equal(parsePlanResult(semComando), null, 'sem comando não há o que executar');
});

test('recusa plano sem plano_id', () => {
  const semId = JSON.stringify({ comando: '-m viralclipper https://youtu.be/x' });
  assert.equal(parsePlanResult(semId), null, 'sem id não há o que aprovar no servidor');
});

test('recusa content vazio ou lixo', () => {
  assert.equal(parsePlanResult(''), null);
  assert.equal(parsePlanResult('nao e json'), null);
  assert.equal(parsePlanResult('null'), null);
  assert.equal(parsePlanResult('[]'), null);
});

test('aceita resumo como string multilinha', () => {
  const plano = JSON.stringify({
    plano_id: 'ff00aa11',
    comando: 'x -m viralclipper https://youtu.be/y',
    resumo: '- 3 clipes verticais\n- legenda neon\n* duracao 30s',
  });
  const plan = parsePlanResult(plano);
  assert.ok(plan);
  // Marcadores de lista são removidos; linhas vazias, descartadas.
  assert.deepEqual(plan.summary, ['3 clipes verticais', 'legenda neon', 'duracao 30s']);
});

test('resumo ausente nao quebra o plano', () => {
  const plano = JSON.stringify({ plano_id: 'aabbccdd', comando: 'x -m viralclipper u' });
  const plan = parsePlanResult(plano);
  assert.ok(plan);
  assert.deepEqual(plan.summary, []);
});

test('remove a linha de URL do resumo (o card ja mostra a URL a parte)', () => {
  const plano = JSON.stringify({
    plano_id: 'aabbccdd',
    comando: 'x -m viralclipper u',
    resumo: ['URL: https://youtu.be/x', 'count = 4', 'layout = focus'],
  });
  const plan = parsePlanResult(plano);
  assert.ok(plan);
  assert.deepEqual(plan.summary, ['count = 4', 'layout = focus'], 'a URL nao deve aparecer duas vezes');
});

test('nao remove uma linha que apenas comeca com "url" minusculo sem dois-pontos', () => {
  const plano = JSON.stringify({
    plano_id: 'aabbccdd',
    comando: 'x -m viralclipper u',
    resumo: ['urlify = 3', 'URL do video = teste'],
  });
  const plan = parsePlanResult(plano);
  assert.ok(plan);
  // So a forma exata "URL:" e removida; o resto passa.
  assert.deepEqual(plan.summary, ['urlify = 3', 'URL do video = teste']);
});

test('urlFromPlanInput le a url dos argumentos', () => {
  assert.equal(urlFromPlanInput({ url: 'https://youtu.be/z' }), 'https://youtu.be/z');
  assert.equal(urlFromPlanInput({}), '');
  assert.equal(urlFromPlanInput(undefined), '');
  // Valor nao-string nao deve virar "[object Object]" na tela.
  assert.equal(urlFromPlanInput({ url: { a: 1 } as unknown as string }), '');
});

test('isDisplayableCommand separa comando real de texto solto', () => {
  assert.equal(isDisplayableCommand('-m viralclipper https://youtu.be/x'), false);
  assert.equal(
    isDisplayableCommand('"C:\\python.exe" -m viralclipper https://youtu.be/x'),
    true,
  );
  assert.equal(isDisplayableCommand(''), false);
});
