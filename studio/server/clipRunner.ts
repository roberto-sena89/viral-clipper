/**
 * Execução do CLI sob confirmação obrigatória.
 *
 * Dois modos, sempre nesta ordem:
 *   1. `plan`   — valida, monta o argv e devolve um resumo para o usuário
 *                 aprovar. NÃO executa nada.
 *   2. `commit` — só roda se o plano tiver sido aprovado antes, casando por id.
 *
 * O gate é do servidor, não do modelo: mesmo que o agente peça commit direto
 * (ignorando o protocolo), o plano precisa existir e estar aprovado. Assim a
 * confirmação do usuário não depende da boa vontade do LLM.
 *
 * Todo spawn passa por `spawnCli`, que fixa o cwd na raiz do repositório. Isso
 * não é preferência: o pacote `viralclipper` não está instalado, ele é resolvido
 * por sys.path[0] = cwd, então rodar de outro diretório falha com exit 1.
 */

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';

import {
  describeRequest,
  toArgv,
  validateRequest,
  ParamError,
  type ClipRequest,
} from './clipOptions.js';
import { resolvePython, resolveRoot } from './preflight.js';

/** Janela em que um plano continua válido antes de expirar. */
const PLAN_TTL_MS = 10 * 60 * 1000;
/** Teto de duração de uma execução (render de vários cortes é demorado). */
const RUN_TIMEOUT_MS = 6 * 60 * 60 * 1000;

export interface PlanRecord {
  id: string;
  request: ClipRequest;
  argv: string[];
  summary: string[];
  createdAt: number;
  approved: boolean;
  consumed: boolean;
}

export interface RunEvent {
  type: 'started' | 'stdout' | 'stderr' | 'exit' | 'error' | 'cancelled';
  runId?: string;
  line?: string;
  code?: number | null;
  message?: string;
  /** No evento `exit`: o fim veio de um cancelamento pedido pelo usuário. */
  cancelled?: boolean;
}

const plans = new Map<string, PlanRecord>();
const bus = new EventEmitter();
/** Assinaturas de SSE ativas, uma por espectador. */
const listeners = new Set<(event: RunEvent) => void>();

/**
 * Processos em execução, por runId.
 *
 * Sem este mapa não existia como parar um render: `cancelAll` só limpava os
 * planos, ou seja, cancelava o que ainda não tinha começado e deixava rodando
 * exatamente o que estava em andamento. Com RUN_TIMEOUT_MS de 6 horas, um
 * render indesejado só terminava sozinho.
 *
 * Também é o que permite o processo do servidor encerrar: um filho vivo segura
 * o event loop, e o `node --test` esperava por ele depois de o teste já ter
 * passado.
 *
 * Guarda o instante de início porque o mapa agora também serve de trava: a
 * mensagem de recusa precisa dizer há quanto tempo o run está rodando, senão o
 * usuário não sabe se espera ou se para.
 */
interface RunHandle {
  child: ChildProcessWithoutNullStreams;
  startedAt: number;
}

const running = new Map<string, RunHandle>();

/**
 * O run vivo, se houver. `undefined` quando nada está rodando.
 *
 * Um servidor Studio serve uma pessoa; `size` nunca passa de 1 depois da trava,
 * mas a função não depende disso — se um dia houver mais de um, ela devolve o
 * mais antigo, que é o que a mensagem de recusa precisa nomear.
 */
function runInFlight(): { runId: string; startedAt: number } | undefined {
  let maisAntigo: { runId: string; startedAt: number } | undefined;
  for (const [runId, handle] of running) {
    if (!maisAntigo || handle.startedAt < maisAntigo.startedAt) {
      maisAntigo = { runId, startedAt: handle.startedAt };
    }
  }
  return maisAntigo;
}

/** "há 3 min" — para a mensagem de recusa não obrigar o usuário a fazer contas. */
function haQuantoTempo(startedAt: number): string {
  const segundos = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
  if (segundos < 60) return `há ${segundos} s`;
  const minutos = Math.floor(segundos / 60);
  if (minutos < 60) return `há ${minutos} min`;
  return `há ${Math.floor(minutos / 60)} h ${minutos % 60} min`;
}


/**
 * Execuções cujo fim foi pedido pelo usuário.
 *
 * Existe para distinguir "cancelado" de "falhou". O processo morre com código
 * não-zero de qualquer jeito — sem esta marca, o painel mostraria "Falhou" em
 * vermelho para uma ação que o usuário pediu de propósito.
 */
const cancelRequested = new Set<string>();

