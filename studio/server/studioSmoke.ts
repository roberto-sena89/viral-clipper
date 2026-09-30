/**
 * Verificação de ponta a ponta do servidor do Studio, sem UI.
 *
 * Exercita o caminho real: preflight → plan → recusa de commit sem aprovação →
 * aprovação → (sem executar) → resumo do comando. É o teste que prova o gate de
 * confirmação, que é a decisão de segurança central deste app.
 *
 * Diferença para `studioRoutes.test.ts`: aqui tudo passa pela **API de módulo**
 * (`createPlan`, `approvePlan`, `commitPlan`). O `studioRoutes.test.ts` bate nas
 * rotas HTTP, que é o que a interface realmente usa. Os dois cobrem camadas
 * diferentes — não são redundantes.
 *
 *   npm run smoke
 */

import { runPreflight } from './preflight.ts';
import { createPlan, approvePlan, commitPlan, renderCommand, getPlan } from './clipRunner.ts';
import { validateRequest, toArgv, ParamError } from './clipOptions.ts';

const URL = 'https://www.youtube.com/watch?v=7OWUenfg2-U';
let failures = 0;

function check(label: string, condition: boolean, detail = ''): void {
  const mark = condition ? 'OK  ' : 'FALHA';
  if (!condition) failures += 1;
  console.log(`${mark} ${label}${detail ? ` — ${detail}` : ''}`);
}

console.log('\n=== 1. Preflight do ambiente ===\n');
const report = await runPreflight();
for (const c of report.checks) {
  const mark = c.ok ? 'OK  ' : c.fatal ? 'FATAL' : 'AVISO';
  console.log(`${mark} ${c.name}: ${c.detail}`);
}
check('preflight pronto', report.ok, report.errors.join(' | '));
check('raiz resolvida', report.root.includes('viral-clipper'), report.root);
check('python do venv', report.python.endsWith('python.exe'), report.python);

console.log('\n=== 2. Plano válido (não executa) ===\n');
const plan = createPlan({
  url: URL,
  params: { count: 4, target_duration: 45, min_duration: 35, max_duration: 55, caption_preset: 'neon' },
});
console.log('plano_id:', plan.id);
console.log('comando :', renderCommand(plan));
console.log('resumo  :', plan.summary.join(' | '));
check('argv começa com -m viralclipper', plan.argv[0] === '-m' && plan.argv[1] === 'viralclipper');
check('plano começa NÃO aprovado', plan.approved === false);

console.log('\n=== 3. Gate de confirmação ===\n');
let refused = false;
try {
  commitPlan(plan.id);
} catch (error) {
  refused = error instanceof ParamError && /não foi aprovado/.test(error.message);
}
check('commit sem aprovação é RECUSADO', refused);

let unknownRefused = false;
try {
  commitPlan('inexistente');
} catch {
  unknownRefused = true;
}
check('commit de plano inexistente é recusado', unknownRefused);

approvePlan(plan.id);
check('após aprovar, plano marcado como aprovado', getPlan(plan.id)?.approved === true);

console.log('\n=== 4. Validação rejeita o que deve rejeitar ===\n');
const rejections: Array<[string, unknown]> = [
  ['output_dir fora da allowlist', { url: URL, params: { output_dir: 'C:\\Windows' } }],
  ['ffmpeg fora da allowlist', { url: URL, params: { ffmpeg: 'calc.exe' } }],
  ['count não numérico', { url: URL, params: { count: 'muitos' } }],
  ['layout inválido', { url: URL, params: { layout: 'diagonal' } }],
  ['durações incoerentes', { url: URL, params: { min_duration: 90, max_duration: 40 } }],
  ['URL ausente', { params: {} }],
];
for (const [label, payload] of rejections) {
  let rejected = false;
  try {
    validateRequest(payload);
  } catch {
    rejected = true;
  }
  check(`rejeita ${label}`, rejected);
}

console.log('\n=== 5. argv sem superfície de shell ===\n');
const payload = 'x"; rm -rf /; echo "';
const malicious = toArgv(validateRequest({ url: URL, params: { headline_text: payload } }));

// O texto hostil DEVE sobreviver intacto como um único argumento. Isso é o que
// prova que não há shell: se houvesse interpolação, ele teria sido quebrado em
// vários pedaços ou o `;` teria sido consumido como separador de comando.
check('texto malicioso permanece um único argumento', malicious.includes(payload));
check(
  'nenhum argumento foi dividido pelo payload',
  malicious.filter((part) => part.includes('rm -rf')).length === 1,
);
// As flags que geramos são todas conhecidas e vêm da allowlist.
const knownFlags = new Set(['-m', 'viralclipper', URL, '-n', '4', '--headline', payload]);
check(
  'argv contém apenas o que geramos',
  malicious.every((part) => knownFlags.has(part)),
  malicious.filter((part) => !knownFlags.has(part)).join(' , '),
);

console.log(`\n${failures === 0 ? 'TUDO OK' : `${failures} FALHA(S)`}\n`);
process.exit(failures === 0 ? 0 : 1);
