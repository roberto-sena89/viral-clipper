/**
 * Aprovação, execução e log em tempo real do pipeline.
 *
 * Este hook é a metade "humana" do gate de confirmação: o `clipRunner.ts` no
 * servidor exige que o plano esteja aprovado, e é AQUI que a aprovação
 * acontece. O agente não tem rota para isso.
 *
 * Fluxo:
 *   1. a UI recebe um `ClipPlan` (extraído do `tool_result` de `clip_plan`);
 *   2. o usuário clica em Executar;
 *   3. `approve(planId)` marca o plano como aprovado no servidor;
 *   4. `POST /api/studio/commit/:planId` dispara o processo;
 *   5. o SSE `/api/studio/events` alimenta o painel de log.
 *
 * Detalhe que importa: o SSE é aberto no MOMENTO DO COMMIT, não no boot. Se
 * abrisse antes, o log acumularia execuções antigas e o usuário não saberia
 * qual delas está vendo.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { v4 as uuidv4 } from 'uuid';
import type { ClipPlan, RunLogLine, RunState } from '../types';
import { describeExitCode } from '../utils/exitCodes';

/** Teto de linhas guardadas. O CLI pode imprimir muito; o painel não precisa. */
const MAX_LINES = 2000;

const EMPTY_STATE: RunState = { runId: null, phase: 'idle', lines: [], exitCode: null };

/**
 * A fase que um código de saída significa.
 *
 * Um código de saída não é binário, e reduzir tudo a "falhou" esconde
 * justamente o que decide o próximo passo:
 *
 * - `0` — deu certo.
 * - `4` — produziu, mas degradado: há clipe no disco, sem legenda. Pintar de
 *   vermelho um run que entregou algo seria a mesma mentira que 'cancelled'
 *   evita.
 * - `5` — não produziu porque outra execução está usando a pasta. Nada
 *   quebrou; a ação é esperar e rodar de novo, não consertar.
 * - cancelado — o processo saiu com código não-zero, mas por pedido do usuário.
 */
function phaseFromExit(
  code: number | null,
  cancelled: boolean | undefined,
): RunState['phase'] {
  if (cancelled) return 'cancelled';
  if (code === 0) return 'done';
  if (code === 4) return 'degraded';
  if (code === 5) return 'ocupado';
  return 'failed';
}

