/**
 * Ponte entre o Studio e o loop proprio de LLM (`llm.ts`).
 *
 * Substitui o `/api/chat` que usava o SDK do CodeBuddy. Mantem o mesmo contrato
 * SSE que o frontend ja consome — nenhuma linha do React precisa mudar.
 *
 * Divisao de responsabilidade, que e o ponto deste arquivo:
 *   - aqui: transformar HTTP/SSE em chamadas do loop, e as ferramentas do
 *     Diretor na forma que o `llm.ts` entende;
 *   - `llm.ts`: falar com os provedores e encadear tool-calls;
 *   - `clipRunner.ts`: decidir o que pode executar (o gate de aprovacao).
 *
 * O agente NAO ganha shell nem escrita. As 4 ferramentas abaixo sao tudo que
 * ele pode fazer, e `clip_commit` recusa plano nao aprovado por conta propria.
 */

import type { Express, Request, Response } from 'express';
import fs from 'node:fs';
import path from 'node:path';

import {
  chat,
  toLlmModel,
  LlmError,
  type ChatMessage,
  type ContinuationContext,
  type LlmModel,
  type ToolDefinition,
} from './llm.js';
import { optionsForAgent, ParamError } from './clipOptions.js';
import { createPlan, commitPlan, getPlan, renderCommand } from './clipRunner.js';
import { buildPlanPayload } from './planPayload.js';
import { runPreflight } from './preflight.js';
import { DIRECTOR_SYSTEM_PROMPT } from './directorPrompt.js';

/** Modelo padrao. 4/4 na sonda de protocolo, com 270k de contexto. */
export const DEFAULT_MODEL_ID = 'claude-sonnet-4-6';

/**
 * Ordem de fallback, medida na sonda de protocolo (2 cenarios x 2 execucoes):
 * os 5 primeiros fecharam 100%. Os dois NVIDIA ficam de fora da cadeia
 * principal — o Nemotron deu HTTP 500 espontaneo e o GPT-OSS deu timeout.
 * Continuam no models.json para uso manual, mas nao como rede de seguranca:
 * um fallback que falha 50% nao e fallback.
 */
const FALLBACK_ORDER = [
  'claude-sonnet-4-6',
  'gpt-6-luna',
  'deepseek-v4.1',
  'agnes-3.0-flash',
  'openrouter/free',
];

// ─── Ferramentas do Diretor ──────────────────────────────────────────────────

/**
 * Politica de continuidade do Diretor de Cortes.
 *
 * Defeito medido, 1 em 10 execucoes: o modelo chamava `clip_get_options` e
 * encerrava o turno **sem escrever nada**. O usuario pedia "corta em 3 clipes" e
 * recebia uma tela vazia — nem resposta, nem cartao de confirmacao. Nao havia
 * erro em lugar nenhum para ele ler; simplesmente nao acontecia nada.
 *
 * A condicao e deliberadamente estreita. Um turno vazio depois de consultar
 * ferramenta nunca e um desfecho valido, entao insistir aqui nao atropela
 * nenhum comportamento legitimo:
 *
 *   - `hadText` -> o modelo respondeu. Pode ser uma pergunta de esclarecimento
 *     ("quantos clipes?"), que e comportamento PEDIDO pelo prompt. Nao mexemos.
 *   - `toolsCalled` vazio -> conversa normal, sem ferramenta. Nao mexemos.
 *   - ja chamou `clip_plan` -> ja entregou o que tinha de entregar (o texto
 *     vazio aqui e so o modelo sendo economico). Nao mexemos.
 *   - `continuations >= 1` -> uma tentativa so. Se ele insistir em ficar
 *     calado, encerrar e melhor que virar laco.
 *
 * Exportada para o teste exercitar ESTA funcao, e nao uma copia da regra: uma
 * copia provaria o mecanismo do laco e deixaria a politica sem cobertura, que
 * e justamente onde o comportamento mora.
 */
