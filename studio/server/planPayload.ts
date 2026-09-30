/**
 * O payload que o `clip_plan` devolve ao modelo — o contrato que a interface
 * consome.
 *
 * POR QUE ISTO E UM MODULO, E NAO TRES LINHAS DENTRO DO HANDLER
 *
 * `src/utils/planParser.ts` exige `plano_id` E `comando` para montar o cartao de
 * confirmacao. Se um dos dois for renomeado, o cartao some **sem aviso**: nao ha
 * erro, nao ha excecao, o usuario simplesmente para de ver o botao Executar.
 * E a pior categoria de falha, e a unica defesa e um teste.
 *
 * Mas um teste so defende se ele exercitar o caminho REAL. A versao anterior
 * deste modulo exportava o construtor e deixava o handler montar o objeto
 * inline — dois lugares com os mesmos nomes de campo. O teste passava pelo
 * construtor, a producao passava pelo inline, e os dois podiam divergir em
 * silencio: renomear `plano_id` no handler nao quebrava teste nenhum. O teste
 * guardava uma COPIA, e a copia nao e o que roda.
 *
 * Agora existe um construtor so, e ele devolve a STRING ja serializada. Nao ha
 * objeto intermediario para o handler remontar nem para o teste reconstruir:
 * a mesma chamada produz o texto que o modelo recebe e o texto que o parser
 * consome no teste. Divergir deixou de ser possivel, em vez de ficar sendo
 * vigiado.
 *
 * O `JSON.stringify(..., null, 2)` faz parte do contrato: o parser precisa
 * lidar com JSON multilinha, e e por isso que a serializacao mora aqui e nao no
 * chamador.
 */
import { renderCommand, type PlanRecord } from './clipRunner.js';

/**
 * Serializa o plano aprovavel no formato que o parser da interface espera.
 *
 * @returns JSON indentado, pronto para virar o `content` da tool. E string, e
 *          nao objeto, de proposito: e isso que impede o chamador de remontar
 *          o formato.
 */
export function buildPlanPayload(plan: PlanRecord): string {
  return JSON.stringify(
    {
      plano_id: plan.id,
      comando: renderCommand(plan),
      resumo: plan.summary,
      requer_aprovacao: true,
      proximo_passo:
        'Apresente este resumo ao usuário e peça a confirmação. Depois de ' +
        'aprovado na interface, chame clip_commit com este plano_id.',
    },
    null,
    2,
  );
}
