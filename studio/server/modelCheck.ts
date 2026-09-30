/**
 * Verificador dos modelos de terceiros declarados em `.codebuddy/models.json`.
 *
 * Por que isto existe: o SDK aceita qualquer URL em `models.json` sem validar
 * nada. Uma URL errada, uma chave ausente ou um provedor que nao implementa
 * tool-calling produzem o mesmo sintoma — o chat abre, o modelo aparece na
 * lista, e falha na primeira mensagem. Este script troca esse sintoma tardio
 * por um diagnostico explicito e anterior.
 *
 * O ponto nao-trivial e o teste de tool-calling. O Diretor de Cortes so
 * funciona se o modelo souber chamar funcao: sem isso ele conversa bonito e
 * nunca emite `clip_plan`. Um modelo pode responder "oi" perfeitamente e ainda
 * assim ser inutil para esta aplicacao. Por isso o teste nao para no 200 OK.
 *
 * Uso:
 *   npx tsx server/modelCheck.ts              # testa todos
 *   npx tsx server/modelCheck.ts openrouter   # so os que casam com o filtro
 *
 * Pre-requisito: as variaveis ${VAR} precisam estar no ambiente. O script le
 * o .env do studio, mas nao sobrescreve o que ja existe no processo.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { loadDotEnv, inspectDotEnv } from './envFile.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const STUDIO_ROOT = path.resolve(__dirname, '..');

// ─── Tipos ───────────────────────────────────────────────────────────────────
export interface ModelEntry {
  id: string;
  name?: string;
  vendor?: string;
  url?: string;
  apiKey?: string;
  maxInputTokens?: number;
  maxOutputTokens?: number;
  supportsToolCall?: boolean;
  supportsImages?: boolean;
  supportsReasoning?: boolean;
}

/**
 * O nome de modelo enviado a API e o campo `id`, sem transformacao.
 *
 * Isto nao e uma simplificacao: foi verificado no bundle do SDK que ele usa o
 * `id` literalmente como nome do modelo, e que nao existe campo separador
 * (grep por 'apiModel' retorna zero ocorrencias). Qualquer normalizacao aqui
 * — cortar prefixo de namespace, trocar barra por hifen — faria o validador
 * testar um nome diferente do que o SDK vai enviar. O validador passaria e a
 * aplicacao falharia, que e exatamente o problema que ele existe para evitar.
 *
 * Consequencia para quem edita o models.json: o `id` tem de ser copiado do
 * catalogo do provedor, caractere por caractere. A API da NVIDIA, por exemplo,
 * aceita 'nvidia/nemotron-3-super-120b-a12b' mas rejeita 'nemotron-3-super-...',
 * e aceita 'openai/gpt-oss-20b' mas rejeita 'nvidia/openai/gpt-oss-20b'.
 */
function toApiModelName(id: string): string {
  return id;
}

type Verdict = 'ok' | 'sem-tool-call' | 'falhou' | 'nao-testado';

export interface Result {
  id: string;
  name: string;
  verdict: Verdict;
  detail: string;
  latencyMs?: number;
  /** Aprovado, mas so na 2a amostra: funciona sem ser confiavel. */
  instavel?: boolean;
}

// ─── Carregamento do models.json ─────────────────────────────────────────────
/**
 * Le os modelos ativos do arquivo.
 *
 * `$disabled` desativa: e o marcador de "nao teste isto".
 * `$comment` NAO desativa: e anotacao para humano.
 *
 * A primeira versao tratava os dois igual, e isso apagou um modelo real do
 * teste: o Nemotron, que estava documentado com um `$comment` explicando sua
 * instabilidade. O validador passou a reportar 5 modelos em vez de 6, e o
 * unico modelo problematico era justamente o que sumiu da vigilancia. Um
 * comentario nao pode ter efeito funcional — se tem, ele mente sobre o que faz.
 */
function loadModels(file: string): ModelEntry[] {
  const raw = JSON.parse(fs.readFileSync(file, 'utf-8'));
  const list: unknown[] = Array.isArray(raw.models) ? raw.models : [];
  return list.filter((m): m is ModelEntry => {
    if (typeof m !== 'object' || m === null) return false;
    const entry = m as Record<string, unknown>;
    if (entry.$disabled) return false;
    return typeof entry.id === 'string' && entry.id.length > 0;
  });
}

function resolveEnvRefs(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  return value.replace(/\$\{([A-Z0-9_]+)\}/g, (_, name: string) => process.env[name] ?? '');
}

