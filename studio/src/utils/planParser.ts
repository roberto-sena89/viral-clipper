/**
 * Extrai o plano de execução do texto devolvido por `clip_plan`.
 *
 * Por que um parser e não um JSON.parse direto: o conteúdo do `tool_result` é
 * o retorno da ferramenta, que pode ser um objeto de erro
 * (`{ "erro": "Parâmetro inválido: ..." }`) em vez de um plano. Tratar os dois
 * casos aqui evita que a UI renderize um card de confirmação vazio — o pior
 * defeito possível num gate de aprovação, porque o usuário aprovaria "nada".
 *
 * Regra: só devolve plano quando existe `plano_id` E `comando`. Sem os dois,
 * não há o que aprovar e o card não deve aparecer.
 */

import type { ClipPlan } from '../types';

interface RawPlan {
  plano_id?: unknown;
  comando?: unknown;
  resumo?: unknown;
  requer_aprovacao?: unknown;
}

/** Normaliza `resumo`, que pode vir como array de strings ou string única. */
function toSummary(raw: unknown): string[] {
  const lines = Array.isArray(raw)
    ? raw.filter((x): x is string => typeof x === 'string' && x.length > 0)
    : typeof raw === 'string' && raw.length > 0
      ? raw
          .split('\n')
          .map((l) => l.replace(/^[-*•]\s*/, '').trim())
          .filter((l) => l.length > 0)
      : [];

  // O handler de `clip_plan` inclui a URL como primeiro item do resumo, e o
  // card ja mostra a URL num campo proprio logo acima. Remover aqui evita a
  // mesma informacao duas vezes na mesma caixa.
  return lines.filter((l) => !/^URL\s*:/i.test(l));
}

/**
 * Tenta montar um `ClipPlan` a partir do conteúdo de um `tool_result`.
 * Devolve `null` quando o conteúdo não é um plano válido.
 */
export function parsePlanResult(content: string, url?: string): ClipPlan | null {
  if (!content) return null;

  let parsed: RawPlan;
  try {
    parsed = JSON.parse(content);
  } catch {
    return null;
  }

  if (!parsed || typeof parsed !== 'object') return null;

  const planId = parsed.plano_id;
  const command = parsed.comando;

  // Os dois campos obrigatórios. Ausência de qualquer um = não é um plano.
  if (typeof planId !== 'string' || planId.length === 0) return null;
  if (typeof command !== 'string' || command.length === 0) return null;

  return {
    planId,
    url: url ?? '',
    command,
    summary: toSummary(parsed.resumo),
    approved: false,
    consumed: false,
  };
}

/**
 * Lê a URL dos argumentos de `clip_plan`, para exibir no card mesmo quando a
 * ferramenta falha e o resumo não volta.
 */
export function urlFromPlanInput(input?: Record<string, unknown>): string {
  const url = input?.url;
  return typeof url === 'string' ? url : '';
}

/**
 * Um comando é seguro de exibir? Serve para decidir se mostramos o botão de
 * copiar: não adianta oferecer "copiar" de um texto que não é um comando real.
 */
export function isDisplayableCommand(command: string): boolean {
  return command.trim().length > 0 && command.includes(' -m viralclipper');
}