export function directorContinuation({
  toolsCalled,
  continuations,
  hadText,
}: ContinuationContext): string | null {
  if (hadText) return null;
  if (continuations >= 1) return null;
  if (toolsCalled.length === 0) return null;
  if (toolsCalled.includes('clip_plan')) return null;

  if (toolsCalled.includes('clip_get_options')) {
    return (
      'Você encerrou o turno sem escrever nada para o usuário. ' +
      'Consultar o cardápio NÃO entrega o plano — o usuário ficou sem ' +
      'nada para aprovar. Chame clip_plan AGORA com os parâmetros que ' +
      'você já conhece, e depois apresente o resumo pedindo a ' +
      'confirmação.'
    );
  }

  return (
    'Você encerrou o turno sem escrever nada para o usuário. Ele não ' +
    'tem como saber o que aconteceu. Escreva a resposta agora — e, se ' +
    'o pedido era gerar cortes, chame clip_plan antes.'
  );
}

/**
 * Rede final: garante que o usuário receba alguma frase.
 *
 * `directorContinuation` insiste UMA vez quando o turno termina vazio. Isso
 * resolve o caso medido, mas não é garantia: um modelo teimosamente calado
 * encerraria mesmo assim, e a tela ficaria em branco — sem resposta, sem plano
 * e sem erro para ler. Silêncio é o único desfecho que não dá para o usuário
 * agir sobre.
 *
 * Por isso a checagem também existe no limite: aqui já não importa o motivo,
 * só que uma resposta vazia nunca chega à interface.
 *
 * Função pura de propósito — a alternativa seria testar isso por dentro do
 * handler HTTP, que exige a cadeia de modelos real.
 */
export function fallbackTextIfSilent(text: string, ferramentasChamadas: number): string {
  if (text.trim().length > 0) return text;

  if (ferramentasChamadas > 0) {
    return (
      'Chamei as ferramentas mas não consegui concluir o pedido — e não vou ' +
      'inventar um resultado. Pode repetir o que você quer que eu faça?'
    );
  }

  return 'Não recebi resposta do modelo desta vez. Tente enviar a mensagem de novo.';
}

/**
 * As 4 ferramentas, no formato que o `llm.ts` espera.
 *
 * Os corpos chamam exatamente o mesmo codigo que a versao do SDK chamava —
 * `createPlan`, `commitPlan`, `getPlan`, `runPreflight`. A validacao real
 * continua em `validateRequest` (clipOptions.ts), nao no schema: schema bonito
 * nao substitui allowlist.
 */
/**
 * Orientação devolvida ao agente quando o `clip_commit` é recusado.
 *
 * Duas recusas diferentes, duas reações diferentes. A trava de execução única
 * não é erro do payload: mandar reenviar o resumo para o usuário confirmar de
 * novo seria pedir uma confirmação que ele já deu, e o plano continua aprovado.
 *
 * Exportada para ser testada diretamente. A decisão mora aqui, não no handler —
 * um teste que reconstruísse a regra guardaria uma cópia, não a produção.
 */
export function orientacaoDoCommit(error: unknown): string {
  if (error instanceof ParamError && error.code === 'em-andamento') {
    return (
      'NÃO insista e NÃO gere outro plano. Já existe um render rodando. ' +
      'Diga ao usuário que este plano continua aprovado e que basta ' +
      'executar de novo depois que o atual terminar — ou parar o atual ' +
      'com o botão Parar.'
    );
  }
  return 'Apresente o resumo de novo e peça a confirmação. NÃO insista.';
}

