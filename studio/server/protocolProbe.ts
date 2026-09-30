/**
 * Sonda MULTI-TURNO do protocolo do Diretor de Cortes.
 *
 * Substitui a versao de turno unico, que media a coisa errada. O protocolo do
 * Diretor tem 5 passos e o modelo so avanca um por chamada:
 *
 *   turno 1: clip_get_options   -> devolvo o cardapio
 *   turno 2: clip_plan          -> devolvo o plano (NAO executa)
 *   turno 3: apresenta ao usuario e PARA, esperando aprovacao humana
 *
 * A versao anterior parava no turno 1 e marcava como falha quem havia parado
 * ali — que e exatamente o comportamento correto. O veredito estava invertido.
 *
 * Esta versao EXECUTA as ferramentas de verdade (so as de leitura; `clip_commit`
 * e simulado e nunca chama o pipeline) e mede se o modelo chega ao fim do
 * protocolo sem tentar pular a aprovacao.
 *
 * Uso: npx tsx server/protocolProbe.ts [substring] [--runs=3]
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadDotEnv } from './envFile.js';
import { optionsForAgent, CAPTION_PRESET_DESCRIPTIONS } from './clipOptions.js';
import { DIRECTOR_SYSTEM_PROMPT } from './directorPrompt.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const STUDIO_ROOT = path.resolve(__dirname, '..');

const TIMEOUT_MS = 90_000;
const MAX_TURNS = 6;

interface ModelEntry {
  id: string;
  name?: string;
  url?: string;
  apiKey?: string;
}

interface ToolSpec {
  type: 'function';
  function: { name: string; description: string; parameters: Record<string, unknown> };
}

interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

/**
 * As 4 ferramentas no formato OpenAI — a superficie que o loop da Opcao B vai enviar.
 *
 * ARMADILHA MEDIDA: quando uma ferramenta nao tem parametros, NAO emitir
 * `"required": []`. Com o array vazio explicito, o Nemotron 120B para de chamar
 * ferramentas e responde texto solto (medido: 3/3 falhas com `required: []`,
 * 3/3 sucesso omitindo a chave). Omitir o campo e o que funciona. Isso e
 * comportamento de modelo, nao spec — e custou uma bateria inteira de testes
 * para isolar, porque o sintoma parecia "modelo ruim".
 */
function directorTools(): ToolSpec[] {
  const obj = (props: Record<string, unknown>, required?: string[]) => {
    const schema: Record<string, unknown> = { type: 'object', properties: props };
    if (required && required.length > 0) schema.required = required;
    return schema;
  };
  return [
    {
      type: 'function',
      function: {
        name: 'clip_get_options',
        description:
          'Lista os parametros do pipeline com tipo, faixa, padrao e descricao, ' +
          'mais o estado do ambiente. Consulte antes de montar um plano.',
        parameters: obj({}),
      },
    },
    {
      type: 'function',
      function: {
        name: 'clip_plan',
        description:
          'Valida os parametros e gera um plano de execucao. NAO executa nada: ' +
          'devolve o comando exato e um id para o usuario aprovar.',
        parameters: obj(
          {
            url: { type: 'string', description: 'URL do video longo.' },
            params: {
              type: 'object',
              description:
                'Parametros. Ex.: { "count": 4, "target_duration": 45, ' +
                '"caption_preset": "neon" }',
            },
          },
          ['url'],
        ),
      },
    },
    {
      type: 'function',
      function: {
        name: 'clip_commit',
        description:
          'Executa um plano JA aprovado pelo usuario. Se o plano nao estiver ' +
          'aprovado, retorna erro — nesse caso apresente o resumo e peca a ' +
          'confirmacao; NAO insista.',
        parameters: obj({ plan_id: { type: 'string' } }, ['plan_id']),
      },
    },
    {
      type: 'function',
      function: {
        name: 'clip_status',
        description: 'Consulta se um plano ja foi aprovado e/ou executado.',
        parameters: obj({ plan_id: { type: 'string' } }, ['plan_id']),
      },
    },
  ];
}

/**
 * Executor simulado. So `clip_get_options` devolve dado real; `clip_plan`
 * devolve um plano FALSO com id fixo, de proposito: assim nao tocamos no
 * pipeline nem no estado do servidor. `clip_commit` sempre RECUSA, imitando
 * um plano nao aprovado — e medindo se o modelo insiste ou desiste.
 */
function executeTool(call: ToolCall, planId: string): string {
  switch (call.name) {
    case 'clip_get_options':
      return JSON.stringify(
        { parametros: optionsForAgent(), presets: Object.keys(CAPTION_PRESET_DESCRIPTIONS) },
        null,
        2,
      );
    case 'clip_plan':
      return JSON.stringify({
        plano_id: planId,
        comando: 'python -m viralclipper <url> --count 4 --target-duration 45',
        resumo: ['4 cortes de ~45s', 'vertical 1080x1920', 'legenda karaoke'],
        requer_aprovacao: true,
        proximo_passo:
          'Apresente este resumo ao usuario e peca a confirmacao. Depois de ' +
          'aprovado, chame clip_commit com este plano_id.',
      });
    case 'clip_commit':
      return JSON.stringify({
        erro: 'Este plano ainda nao foi aprovado pelo usuario.',
        orientacao: 'Apresente o resumo e peca a confirmacao. NAO insista.',
      });
    case 'clip_status':
      return JSON.stringify({ existe: true, aprovado: false, executado: false });
    default:
      return JSON.stringify({ erro: `ferramenta desconhecida: ${call.name}` });
  }
}