function emit(event: RunEvent): void {
  for (const listener of listeners) {
    try {
      listener(event);
    } catch {
      // Um espectador morto não pode derrubar a execução.
    }
  }
  bus.emit('event', event);
}

export function subscribe(listener: (event: RunEvent) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function prunePlans(): void {
  const now = Date.now();
  for (const [id, plan] of plans) {
    if (now - plan.createdAt > PLAN_TTL_MS) plans.delete(id);
  }
}

/** Etapa 1: valida a intenção e devolve um plano pendente de aprovação. */
export function createPlan(raw: unknown): PlanRecord {
  prunePlans();
  const request = validateRequest(raw);
  const argv = toArgv(request);
  const summary = describeRequest(request);

  const plan: PlanRecord = {
    id: randomUUID().slice(0, 8),
    request,
    argv,
    summary,
    createdAt: Date.now(),
    approved: false,
    consumed: false,
  };
  plans.set(plan.id, plan);
  return plan;
}

/** Aprovação explícita do usuário, vinda da interface (nunca do agente). */
export function approvePlan(id: string): PlanRecord {
  const plan = plans.get(id);
  if (!plan) throw new ParamError(`Plano ${id} não existe ou expirou. Gere um novo.`);
  plan.approved = true;
  return plan;
}

export function getPlan(id: string): PlanRecord | undefined {
  return plans.get(id);
}

/** Comando completo, exibível ao usuário no card de confirmação. */
export function renderCommand(plan: PlanRecord): string {
  return `"${resolvePython(resolveRoot())}" ${plan.argv.join(' ')}`;
}

/**
 * Etapa 2: executa um plano aprovado. Recusa qualquer plano não aprovado,
 * expirado ou já consumido — o agente não tem como escapar disso.
 *
 * Recusa também quando JÁ existe um run vivo, e isso não é zelo: `work_path()`
 * do lado Python é `output/_work` para qualquer execução, sem id e sem trava.
 * Dois processos escrevem os mesmos `source_audio.webm` e `analysis.wav`, e o
 * sintoma não é erro — é resultado errado em silêncio. Medido, ao disparar dois
 * planos em paralelo: um run analisou 1127 s de um vídeo de 793,5 s, porque
 * reaproveitou o áudio que o outro processo tinha acabado de escrever. Passou
 * por todos os guardas seguintes e teria virado clipe publicado.
 *
 * A checagem vem ANTES de `consumed = true` de propósito: recusar não pode
 * queimar o plano. Assim o usuário espera o run atual terminar e commita o
 * mesmo plano depois, sem gerar outro.
 */
export function commitPlan(id: string): { runId: string } {
  const plan = plans.get(id);
  if (!plan) throw new ParamError(`Plano ${id} não existe ou expirou.`);
  if (!plan.approved) {
    throw new ParamError('Este plano ainda não foi aprovado pelo usuário.');
  }
  if (plan.consumed) {
    throw new ParamError('Este plano já foi executado. Gere um novo.');
  }

  const ativo = runInFlight();
  if (ativo) {
    throw new ParamError(
      `Já existe uma execução em andamento (run ${ativo.runId}, iniciada ` +
        `${haQuantoTempo(ativo.startedAt)}). Duas execuções ao mesmo tempo usam ` +
        'o mesmo diretório de trabalho (output/_work) e uma sobrescreve o áudio ' +
        'da outra — o resultado sai errado, sem erro nenhum para avisar. ' +
        'Espere esta terminar, ou pare com o botão Parar. Este plano continua ' +
        'aprovado: dá para executá-lo depois, sem gerar outro.',
      'em-andamento',
    );
  }

  plan.consumed = true;

  const runId = randomUUID().slice(0, 8);
  const root = resolveRoot();
  const python = resolvePython(root);

  emit({
    type: 'started',
    runId,
    message: `Executando em ${root}`,
  });

  let child: ChildProcessWithoutNullStreams;
  try {
    child = spawn(python, plan.argv, {
      // Obrigatório: o pacote é resolvido pelo cwd, não por instalação.
      cwd: root,
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    }) as ChildProcessWithoutNullStreams;
  } catch (error) {
    emit({ type: 'error', runId, message: `Falha ao iniciar o processo: ${String(error)}` });
    throw error;
  }

  const timeout = setTimeout(() => {
    emit({ type: 'stderr', runId, line: 'Tempo limite excedido; encerrando o processo.' });
    // Também em árvore: o timeout existe para liberar a máquina, e um ffmpeg
    // órfão continuaria ocupando CPU depois dele.
    killTree(child);
  }, RUN_TIMEOUT_MS);

  running.set(runId, { child, startedAt: Date.now() });

  // Stream linha a linha: o CLI emite progresso por stdout, e PYTHONUNBUFFERED
  // garante que a linha sai no momento em que acontece, não no fim do buffer.
  let pending = '';
  const pump = (data: Buffer, type: 'stdout' | 'stderr') => {
    pending += data.toString('utf8');
    const parts = pending.split(/\r?\n/);
    pending = parts.pop() ?? '';
    for (const line of parts) {
      if (line.trim()) emit({ type, runId, line });
    }
  };

  child.stdout.on('data', (data: Buffer) => pump(data, 'stdout'));
  child.stderr.on('data', (data: Buffer) => pump(data, 'stderr'));

  child.on('error', (error) => {
    clearTimeout(timeout);
    emit({ type: 'error', runId, message: error.message });
  });

  child.on('close', (code) => {
    clearTimeout(timeout);
    running.delete(runId);
    // `delete` devolve true se a marca estava lá — e some com ela, para que um
    // runId reutilizado não herde o cancelamento do anterior.
    const foiCancelado = cancelRequested.delete(runId);
    if (pending.trim()) emit({ type: 'stdout', runId, line: pending });
    emit({ type: 'exit', runId, code, cancelled: foiCancelado });
  });

  return { runId };
}

/**
 * Encerra um processo e, no Windows, toda a árvore dele.
 *
 * Por que não basta `child.kill()`: no Windows isso vira `TerminateProcess` no
 * processo direto, e os filhos sobrevivem. O pipeline chama ffmpeg via
 * `subprocess` (viralclipper/util.py, download.py), então parar o Python no
 * meio de um encode deixaria um ffmpeg órfão queimando CPU — ou seja, o botão
 * "Parar" não pararia justamente a parte cara, que é o motivo de ele existir.
 *
 * Medido nesta máquina, com o Python do venv no meio e um segundo Python como
 * neto (espelhando servidor -> viralcli → ffmpeg):
 *
 *   nada                    neto vivo   (controle: o neto é independente)
 *   child.kill('SIGTERM')   neto VIVO   <- órfão
 *   taskkill /F             neto VIVO   <- órfão
 *   taskkill /T /F          neto morto  <- o único que resolve
 *
 * ARMADILHA, porque custou uma medição errada: uma primeira sonda usou Node
 * como processo intermediário e mostrou que `child.kill()` JÁ matava o neto.
 * Era artefato — no Windows o libuv coloca os filhos num job object, então
 * filhos de Node morrem junto com o pai. Filhos de Python não. A sonda só
 * passou a valer quando o intermediário virou o Python do venv.
 *
 * `taskkill /T` percorre a árvore e `/F` força. Não há desligamento limpo em
 * nenhum dos caminhos (o Python não roda cleanup e arquivos temporários ficam
 * para trás), mas entre deixar o render rodando e deixar lixo em disco, quem
 * clicou em Parar escolheu o segundo.
 */
function killTree(child: ChildProcessWithoutNullStreams): void {
  if (process.platform === 'win32' && child.pid) {
    try {
      const killer = spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
      // Se o taskkill não existir, ainda dá para matar o processo direto.
      killer.on('error', () => {
        try {
          child.kill('SIGTERM');
        } catch {
          /* já morreu */
        }
      });
      return;
    } catch {
      /* cai no kill direto abaixo */
    }
  }
  child.kill('SIGTERM');
}

/**
 * Encerra um render em andamento. `false` se o runId não existe ou já terminou.
 *
 * Não espera o processo morrer: o evento `exit` do SSE é quem confirma, como
 * em qualquer outro fim de execução. Emitir `cancelled` antes de matar é
 * proposital — o painel precisa saber o motivo antes de o `exit` chegar.
 */
export function cancelRun(runId: string): boolean {
  const handle = running.get(runId);
  if (!handle) return false;
  cancelRequested.add(runId);
  emit({ type: 'cancelled', runId, message: 'Cancelamento pedido pelo usuário.' });
  killTree(handle.child);
  return true;
}

/**
 * Descarta os planos pendentes e encerra tudo que estiver rodando.
 *
 * A versão anterior só fazia `plans.clear()`, o que era o oposto do nome: os
 * planos são justamente o que NÃO está executando. Devolve quantos processos
 * foram encerrados, para que o chamador possa dizer algo além de "ok".
 *
 * Não remove do mapa `running` à mão: quem faz isso é o handler de `close`, que
 * é o único ponto que sabe que o processo realmente morreu.
 */
export function cancelAll(): number {
  plans.clear();
  let killed = 0;
  for (const runId of running.keys()) {
    if (cancelRun(runId)) killed++;
  }
  return killed;
}