export const directorTools: ToolDefinition[] = [
  {
    name: 'clip_get_options',
    description:
      'Consulta OPCIONAL: lista os parâmetros que o pipeline aceita (tipo, faixa, ' +
      'padrão, descrição) e o estado do ambiente (detecção de rosto, ffmpeg, GPU). ' +
      'Para gerar um plano, chame clip_plan DIRETO — ele valida e devolve o erro ' +
      'exato se uma chave estiver errada, então não é preciso consultar antes. ' +
      'Use isto apenas se o usuário pedir um parâmetro que você não conhece.',
    // Sem "required" — ver toolSpec() em llm.ts: array vazio quebra o tool-call.
    parameters: { type: 'object', properties: {} },
    handler: async () => {
      const report = await runPreflight();
      return JSON.stringify(
        {
          parametros: optionsForAgent(),
          ambiente: {
            pronto: report.ok,
            deteccao_de_rosto: report.capabilities.faceDetection,
            gpu: report.capabilities.gpu,
            ffmpeg: report.capabilities.ffmpeg,
            problemas: report.errors,
          },
        },
        null,
        2,
      );
    },
  },
  {
    name: 'clip_plan',
    description:
      'Valida os parâmetros e gera um plano de execução. NÃO executa nada: devolve ' +
      'o comando exato e um id de plano, para o usuário aprovar na interface. ' +
      'Sempre passe por aqui antes de clip_commit — o commit recusa qualquer ' +
      'plano que não tenha sido aprovado.',
    parameters: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'URL do vídeo longo (YouTube ou Instagram).' },
        params: {
          type: 'object',
          description:
            'Parâmetros do pipeline. Use apenas as chaves válidas; se alguma ' +
            'estiver errada, o erro devolve a lista completa e você corrige numa ' +
            'chamada só. Ex.: { "count": 4, "target_duration": 45 }.',
        },
      },
      required: ['url'],
    },
    handler: (args) => {
      try {
        const plan = createPlan({ url: args.url, params: args.params ?? {} });
        // O construtor devolve a string ja serializada — o mesmo texto que o
        // teste alimenta no parser da interface. Ver planPayload.ts.
        return buildPlanPayload(plan);
      } catch (error) {
        // ParamError tem mensagem escrita para o MODELO corrigir o JSON, nao
        // para desistir. Por isso o erro sobe como texto, nao como excecao.
        if (error instanceof ParamError) {
          return JSON.stringify({ erro: `Parâmetro inválido: ${error.message}` });
        }
        return JSON.stringify({ erro: `Falha ao gerar o plano: ${String(error)}` });
      }
    },
  },
  {
    name: 'clip_commit',
    description:
      'Executa um plano gerado por clip_plan e aprovado pelo usuário na interface. ' +
      'Se o plano não estiver aprovado, retorna erro — nesse caso NÃO insista: ' +
      'mostre o resumo de novo e peça a confirmação.',
    parameters: {
      type: 'object',
      properties: { plan_id: { type: 'string', description: 'Id devolvido por clip_plan.' } },
      required: ['plan_id'],
    },
    handler: (args) => {
      try {
        const { runId } = commitPlan(String(args.plan_id ?? ''));
        return JSON.stringify({
          executando: true,
          run_id: runId,
          mensagem:
            'Execução iniciada. O progresso aparece no painel de execução. ' +
            'Avise o usuário que o download e a transcrição podem demorar, ' +
            'sobretudo porque o Whisper roda em CPU.',
        });
      } catch (error) {
        return JSON.stringify({
          erro: error instanceof Error ? error.message : String(error),
          orientacao: orientacaoDoCommit(error),
        });
      }
    },
  },
  {
    name: 'clip_status',
    description:
      'Consulta o estado de um plano: se foi aprovado pelo usuário e se já foi ' +
      'executado. Use quando precisar saber se o usuário já confirmou.',
    parameters: {
      type: 'object',
      properties: { plan_id: { type: 'string' } },
      required: ['plan_id'],
    },
    handler: (args) => {
      const plan = getPlan(String(args.plan_id ?? ''));
      if (!plan) return JSON.stringify({ existe: false, motivo: 'Plano não encontrado ou expirado.' });
      return JSON.stringify(
        {
          existe: true,
          plano_id: plan.id,
          aprovado: plan.approved,
          executado: plan.consumed,
          comando: renderCommand(plan),
          resumo: plan.summary,
        },
        null,
        2,
      );
    },
  },
];

