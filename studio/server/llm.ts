/**
 * Cliente LLM proprio, no lugar do SDK do CodeBuddy.
 *
 * Por que existe: o SDK exige CODEBUDDY_API_KEY e so fala com o gateway da
 * Tencent. Sem essa credencial o Studio nao funciona, por melhor que seja a
 * configuracao de modelos de terceiros. Este modulo implementa o mesmo laco de
 * agente — mensagens, tool_calls, resultados — falando OpenAI-format direto
 * com os provedores que o usuario ja tem (Vyce, NVIDIA, Kilo).
 *
 * O que este modulo NAO faz, de proposito:
 *   - nao executa nada. Ele so conversa e chama as ferramentas que recebe.
 *     Quem decide o que pode rodar e `clipRunner.ts`, que checa aprovacao.
 *   - nao le shell, nao escreve arquivo, nao acessa rede fora do provedor.
 *     Zero `eval`, zero `child_process`. A unica ferramenta perigosa possivel
 *     e o `clip_commit`, e ele recusa plano nao aprovado por conta propria.
 *
 * ATENCAO (armadilha medida, nao teorica): ao declarar uma ferramenta SEM
 * parametros, NAO emitir `"required": []`. Com o array vazio explicito o
 * Nemotron 120B abandona o tool-call e responde texto solto — medido 3/3 falhas
 * com `required: []` contra 3/3 sucesso omitindo a chave. Ver `toolSpec()`.
 */

/** Uma ferramenta que o modelo pode chamar. */
export interface ToolDefinition {
  name: string;
  description: string;
  /** JSON Schema dos parametros, ja no formato OpenAI. */
  parameters: Record<string, unknown>;
  /** Executor local. Devolve texto (normalmente JSON) para o modelo ler. */
  handler: (args: Record<string, unknown>) => Promise<string> | string;
}

/** Entrada de configuracao de um modelo, resolvida a partir do models.json. */
export interface LlmModel {
  id: string;
  name?: string;
  url: string;
  apiKey: string;
  maxOutputTokens?: number;
  temperature?: number;
}

export type ChatRole = 'system' | 'user' | 'assistant' | 'tool';

export interface ChatMessage {
  role: ChatRole;
  content: string | null;
  tool_calls?: Array<{
    id: string;
    type: 'function';
    function: { name: string; arguments: string };
  }>;
  tool_call_id?: string;
}

/** Eventos emitidos durante a conversa, para o servidor repassar por SSE. */
export type LlmEvent =
  | { type: 'text'; content: string }
  | { type: 'tool'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; toolId: string; content: string; isError: boolean }
  | { type: 'model'; id: string; attempt: number }
  | { type: 'retry'; attempt: number; model: string; reason: string };

/**
 * Erro de uma chamada HTTP. `retryable` separa falha do provedor (vale
 * insistir) de falha de configuracao (nao vale — repetir da o mesmo 401).
 */
export class LlmError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = 'LlmError';
  }
}

/**
 * Converte uma ToolDefinition no schema do OpenAI.
 *
 * O ponto delicado e o `required`: ferramenta sem parametros NAO deve ter
 * `required` — nem vazio. Ver o aviso no topo do arquivo. Por isso a chave so
 * entra quando ha campos de fato.
 */
export function toolSpec(def: ToolDefinition): Record<string, unknown> {
  const schema = { ...def.parameters } as Record<string, unknown>;
  const required = schema.required;

  if (Array.isArray(required) && required.length === 0) {
    delete schema.required;
  }

  return {
    type: 'function',
    function: { name: def.name, description: def.description, parameters: schema },
  };
}

const RETRYABLE_STATUS = new Set([408, 409, 425, 429, 500, 502, 503, 504]);
const DEFAULT_TIMEOUT_MS = 120_000;

/** Extrai a mensagem de erro do formato OpenAI, ou devolve o corpo cru. */
function describeHttpError(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body);
    const detail =
      parsed?.error?.message || parsed?.message || parsed?.detail || body.slice(0, 200);
    return `HTTP ${status} — ${String(detail).slice(0, 200)}`;
  } catch {
    return `HTTP ${status} — ${body.slice(0, 200)}`;
  }
}

interface SingleCallResult {
  text: string;
  toolCalls: Array<{ id: string; name: string; arguments: string }>;
}

/**
 * Uma chamada ao provedor. Sem retry aqui — quem decide tentar de novo e
 * `chatWithModel`, que pode trocar de modelo em vez de so repetir.
 */