export function useRunLog() {
  const [state, setState] = useState<RunState>(EMPTY_STATE);
  const sourceRef = useRef<EventSource | null>(null);

  // Fecha o SSE ao desmontar, senão o canal fica aberto apontando para um
  // componente que não existe mais.
  useEffect(() => {
    return () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  const pushLine = useCallback((line: Omit<RunLogLine, 'id' | 'timestamp'>) => {
    setState((prev) => {
      const next = [...prev.lines, { ...line, id: uuidv4(), timestamp: new Date() }];
      return {
        ...prev,
        lines: next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next,
      };
    });
  }, []);

  /** Abre o canal de eventos e traduz cada `RunEvent` numa linha do painel. */
  const openStream = useCallback(() => {
    sourceRef.current?.close();
    const source = new EventSource('/api/studio/events');
    sourceRef.current = source;

    source.onmessage = (event) => {
      let data: {
        type: string;
        runId?: string;
        line?: string;
        code?: number | null;
        message?: string;
        cancelled?: boolean;
      };
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      switch (data.type) {
        case 'hello':
          // Handshake do servidor. Não é uma linha do processo.
          break;
        case 'started':
          setState((prev) => ({ ...prev, runId: data.runId ?? null, phase: 'running' }));
          pushLine({ type: 'started', message: data.message, line: data.message });
          break;
        case 'stdout':
          pushLine({ type: 'stdout', line: data.line });
          break;
        case 'stderr':
          pushLine({ type: 'stderr', line: data.line });
          break;
        case 'cancelled':
          // Só registra. Quem decide a fase é o `exit`, que chega logo depois —
          // assim não existe uma janela em que o painel diz "cancelado" com o
          // processo ainda vivo.
          pushLine({ type: 'cancelled', line: data.message ?? 'Cancelamento pedido.' });
          break;
        case 'exit':
          setState((prev) => ({
            ...prev,
            phase: phaseFromExit(data.code ?? null, data.cancelled),
            exitCode: data.code ?? null,
          }));
          pushLine({
            type: 'exit',
            code: data.code ?? null,
            line: data.cancelled
              ? 'Cancelado pelo usuário.'
              : (describeExitCode(data.code ?? null) ?? 'Encerrado.'),
          });
          // Execução terminou: não faz sentido manter o canal aberto.
          source.close();
          sourceRef.current = null;
          break;
        case 'error':
          setState((prev) => ({ ...prev, phase: 'failed' }));
          pushLine({ type: 'error', message: data.message, line: data.message });
          break;
      }
    };

    source.onerror = () => {
      // O EventSource reconecta sozinho; se o servidor caiu de vez, avisamos
      // uma vez e fechamos para não ficar tentando em loop.
      if (source.readyState === EventSource.CLOSED) {
        pushLine({ type: 'error', line: 'Conexão com o painel de execução encerrada.' });
      }
    };
  }, [pushLine]);

  /**
   * Aprova e executa. Devolve `true` se o processo foi disparado.
   *
   * A ordem importa: aprovar primeiro, commitar depois. O servidor recusa
   * commit de plano não aprovado, então inverter daria 400 sempre.
   *
   * As duas falhas vão para o LOG do painel, não só para o valor de retorno.
   * Antes elas morriam num `console.error` no App: quem clicasse em "Executar
   * agora" via o painel abrir e parar em "Falhou", sem uma linha dizendo por
   * quê. O caso que expôs isso é a trava de execução única — a recusa é uma
   * frase inteira, escrita para o usuário, e ela não chegava até ele.
   */
  const approveAndRun = useCallback(
    async (planId: string): Promise<{ ok: boolean; error?: string }> => {
      try {
        const approveRes = await fetch(`/api/studio/approve/${planId}`, { method: 'POST' });
        const approveData = await approveRes.json().catch(() => ({}));
        if (!approveRes.ok || approveData.ok === false) {
          const error =
            approveData.error || `Falha ao aprovar o plano (HTTP ${approveRes.status}).`;
          // Log limpo: a falha aqui é do plano atual, não da execução anterior.
          setState({ ...EMPTY_STATE, phase: 'failed', lines: [] });
          pushLine({ type: 'error', message: error, line: error });
          return { ok: false, error };
        }

        // Limpa o log da execução anterior antes de abrir o novo canal.
        setState({ ...EMPTY_STATE, runId: null, phase: 'running', lines: [], exitCode: null });
        openStream();

        const commitRes = await fetch(`/api/studio/commit/${planId}`, { method: 'POST' });
        const commitData = await commitRes.json().catch(() => ({}));
        if (!commitRes.ok || commitData.ok === false) {
          const error = commitData.error || `Falha ao executar (HTTP ${commitRes.status}).`;
          sourceRef.current?.close();
          sourceRef.current = null;
          setState((prev) => ({ ...prev, phase: 'failed' }));
          pushLine({ type: 'error', message: error, line: error });
          return { ok: false, error };
        }

        setState((prev) => ({ ...prev, runId: commitData.run_id ?? prev.runId, phase: 'running' }));
        return { ok: true };
      } catch (error) {
        sourceRef.current?.close();
        sourceRef.current = null;
        const mensagem = error instanceof Error ? error.message : String(error);
        setState((prev) => ({ ...prev, phase: 'failed' }));
        pushLine({ type: 'error', message: mensagem, line: mensagem });
        return { ok: false, error: mensagem };
      }
    },
    [openStream, pushLine],
  );

  /**
   * Para a execução em andamento.
   *
   * Não fecha o SSE nem mexe na fase: quem faz isso é o evento `exit` que o
   * servidor manda depois que o processo morre de verdade. Marcar "cancelado"
   * aqui deixaria o painel dizendo que acabou enquanto o Python ainda roda.
   *
   * Em caso de falha a linha de erro vai para o PRÓPRIO log, não para o
   * chamador: o painel é o lugar onde o usuário está olhando, e um `{ok:false}`
   * devolvido em silêncio não apareceria em lugar nenhum.
   */
  const cancel = useCallback(async (): Promise<{ ok: boolean; error?: string }> => {
    const runId = state.runId;
    if (!runId) return { ok: false, error: 'Nenhuma execução em andamento.' };
    try {
      const res = await fetch(`/api/studio/cancel/${runId}`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        const error = data.error || `Falha ao cancelar (HTTP ${res.status}).`;
        pushLine({ type: 'error', line: error });
        return { ok: false, error };
      }
      return { ok: true };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      pushLine({ type: 'error', line: `Falha ao cancelar: ${message}` });
      return { ok: false, error: message };
    }
  }, [state.runId, pushLine]);

  /** Rejeita o plano sem executar. Não há rota no servidor: é só descartar. */
  const discard = useCallback(() => {
    setState(EMPTY_STATE);
  }, []);

  const clearLog = useCallback(() => {
    setState(EMPTY_STATE);
  }, []);

  return { runState: state, approveAndRun, cancel, discard, clearLog };
}

/** Reexport para conveniência dos componentes. */
export type { ClipPlan };