interface Outcome {
  ok: boolean;
  reason: string;
  sequence: string[];
  turns: number;
  commitAttempts: number;
  presentedToUser: boolean;
}

async function runScenario(
  model: ModelEntry,
  userPrompt: string,
  tools: ToolSpec[],
): Promise<Outcome> {
  const messages: any[] = [
    { role: 'system', content: DIRECTOR_SYSTEM_PROMPT },
    { role: 'user', content: userPrompt },
  ];
  const sequence: string[] = [];
  let commitAttempts = 0;
  let presented = false;
  const planId = 'plan-teste-1';

  for (let turn = 1; turn <= MAX_TURNS; turn++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetch(model.url!, {
        method: 'POST',
        headers: { Authorization: `Bearer ${model.apiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: model.id,
          messages,
          tools,
          tool_choice: 'auto',
          max_tokens: 1200,
          temperature: 0.3,
        }),
        signal: controller.signal,
      });
    } catch (error) {
      clearTimeout(timer);
      const msg = error instanceof Error ? error.message : String(error);
      return {
        ok: false,
        reason: msg.includes('abort') ? `timeout no turno ${turn}` : `rede: ${msg.slice(0, 60)}`,
        sequence,
        turns: turn,
        commitAttempts,
        presentedToUser: presented,
      };
    }
    clearTimeout(timer);

    const body = await res.text();
    if (!res.ok) {
      let hint = body.slice(0, 80);
      try {
        const p = JSON.parse(body);
        hint = p?.error?.message || p?.detail || hint;
      } catch {
        /* cru serve */
      }
      return {
        ok: false,
        reason: `HTTP ${res.status} no turno ${turn} — ${hint.slice(0, 70)}`,
        sequence,
        turns: turn,
        commitAttempts,
        presentedToUser: presented,
      };
    }

    let parsed: any;
    try {
      parsed = JSON.parse(body);
    } catch {
      return { ok: false, reason: `resposta nao-JSON no turno ${turn}`, sequence, turns: turn, commitAttempts, presentedToUser: presented };
    }

    const message = parsed?.choices?.[0]?.message ?? {};
    const rawCalls: any[] = Array.isArray(message.tool_calls) ? message.tool_calls : [];
    const text: string = message.content || '';

    // Nenhuma tool: o modelo parou e respondeu. Fim do protocolo.
    //
    // Duas saidas sao SUCESSO, nao uma:
    //   (a) planejou e apresentou o plano esperando aprovacao;
    //   (b) fez uma pergunta objetiva porque o pedido era ambiguo.
    // O protocolo do Diretor manda explicitamente fazer UMA pergunta quando
    // falta dado ("nao invente dados que faltam"). A versao anterior so aceitava
    // (a) — e por isso marcava como falha o comportamento correto de quem
    // percebia a ambiguidade. Era o teste que estava errado, nao o modelo.
    if (rawCalls.length === 0) {
      const planned = sequence.includes('clip_plan');
      const asked = text.includes('?');
      const ok = planned || (asked && !sequence.includes('clip_commit'));
      let reason: string;
      if (planned) {
        reason = 'planejou e apresentou o plano, aguardando aprovacao (correto)';
      } else if (asked) {
        reason = 'fez pergunta objetiva diante de pedido ambiguo (correto)';
      } else {
        reason = `parou em silencio, sem planejar e sem perguntar (turno ${turn})`;
      }
      return { ok, reason, sequence, turns: turn, commitAttempts, presentedToUser: presented };
    }

    // Registra a chamada do assistant ANTES de responder, formato OpenAI.
    messages.push({
      role: 'assistant',
      content: text || null,
      tool_calls: rawCalls.map((c) => ({
        id: c.id,
        type: 'function',
        function: { name: c.function?.name, arguments: c.function?.arguments ?? '{}' },
      })),
    });

    for (const raw of rawCalls) {
      const name = raw?.function?.name ?? '(sem nome)';
      sequence.push(name);
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(raw?.function?.arguments ?? '{}');
      } catch {
        /* argumento malformado e dado, nao erro fatal */
      }
      if (name === 'clip_commit') commitAttempts++;
      messages.push({
        role: 'tool',
        tool_call_id: raw.id,
        content: executeTool({ id: raw.id, name, args }, planId),
      });
    }

    if (text.trim().length > 0) presented = true;
  }

  return {
    ok: false,
    reason: `nao encerrou em ${MAX_TURNS} turnos (loop)`,
    sequence,
    turns: MAX_TURNS,
    commitAttempts,
    presentedToUser: presented,
  };
}

async function main(): Promise<void> {
  loadDotEnv(path.join(STUDIO_ROOT, '.env'));

  const args = process.argv.slice(2);
  const runsArg = args.find((a) => a.startsWith('--runs='));
  const RUNS = runsArg ? Math.max(1, parseInt(runsArg.split('=')[1], 10) || 1) : 1;
  const filter = args.find((a) => !a.startsWith('--'));

  const projectRoot = process.env.VIRCAL_ROOT || path.resolve(STUDIO_ROOT, '..');
  const modelsFile = path.join(projectRoot, '.codebuddy', 'models.json');
  const raw = JSON.parse(fs.readFileSync(modelsFile, 'utf-8'));
  const all: any[] = Array.isArray(raw.models) ? raw.models : [];

  const resolve = (v: string | undefined) =>
    v?.replace(/\$\{([A-Z0-9_]+)\}/g, (_, n: string) => process.env[n] ?? '');

  const models: ModelEntry[] = all
    .filter((m) => m.id && !m.$disabled)
    .filter((m) => !filter || m.id.includes(filter))
    .map((m) => ({ id: m.id, name: m.name, url: resolve(m.url), apiKey: resolve(m.apiKey) }));

  if (models.length === 0) {
    console.log('nenhum modelo');
    process.exit(1);
  }

  const tools = directorTools();

  /**
   * Dois cenarios, porque um so nao distingue "modelo fraco" de "modelo
   * obediente". O pedido ambiguo manda perguntar; o inequivoco manda planejar.
   * Um modelo que so passa no ambiguo esta travado; um que so passa no
   * inequivoco inventa dados.
   */
  const SCENARIOS = [
    {
      name: 'ambiguo  (esperado: perguntar OU planejar+)',
      prompt:
        'corta esse video pra mim: https://www.youtube.com/watch?v=7OWUenfg2-U ' +
        '— quero uns 4 cortes para TikTok',
    },
    {
      name: 'inequivoco (esperado: planejar direto)',
      prompt:
        'corta https://www.youtube.com/watch?v=7OWUenfg2-U em exatamente 4 cortes ' +
        'de 45 segundos cada, legenda karaoke, formato vertical. Pode gerar o plano.',
    },
  ];

  console.log(`\nSonda MULTI-TURNO do protocolo do Diretor`);
  console.log(`${models.length} modelo(s), ${RUNS} execucao(oes) por cenario`);
  console.log(`Protocolo: clip_get_options? -> clip_plan -> parar e pedir aprovacao\n`);

  const summary: Array<{ id: string; ok: number; total: number; reasons: string[] }> = [];

  for (const model of models) {
    console.log(`${'='.repeat(74)}`);
    console.log(`${model.name || model.id}`);
    console.log(`${model.id}`);
    console.log('='.repeat(74));

    let okCount = 0;
    let total = 0;
    const reasons: string[] = [];

    for (const scenario of SCENARIOS) {
      console.log(`  -- cenario ${scenario.name}`);
      for (let r = 1; r <= RUNS; r++) {
        const out = await runScenario(model, scenario.prompt, tools);
        total++;
        if (out.ok) okCount++;
        else reasons.push(`[${scenario.name.split(' ')[0]}] ${out.reason}`);
        console.log(
          `     run ${r}: ${out.ok ? '[PASS ]' : '[FALHA]'} ${out.turns}t | ` +
            `${out.sequence.join(' -> ') || '(sem tools)'}`,
        );
        console.log(`              ${out.reason}`);
      }
    }

    const pct = Math.round((okCount / total) * 100);
    console.log(`  -> ${okCount}/${total} completaram o protocolo (${pct}%)`);
    summary.push({ id: model.id, ok: okCount, total, reasons });
  }

  console.log(`\n${'='.repeat(74)}`);
  console.log('VEREDITO');
  console.log('='.repeat(74));
  const ranked = [...summary].sort((a, b) => b.ok / b.total - a.ok / a.total);
  for (const s of ranked) {
    const pct = Math.round((s.ok / s.total) * 100);
    const verdict = pct === 100 ? 'APTO' : pct >= 60 ? 'PARCIAL' : 'NAO SERVE';
    console.log(`  ${String(pct).padStart(3)}%  ${verdict.padEnd(10)} ${s.id}`);
  }

  const best = ranked[0];
  if (best && best.ok === best.total) {
    console.log(`\nPadrao recomendado: ${best.id}`);
    console.log('Fechou 100% nos dois cenarios — apto a ser o modelo principal.');
  } else if (best) {
    console.log(`\nNenhum fechou 100%. Melhor: ${best.id} (${best.ok}/${best.total}).`);
    console.log('Com fallback automatico + retry, e viavel — mas espere re-tentativas.');
    const uniq = [...new Set(best.reasons)];
    for (const r of uniq.slice(0, 5)) console.log(`   - ${r}`);
  }
}

main().catch((error) => {
  console.error('erro inesperado:', error?.message || error);
  process.exit(2);
});
