/**
 * Testes do tradutor de código de saída.
 *
 * O risco que estes testes cobrem: o painel dizer "Processo finalizado com
 * código 4" e o usuário não saber que perdeu a legenda de todos os cortes. O
 * código é o único sinal que o painel recebe quando o run termina **sem erro
 * fatal** — por isso ele tem de vir traduzido.
 *
 * O teste que mais vale aqui não é o do mapa: é o que **lê o `cli.py` de
 * verdade** e exige que todo `return <n>` do pipeline tenha descrição do lado
 * da interface. Um mapa copiado à mão no teste passaria a mentir no dia em que
 * alguém adicionasse um código novo no Python — que é exatamente o que
 * aconteceu com o exit 4.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describeExitCode } from '../src/utils/exitCodes.ts';

const CLI_PATH = path.join(
  import.meta.dirname,
  '..',
  '..',
  'viralclipper',
  'cli.py',
);

/** Todo `return <inteiro>,` do CLI. É a lista real de códigos que ele emite. */
function exitCodesFromCli(): number[] {
  const source = readFileSync(CLI_PATH, 'utf8');
  const found = new Set<number>();
  for (const match of source.matchAll(/^\s*return (\d+),/gm)) {
    found.add(Number(match[1]));
  }
  return [...found].sort((a, b) => a - b);
}

test('a lista de códigos do CLI não está vazia', () => {
  // Guarda contra o teste virar vácuo: se o regex parar de casar (formatação
  // nova no cli.py), `exitCodesFromCli` devolveria [] e o teste abaixo passaria
  // sem verificar nada.
  const codes = exitCodesFromCli();
  assert.ok(codes.length >= 4, `esperava vários códigos, achei ${JSON.stringify(codes)}`);
  assert.ok(codes.includes(0), 'o caminho feliz tem de estar na lista');
});

test('todo código que o CLI emite tem descrição na interface', () => {
  for (const code of exitCodesFromCli()) {
    const texto = describeExitCode(code);
    assert.ok(texto, `código ${code} sem descrição`);
    assert.ok(
      !texto.startsWith('Processo finalizado com código'),
      `código ${code} caiu no texto genérico — falta traduzir em src/utils/exitCodes.ts`,
    );
  }
});

test('o exit 4 diz que a legenda foi perdida', () => {
  // O motivo de o 4 existir. Um texto que só dissesse "degradado" obrigaria o
  // usuário a subir o log para descobrir a consequência.
  const texto = describeExitCode(4) ?? '';
  assert.match(texto, /legenda/i, 'o texto do 4 tem de nomear a legenda');
});

test('o exit 5 diz que a pasta está ocupada e o que fazer', () => {
  // O 5 não é falha: nada quebrou, a pasta está em uso. Sem a instrução
  // ("espere e rode de novo") o usuário tentaria consertar o que não quebrou.
  const texto = describeExitCode(5) ?? '';
  assert.match(texto, /pasta/i, 'o texto do 5 tem de nomear a pasta');
  assert.match(texto, /de novo/i, 'o texto do 5 tem de dizer o que fazer');
});

test('código desconhecido não vira "undefined"', () => {
  assert.equal(describeExitCode(77), 'Processo finalizado com código 77.');
});

test('sem código devolve null, não uma frase', () => {
  // `null` é processo morto por sinal: não houve código para traduzir. Devolver
  // uma frase aqui inventaria um código que o sistema não deu.
  assert.equal(describeExitCode(null), null);
});

test('o 130 é interrupção, não falha', () => {
  assert.match(describeExitCode(130) ?? '', /interromp/i);
});