function missingVars(value: string | undefined): string[] {
  if (!value) return [];
  const found: string[] = [];
  for (const match of value.matchAll(/\$\{([A-Z0-9_]+)\}/g)) {
    if (!process.env[match[1]]) found.push(match[1]);
  }
  return found;
}

// ─── Teste de um modelo ──────────────────────────────────────────────────────
//
// O timeout de uma tentativa e o teto de tempo POR MODELO sao coisas
// diferentes, e confundir as duas foi um defeito real.
//
// Antes: TIMEOUT_MS = 60s, sem teto por modelo, e o abort era tratado como
// erro transitorio. Pior caso de UM modelo = 3 x 60s + backoff (1,5s + 3s) =
// ~185s. O validador ficava ~3 min parado no mesmo id, sem imprimir nada
// depois do "id ... ", e o sintoma era "o check:models estourou o timeout".
// Nao era o modelo: era a politica de repeticao multiplicando o timeout.
//
// O teto de 20s vem de medicao, nao de palpite: o ping com tool-call neste
// conjunto de provedores responde em 2s a 9s (o pior caso medido foi
// openai/gpt-oss-20b, 8,6s, que e modelo de raciocinio e varia muito). 20s da
// 2x de folga sobre o pior caso observado. Um modelo que nao devolve um
// tool-call simples em 20s tambem nao serve para um chat interativo.
// Os dois valores sao sobrescriviveis por variavel de ambiente para que o
// teste consiga verificar o comportamento de estouro sem esperar 45s reais.
// O default e o que vale em uso normal.
const TIMEOUT_MS = Number(process.env.MODEL_CHECK_TIMEOUT_MS) || 20_000;

// Teto de tempo por modelo, somando TODAS as tentativas e backoffs. E o que
// garante que um provedor que trava nao consuma a execucao inteira.
const MODEL_BUDGET_MS = Number(process.env.MODEL_CHECK_BUDGET_MS) || 45_000;

// Nao vale iniciar uma tentativa que nao cabe no orcamento restante: uma
// tentativa abortada no meio gastaria o resto do tempo sem chance de sucesso.
//
// Derivado do teto, e nao fixo em 5s. Um valor fixo maior que o teto (o que
// acontece quando o teto e encolhido por variavel de ambiente, como o teste
// faz) nao deixa tentativa nenhuma comecar: o laco sai na primeira volta com
// "0 tentativa(s)" e o resultado fica "nao executado", que nao diz nada sobre
// o modelo. O minimo tem de ser sempre menor que o orcamento.
const MIN_ATTEMPT_MS = Math.min(5_000, Math.floor(MODEL_BUDGET_MS / 3));

// Provedores gratuitos (NVIDIA NIM, em especial) devolvem 500 e 429 de forma
// intermitente sob carga. Uma tentativa unica transforma ruido de infraestrutura
// em "modelo quebrado" — e o aviso perde credibilidade. Vale insistir nesses
// codigos; nao vale insistir em 401/404, que sao erros de configuracao e vao
// se repetir identicos.
const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);
const MAX_ATTEMPTS = 3;

// 'sem-tool-call' NAO e erro de configuracao — e uma amostra ruim.
//
// Medicao que motivou isto: `openai/gpt-oss-20b` devolveu tool-call em 6 de 6
// amostras seguidas, mas numa rodada anterior do check:models devolveu HTTP
// 200, texto de raciocinio e ZERO tool_call. Nao e defeito do modelo nem da
// chave: e nao-determinismo. Julgar um modelo por uma unica amostra produz
// falso negativo — e o pior tipo de falso negativo aqui, porque manda o usuario
// caçar um problema de configuracao que nao existe.
//
// Por isso 'sem-tool-call' ganha uma segunda amostra (sem backoff: nao ha
// infraestrutura caida, so um sorteio ruim). Se a segunda vier com tool-call,
// o modelo passa — mas o resultado fica marcado como instavel, porque a
// instabilidade e informacao util para quem vai confiar o Diretor a ele.
const MAX_RESAMPLES = 2;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Formata uma duracao para o texto de diagnostico.
 *
 * `Math.round(ms / 1000) + 's'` transforma um teto de 400ms em "0s", e um
 * diagnostico que diz "timeout de 0s" parece defeito do validador em vez de
 * descricao do que aconteceu. Abaixo de 1s, mostra em ms.
 */
function fmtMs(ms: number): string {
  return ms >= 1000 ? `${Math.round(ms / 1000)}s` : `${ms}ms`;
}