async function callOnce(
  model: LlmModel,
  messages: ChatMessage[],
  tools: ToolDefinition[] | undefined,
  signal?: AbortSignal,
): Promise<SingleCallResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  const onAbort = () => controller.abort();
  signal?.addEventListener('abort', onAbort, { once: true });

  const body: Record<string, unknown> = {
    model: model.id,
    messages,
    temperature: model.temperature ?? 0.3,
    max_tokens: model.maxOutputTokens ?? 4096,
  };
  if (tools && tools.length > 0) {
    body.tools = tools.map(toolSpec);
    body.tool_choice = 'auto';
  }

  try {
    const res = await fetch(model.url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${model.apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const raw = await res.text();

    if (!res.ok) {
      throw new LlmError(
        describeHttpError(res.status, raw),
        res.status,
        RETRYABLE_STATUS.has(res.status),
      );
    }

    let parsed: any;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new LlmError('resposta nao e JSON valido', null, false);
    }

    const message = parsed?.choices?.[0]?.message ?? {};
    const rawCalls: any[] = Array.isArray(message.tool_calls) ? message.tool_calls : [];

    return {
      text: typeof message.content === 'string' ? message.content : '',
      toolCalls: rawCalls
        .map((c) => ({
          id: String(c?.id ?? ''),
          name: String(c?.function?.name ?? ''),
          arguments: String(c?.function?.arguments ?? '{}'),
        }))
        .filter((c) => c.name.length > 0),
    };
  } catch (error) {
    if (error instanceof LlmError) throw error;
    const isAbort = error instanceof Error && error.name === 'AbortError';
    throw new LlmError(
      isAbort ? 'timeout na chamada ao provedor' : `falha de rede: ${(error as Error).message}`,
      null,
      true,
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

/**
 * Contexto entregue a `continuation` quando o modelo encerra o turno sem pedir
 * ferramenta. Ver a opcao `continuation` em `chat()`.
 */
export interface ContinuationContext {
  /** Turno que acabou de terminar (1-based). */
  turn: number;
  /** Ferramentas ja chamadas nesta conversa, em ordem. */
  toolsCalled: string[];
  /** Quantas continuacoes ja foram injetadas. */
  continuations: number;
  /** O modelo escreveu algo para o usuario neste turno? */
  hadText: boolean;
}

/**
 * Conversa com um modelo, executando as ferramentas que ele pedir, ate ele
 * parar de pedir ou o limite de turnos acabar.
 *
 * O fallback e por CHAMADA, nao por conversa: se um modelo falha no turno 2,
 * o turno 2 e refeito em outro modelo com o mesmo historico. E o que faz
 * sentido aqui, porque a instabilidade medida e por chamada (o Nemotron deu
 * HTTP 500 espontaneo em 1 de 4 execucoes, nao em todas).
 *
 * `chain` inclui o proprio modelo na frente dos fallbacks.
 */
export async function chat(
  chain: LlmModel[],
  history: ChatMessage[],
  tools: ToolDefinition[],
  options: {
    maxTurns?: number;
    onEvent?: (event: LlmEvent) => void;
    signal?: AbortSignal;
    /**
     * Regra de continuidade. Chamada quando o modelo encerra o turno SEM pedir
     * ferramenta — ou seja, quando a conversa iria acabar. Se devolver texto,
     * ele entra como mensagem de `user` e o laco continua.
     *
     * Existe por causa de um defeito MEDIDO: em ~1 de 10 execucoes o modelo
     * chamava `clip_get_options` e encerrava o turno sem escrever NADA. O
     * usuario pedia "corta em 3 clipes" e recebia uma tela vazia — nem
     * resposta, nem plano. Isso nao e um caso de borda do prompt: um turno
     * vazio nunca e um desfecho valido.
     *
     * O mecanismo fica aqui (generico, nao conhece o Diretor de Cortes); a
     * POLITICA fica em quem chama, que e quem sabe o que a conversa devia
     * entregar. Ver `continuation` em `chatRoute.ts`.
     */
    continuation?: (context: ContinuationContext) => string | null;
  } = {},
): Promise<{ messages: ChatMessage[]; text: string }> {
  if (chain.length === 0) throw new LlmError('nenhum modelo configurado', null, false);

  const maxTurns = options.maxTurns ?? 8;
  const emit = options.onEvent ?? (() => {});
  const messages: ChatMessage[] = [...history];
  let fullText = '';
  const toolsCalled: string[] = [];
  let continuations = 0;

  for (let turn = 1; turn <= maxTurns; turn++) {
    let result: SingleCallResult | null = null;
    let lastError: LlmError | null = null;

    // Percorre a cadeia: modelo principal, depois os fallbacks.
    for (let i = 0; i < chain.length; i++) {
      const model = chain[i];
      try {
        if (i > 0) {
          emit({
            type: 'retry',
            attempt: i,
            model: model.id,
            reason: lastError?.message ?? 'falha desconhecida',
          });
        } else {
          emit({ type: 'model', id: model.id, attempt: 0 });
        }
        result = await callOnce(model, messages, tools, options.signal);
        break;
      } catch (error) {
        const llmError =
          error instanceof LlmError ? error : new LlmError(String(error), null, true);
        lastError = llmError;
        // 401/404 nao melhora trocando de modelo no mesmo provedor, mas
        // pode melhorar em outro provedor — por isso nao abortamos a cadeia.
        if (options.signal?.aborted) break;
      }
    }

    if (!result) {
      throw new LlmError(
        `todos os modelos da cadeia falharam: ${lastError?.message ?? 'erro desconhecido'}`,
        lastError?.status ?? null,
        false,
      );
    }

    // Sem tool_calls: o modelo parou de pedir ferramenta. Aqui a conversa
    // terminaria — a menos que a politica de continuidade diga que nao.
    if (result.toolCalls.length === 0) {
      if (result.text) {
        fullText += result.text;
        emit({ type: 'text', content: result.text });
      }
      messages.push({ role: 'assistant', content: result.text || null });

      if (options.continuation && turn < maxTurns) {
        const nudge = options.continuation({
          turn,
          toolsCalled: [...toolsCalled],
          continuations,
          hadText: result.text.trim().length > 0,
        });
        if (nudge) {
          continuations++;
          messages.push({ role: 'user', content: nudge });
          continue;
        }
      }

      return { messages, text: fullText };
    }

    // Registra a fala do assistant antes de responder as ferramentas.
    messages.push({
      role: 'assistant',
      content: result.text || null,
      tool_calls: result.toolCalls.map((c) => ({
        id: c.id,
        type: 'function' as const,
        function: { name: c.name, arguments: c.arguments },
      })),
    });

    if (result.text) {
      fullText += result.text;
      emit({ type: 'text', content: result.text });
    }

    // Executa cada ferramenta pedida e devolve o resultado.
    for (const call of result.toolCalls) {
      toolsCalled.push(call.name);

      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(call.arguments || '{}');
      } catch {
        // Argumento malformado e dado a reportar, nao excecao: o modelo
        // costuma se corrigir quando ve o erro.
        args = {};
      }

      emit({ type: 'tool', id: call.id, name: call.name, input: args });

      const def = tools.find((t) => t.name === call.name);
      let output: string;
      let isError = false;

      if (!def) {
        // Allowlist na pratica: nome fora da lista nao executa nada.
        isError = true;
        output = JSON.stringify({
          erro: `ferramenta desconhecida: ${call.name}`,
          disponiveis: tools.map((t) => t.name),
        });
      } else {
        try {
          output = await def.handler(args);
        } catch (error) {
          isError = true;
          output = JSON.stringify({
            erro: error instanceof Error ? error.message : String(error),
          });
        }
      }

      emit({ type: 'tool_result', toolId: call.id, content: output, isError });
      messages.push({ role: 'tool', tool_call_id: call.id, content: output });
    }
  }

  // Estourou o limite de turnos. Nao e erro fatal: devolvemos o que houve,
  // porque o texto parcial muitas vezes ja explica o estado ao usuario.
  return { messages, text: fullText };
}

/** Instante atual em epoch ms, para medir duracao sem depender de Date global. */
export const now = (): number => Date.now();

// ─── Selecao de modelos a partir do models.json ──────────────────────────────

interface RawModelEntry {
  id?: string;
  name?: string;
  url?: string;
  apiKey?: string;
  maxOutputTokens?: number;
  temperature?: number;
  $disabled?: boolean;
}

/**
 * Traduz uma entrada do models.json em LlmModel, resolvendo ${VAR}.
 * Devolve null quando falta chave ou URL — entrada incompleta nao deve virar
 * uma chamada que falha com 401 disfarcado.
 */
export function toLlmModel(raw: RawModelEntry): LlmModel | null {
  const resolve = (v?: string) =>
    v?.replace(/\$\{([A-Z0-9_]+)\}/g, (_, name: string) => process.env[name] ?? '');

  const url = resolve(raw.url);
  const apiKey = resolve(raw.apiKey);
  if (!raw.id || !url || !apiKey || apiKey.trim().length === 0) return null;

  return {
    id: raw.id,
    name: raw.name,
    url,
    apiKey,
    maxOutputTokens: raw.maxOutputTokens,
    temperature: raw.temperature,
  };
}