// ─── Carga dos modelos ───────────────────────────────────────────────────────

interface RawModels {
  models?: Array<Record<string, unknown>>;
}

/**
 * Le o models.json do projeto e monta a cadeia de fallback.
 *
 * Entradas sem chave sao descartadas em silencio por `toLlmModel` — declarar
 * um modelo sem credencial nao deve virar uma chamada que falha com 401
 * disfarcado de "modelo ruim".
 */
export function loadModelChain(projectRoot: string): LlmModel[] {
  const file = path.join(projectRoot, '.codebuddy', 'models.json');
  if (!fs.existsSync(file)) return [];

  let raw: RawModels;
  try {
    raw = JSON.parse(fs.readFileSync(file, 'utf-8'));
  } catch {
    return [];
  }

  const entries = Array.isArray(raw.models) ? raw.models : [];
  const resolved = new Map<string, LlmModel>();
  for (const entry of entries) {
    if (entry.$disabled) continue;
    const model = toLlmModel(entry);
    if (model) resolved.set(model.id, model);
  }

  // Cadeia na ordem medida; o que nao estiver na lista entra no fim, para nao
  // descartar um modelo que o usuario adicionou depois sem mexer aqui.
  const chain: LlmModel[] = [];
  for (const id of FALLBACK_ORDER) {
    const model = resolved.get(id);
    if (model) {
      chain.push(model);
      resolved.delete(id);
    }
  }
  chain.push(...resolved.values());

  return chain;
}

/** Lista para a UI, no contrato que `useModels.ts` ja consome. */
export function listModels(chain: LlmModel[]): {
  models: Array<{ modelId: string; name: string }>;
  defaultModel: string;
} {
  const models = chain.map((m) => ({ modelId: m.id, name: m.name || m.id }));
  const preferred = models.find((m) => m.modelId === DEFAULT_MODEL_ID);
  return { models, defaultModel: preferred?.modelId ?? models[0]?.modelId ?? DEFAULT_MODEL_ID };
}

// ─── Rota de chat ────────────────────────────────────────────────────────────

/** Historico enviado pelo cliente: so papel e texto, sem tool_calls internas. */
interface ClientHistoryItem {
  role: 'user' | 'assistant';
  content: string;
}