/**
 * Como reagir a uma tentativa.
 *   'transient' — falha de infraestrutura (429/5xx/timeout). Repetir ajuda.
 *   'resample'  — resposta valida porem sem tool-call. Repetir sorteia de novo.
 *   false       — erro de configuracao. Repetir daria exatamente o mesmo.
 */
type RetryKind = 'transient' | 'resample' | false;

interface AttemptOutcome {
  verdict: Verdict;
  detail: string;
  retry: RetryKind;
}

async function attemptOnce(
  url: string,
  apiKey: string,
  body: Record<string, unknown>,
  wantsTools: boolean,
  timeoutMs: number,
): Promise<AttemptOutcome> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const text = await res.text();

    if (!res.ok) {
      let hint = text.slice(0, 200);
      try {
        const parsed = JSON.parse(text);
        hint = parsed?.error?.message || parsed?.detail || parsed?.message || hint;
      } catch {
        /* corpo nao-era JSON; usa o cru mesmo */
      }
      return {
        verdict: 'falhou',
        detail: `HTTP ${res.status} — ${hint}`,
        retry: RETRYABLE_STATUS.has(res.status) ? 'transient' : false,
      };
    }

    let parsed: any;
    try {
      parsed = JSON.parse(text);
    } catch {
      return { verdict: 'falhou', detail: 'resposta 200 mas corpo nao e JSON', retry: false };
    }

    const message = parsed?.choices?.[0]?.message;
    if (!message) {
      return { verdict: 'falhou', detail: 'resposta sem choices[0].message', retry: false };
    }

    if (!wantsTools) {
      return { verdict: 'ok', detail: 'respondeu (tool-call desativado)', retry: false };
    }

    const toolCalls = message.tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      const fn = toolCalls[0]?.function?.name;
      return { verdict: 'ok', detail: `tool-call OK (${fn})`, retry: false };
    }

    // O caso que motiva o teste inteiro: HTTP 200, texto plausivel, zero
    // tool-call. Para esta aplicacao e equivalente a falha — mas ainda nao
    // sabemos se e sempre assim, entao vale uma segunda amostra.
    return {
      verdict: 'sem-tool-call',
      detail: 'HTTP 200 mas nao emitiu tool_call — o Diretor nao vai conseguir planejar',
      retry: 'resample',
    };
  } catch (error: any) {
    const reason =
      error?.name === 'AbortError' ? `timeout de ${fmtMs(timeoutMs)}` : error?.message;
    // Timeout e erro de rede entram como transitorios, mas o orcamento por
    // modelo e o que impede que essa repeticao vire minutos de espera.
    return { verdict: 'falhou', detail: reason || String(error), retry: 'transient' };
  } finally {
    clearTimeout(timer);
  }
}

export async function checkModel(model: ModelEntry): Promise<Result> {
  const name = model.name || model.id;

  if (!model.url) {
    return { id: model.id, name, verdict: 'falhou', detail: 'sem campo url (obrigatorio)' };
  }
  if (!model.url.endsWith('/chat/completions')) {
    return {
      id: model.id,
      name,
      verdict: 'falhou',
      detail: `url nao termina em /chat/completions -> ${model.url}`,
    };
  }

  const missing = missingVars(model.apiKey).concat(missingVars(model.url));
  if (missing.length > 0) {
    // Diagnostico util: dizer ONDE a chave falta. "nao esta no .env" manda
    // editar o arquivo; ".env declarou mas vazio" manda preencher uma linha que
    // ja existe. Antes os dois casos apareciam como "ausente" e o usuario
    // procurava a linha certa no lugar errado.
    const states = inspectDotEnv(path.join(STUDIO_ROOT, '.env'), missing);
    const parts: string[] = [];
    if (states.empty.length > 0) parts.push(`declarada(s) vazia(s) no .env: ${states.empty.join(', ')}`);
    if (states.missing.length > 0) parts.push(`ausente(s) do .env: ${states.missing.join(', ')}`);
    return {
      id: model.id,
      name,
      verdict: 'nao-testado',
      detail: parts.join(' | ') || `variavel de ambiente ausente: ${missing.join(', ')}`,
    };
  }

  const apiKey = resolveEnvRefs(model.apiKey) || 'sk-none';
  const url = resolveEnvRefs(model.url)!;

  // Pedido minimo, mas nao trivial: pede explicitamente o uso de uma ferramenta.
  // Sem `tools` no corpo, nao ha como saber se o modelo sabe chamar funcao.
  const wantsTools = model.supportsToolCall !== false;
  const body: Record<string, unknown> = {
    model: toApiModelName(model.id),
    messages: [
      {
        role: 'user',
        content: 'Call the ping function exactly once with value 1. Do not write any text.',
      },
    ],
    max_tokens: 256,
    stream: false,
  };

  if (wantsTools) {
    body.tools = [
      {
        type: 'function',
        function: {
          name: 'ping',
          description: 'Echoes the value back. Call it whenever asked.',
          parameters: {
            type: 'object',
            properties: { value: { type: 'number', description: 'Value to echo' } },
            required: ['value'],
          },
        },
      },
    ];
    body.tool_choice = 'auto';
  }

  const started = Date.now();
  let outcome: AttemptOutcome = { verdict: 'falhou', detail: 'nao executado', retry: false };
  let transientAttempts = 0;
  let resamples = 0;
  let budgetExhausted = false;

  for (;;) {
    const budgetLeft = MODEL_BUDGET_MS - (Date.now() - started);
    if (budgetLeft < MIN_ATTEMPT_MS) {
      // Nao ha tempo util para outra tentativa. Sair aqui e o que impede o
      // "estouro do timeout": sem este corte, o laco insistiria por mais
      // MAX_ATTEMPTS x TIMEOUT_MS depois de o orcamento ja ter acabado.
      budgetExhausted = true;
      break;
    }

    const attemptTimeout = Math.min(TIMEOUT_MS, budgetLeft);
    if (transientAttempts + resamples > 0) {
      // Sem isto, um modelo lento fica com a linha "id ... " parada e parece
      // travado. Vai para stderr para nao poluir o resumo.
      process.stderr.write(
        `     [tentativa ${transientAttempts + resamples + 1}, ${fmtMs(Date.now() - started)} decorridos, teto ${fmtMs(attemptTimeout)}]\n`,
      );
    }

    const result = await attemptOnce(url, apiKey, body, wantsTools, attemptTimeout);
    outcome = result;

    if (result.verdict === 'ok') break;

    if (result.retry === 'transient') {
      transientAttempts++;
      if (transientAttempts >= MAX_ATTEMPTS) break;
      // Backoff curto: o objetivo e atravessar um blip, nao esperar o provedor.
      //
      // O sono tambem e limitado pelo orcamento. Sem isto, o backoff podia
      // empurrar o total para alem do teto: com 1,5s + 3s de espera, o corte
      // do orcamento so acontecia DEPOIS de ja ter dormido o tempo todo.
      const backoff = 1500 * transientAttempts;
      const remaining = MODEL_BUDGET_MS - (Date.now() - started);
      await sleep(Math.max(0, Math.min(backoff, remaining)));
      continue;
    }

    if (result.retry === 'resample') {
      resamples++;
      if (resamples >= MAX_RESAMPLES) break;
      // Sem backoff: nao ha infraestrutura caida, so um sorteio ruim.
      continue;
    }

    break; // erro de configuracao: repetir daria identico
  }

  // "Instavel" e diferente de "aprovado": o modelo funciona, mas so depois de
  // uma amostra ruim. Reportar os dois como iguais esconderia a informacao mais
  // util que o teste produziu.
  const instavel = outcome.verdict === 'ok' && resamples > 0;

  let detail = outcome.detail;
  if (budgetExhausted && outcome.verdict !== 'ok') {
    detail += ` (teto de ${fmtMs(MODEL_BUDGET_MS)} por modelo atingido apos ${transientAttempts + resamples} tentativa(s))`;
  } else if (outcome.verdict === 'falhou' && transientAttempts >= MAX_ATTEMPTS) {
    detail += ` (apos ${MAX_ATTEMPTS} tentativas — instabilidade do provedor)`;
  } else if (instavel) {
    detail += ' — 1a amostra sem tool_call, 2a passou (modelo instavel)';
  }

  return {
    id: model.id,
    name,
    verdict: outcome.verdict,
    detail,
    latencyMs: Date.now() - started,
    instavel,
  };
}

// ─── Main ────────────────────────────────────────────────────────────────────
const MARK: Record<Verdict, string> = {
  ok: '[ OK ]',
  'sem-tool-call': '[AVISO]',
  falhou: '[FALHA]',
  'nao-testado': '[ -- ]',
};