export function registerChatRoute(app: Express, deps: {
  projectRoot: () => string;
  saveUserMessage: (sessionId: string, content: string) => string;
  saveAssistantMessage: (sessionId: string, content: string, model: string, toolCalls: unknown[]) => void;
  ensureSession: (sessionId: string | undefined, firstMessage: string, model: string) => string;
}): void {
  app.post('/api/chat', async (req: Request, res: Response) => {
    const { sessionId, message, model, history } = req.body ?? {};

    if (typeof message !== 'string' || message.trim().length === 0) {
      res.status(400).json({ error: 'mensagem vazia' });
      return;
    }

    const projectRoot = deps.projectRoot();
    let chain = loadModelChain(projectRoot);

    if (chain.length === 0) {
      res.status(503).json({
        error:
          'Nenhum modelo utilizável. Preencha uma chave em studio/.env ' +
          '(VYCE_API_KEY, KILO_API_KEY ou NVIDIA_API_KEY) e rode npm run check:models.',
      });
      return;
    }

    // O modelo escolhido na UI vira o primeiro da cadeia; o resto e fallback.
    if (typeof model === 'string' && model.length > 0) {
      const chosen = chain.find((m) => m.id === model);
      if (chosen) chain = [chosen, ...chain.filter((m) => m.id !== model)];
    }

    const selectedModel = chain[0].id;
    const session = deps.ensureSession(sessionId, message, selectedModel);
    const userMessageId = deps.saveUserMessage(session, message);

    // SSE — mesmo contrato de antes.
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.flushHeaders?.();

    res.write(
      `data: ${JSON.stringify({
        type: 'init',
        sessionId: session,
        userMessageId,
        model: selectedModel,
      })}\n\n`,
    );

    // Monta o historico: system prompt do Diretor + o que o cliente mandou.
    // O cliente manda so texto (sem tool_calls), de proposito: reidratar
    // tool_calls antigos exigiria trafegar ids e argumentos que o modelo atual
    // nao reconhece. O texto das respostas anteriores ja da o contexto.
    const messages: ChatMessage[] = [{ role: 'system', content: DIRECTOR_SYSTEM_PROMPT }];

    if (Array.isArray(history)) {
      for (const item of history as ClientHistoryItem[]) {
        if (
          item &&
          (item.role === 'user' || item.role === 'assistant') &&
          typeof item.content === 'string' &&
          item.content.length > 0
        ) {
          messages.push({ role: item.role, content: item.content });
        }
      }
    }
    messages.push({ role: 'user', content: message });

    const toolCalls: Array<{
      id: string;
      name: string;
      input: Record<string, unknown>;
      status: string;
      result?: string;
      isError?: boolean;
    }> = [];

    try {
      const result = await chat(chain, messages, directorTools, {
        maxTurns: 8,
        // A regra fica fora daqui de proposito: assim o teste pode exercitar a
        // POLITICA real, e nao uma copia dela. Ver `directorContinuation`.
        continuation: directorContinuation,
        onEvent: (event) => {
          switch (event.type) {
            case 'text':
              res.write(`data: ${JSON.stringify({ type: 'text', content: event.content })}\n\n`);
              break;
            case 'model':
              // Informa a UI qual modelo esta atendendo — util quando o
              // fallback entra em acao no meio da conversa.
              res.write(`data: ${JSON.stringify({ type: 'model_active', model: event.id })}\n\n`);
              break;
            case 'retry':
              res.write(
                `data: ${JSON.stringify({
                  type: 'fallback',
                  model: event.model,
                  reason: event.reason,
                })}\n\n`,
              );
              break;
            case 'tool': {
              const call = { id: event.id, name: event.name, input: event.input, status: 'running' };
              toolCalls.push(call);
              res.write(
                `data: ${JSON.stringify({
                  type: 'tool',
                  id: call.id,
                  name: call.name,
                  input: call.input,
                  status: call.status,
                })}\n\n`,
              );
              break;
            }
            case 'tool_result': {
              const call = toolCalls.find((t) => t.id === event.toolId) ?? toolCalls[toolCalls.length - 1];
              if (call) {
                call.status = event.isError ? 'error' : 'completed';
                call.isError = event.isError;
                call.result = event.content;
              }
              res.write(
                `data: ${JSON.stringify({
                  type: 'tool_result',
                  toolId: event.toolId,
                  content: event.content,
                  isError: event.isError,
                })}\n\n`,
              );
              break;
            }
          }
        },
      });

      // Rede final: resposta vazia nunca chega à interface. Ver
      // `fallbackTextIfSilent` — o silêncio é o único desfecho sobre o qual o
      // usuário não tem como agir.
      const textoFinal = fallbackTextIfSilent(result.text, toolCalls.length);
      if (textoFinal !== result.text) {
        res.write(`data: ${JSON.stringify({ type: 'text', content: textoFinal })}\n\n`);
      }

      deps.saveAssistantMessage(session, textoFinal, selectedModel, toolCalls);
      res.write(`data: ${JSON.stringify({ type: 'done', model: selectedModel })}\n\n`);
      res.end();
    } catch (error) {
      const isLlm = error instanceof LlmError;
      const message_ = error instanceof Error ? error.message : String(error);
      console.error('[Chat] falha:', message_);
      res.write(
        `data: ${JSON.stringify({
          type: 'error',
          message: message_,
          hint: isLlm
            ? 'Todos os modelos da cadeia falharam. Rode npm run check:models para ver quais respondem.'
            : undefined,
        })}\n\n`,
      );
      res.end();
    }
  });
}