async function main(): Promise<void> {
  loadDotEnv(path.join(STUDIO_ROOT, '.env'));

  // Resolvido DEPOIS do loadDotEnv, de proposito. VIRCAL_ROOT costuma vir do
  // proprio .env; ler `process.env` no topo do modulo (antes do load) pegaria
  // `undefined` e cairia sempre no diretorio pai — que aqui coincide, mas
  // deixaria de coincidir se o Studio fosse movido para fora da raiz.
  const projectRoot = process.env.VIRCAL_ROOT || path.resolve(STUDIO_ROOT, '..');

  const modelsFile = path.join(projectRoot, '.codebuddy', 'models.json');
  if (!fs.existsSync(modelsFile)) {
    console.error(`models.json nao encontrado em ${modelsFile}`);
    process.exit(2);
  }

  const filter = process.argv[2];
  let models = loadModels(modelsFile);
  if (filter) models = models.filter((m) => m.id.includes(filter));

  if (models.length === 0) {
    console.log(filter ? `nenhum modelo casa com "${filter}"` : 'nenhum modelo declarado');
    process.exit(1);
  }

  console.log(`\nModels file : ${modelsFile}`);
  console.log(`Testando    : ${models.length} modelo(s)\n`);

  const results: Result[] = [];
  for (const model of models) {
    process.stdout.write(`  ${model.id} ... `);
    const result = await checkModel(model);
    results.push(result);
    const ms = result.latencyMs !== undefined ? ` (${result.latencyMs}ms)` : '';
    const flag = result.instavel ? ' (instavel)' : '';
    console.log(`${MARK[result.verdict]}${flag}${ms} ${result.detail}`);
  }

  const counts = results.reduce<Record<string, number>>((acc, r) => {
    acc[r.verdict] = (acc[r.verdict] || 0) + 1;
    return acc;
  }, {});

  console.log('\n─── resumo ───');
  for (const verdict of ['ok', 'sem-tool-call', 'falhou', 'nao-testado'] as Verdict[]) {
    if (counts[verdict]) console.log(`  ${MARK[verdict]} ${counts[verdict]}`);
  }

  const ok = counts['ok'] || 0;
  const skipped = counts['nao-testado'] || 0;
  const blocking = (counts['falhou'] || 0) + (counts['sem-tool-call'] || 0);

  // Instabilidade e um aviso, nao uma reprovacao: o modelo respondeu tool-call,
  // so nao na primeira amostra. Reprovar aqui seria trocar um falso negativo
  // por outro. Mas precisa aparecer — quem for confiar o Diretor a este modelo
  // tem direito de saber que ele falha de vez em quando.
  const unstable = results.filter((r) => r.instavel);
  if (unstable.length > 0) {
    console.log(`\n${unstable.length} modelo(s) so emitiram tool-call na 2a amostra:`);
    for (const r of unstable) console.log(`  - ${r.id}`);
    console.log('Servem, mas nao sao confiaveis: espere falhas intermitentes no chat.');
  }

  // O veredito final precisa refletir o que foi de fato verificado.
  //
  // A primeira versao imprimia "Todos prontos para uso" sempre que nao houvesse
  // falha — inclusive com 4 de 5 modelos pulados por falta de chave. Isso e
  // falso duas vezes: nao foram testados, e o "todos" inclui quem nunca rodou.
  // Uma mensagem de sucesso que nao distingue "aprovado" de "nao verificado"
  // treina o usuario a ignorar o aviso no dia em que ele importar.
  if (blocking > 0) {
    console.log('\nModelos com falha ou sem tool-call NAO servem para o Diretor de Cortes.');
    process.exit(1);
  }

  if (skipped > 0 && ok > 0) {
    console.log(`\n${ok} modelo(s) aprovado(s). ${skipped} NAO foram testados (falta chave) —`);
    console.log('nao presuma que funcionam: preencha o studio/.env e rode de novo.');
    // Sai com 0: pular por falta de chave nao e defeito do modelo. Mas o texto
    // deixa explicito o que ficou sem verificacao.
    process.exit(0);
  }

  if (skipped > 0 && ok === 0) {
    console.log('\nNenhum modelo foi testado — falta configurar as chaves.');
    console.log('Nada aqui autoriza dizer que algum modelo funciona.');
    process.exit(1);
  }

  console.log(`\nTodos os ${ok} modelos foram testados e estao prontos para uso.\n`);
}

// So executa quando chamado como script. O teste importa `checkModel` deste
// modulo; sem esta guarda, o `main()` rodaria durante o import e o teste
// bateria na rede de verdade — que e exatamente o que ele nao deve fazer.
const invokedDirectly =
  process.argv[1] !== undefined &&
  path.resolve(process.argv[1]).replace(/\.(ts|js)$/, '') ===
    __filename.replace(/\.(ts|js)$/, '');

if (invokedDirectly) {
  main().catch((error) => {
    console.error('erro inesperado:', error);
    process.exit(2);
  });
}
