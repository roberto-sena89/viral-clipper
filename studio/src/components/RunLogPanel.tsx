/**
 * Painel de execução: o log do CLI em tempo real.
 *
 * Por que existe: o pipeline demora (download + Whisper em CPU). Sem isto, o
 * usuário clica em Executar e fica olhando uma tela parada sem saber se travou.
 *
 * Decisões que importam:
 *   - diferença visual entre stdout e stderr. O CLI usa stderr para progresso
 *     normal do Whisper, então stderr NÃO é sinônimo de erro. Pintar tudo de
 *     vermelho assustaria sem motivo.
 *   - autoscroll que respeita o usuário: se ele rolou para cima para ler algo,
 *     a gente não arrasta a tela de volta.
 *   - "cancelado" não é "falhou". Os dois terminam com código não-zero, mas o
 *     segundo foi um acidente e o primeiro foi um pedido. Cores e textos
 *     diferentes, senão o painel mente sobre o que aconteceu.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { Button, Popconfirm, Tooltip } from 'tdesign-react';
import { CloseIcon, DeleteIcon, MoveIcon, StopCircleIcon } from 'tdesign-icons-react';
import type { RunState } from '../types';

interface RunLogPanelProps {
  state: RunState;
  onClose: () => void;
  onClear: () => void;
  onCancel: () => void;
}

/** Distância do fundo (px) abaixo da qual consideramos que o usuário está "no fim". */
const STICK_THRESHOLD = 40;

/** Laranja: nem sucesso, nem erro. É uma parada pedida pelo usuário. */
const CANCELLED_COLOR = '#ed7b2f';

function lineStyle(type: string): { color: string; prefix?: string } {
  switch (type) {
    case 'started':
      return { color: 'var(--td-brand-color)', prefix: '▶' };
    case 'stderr':
      // Progresso normal do Whisper chega por aqui. Não é erro.
      return { color: 'var(--td-text-color-secondary)' };
    case 'error':
      return { color: '#e34d59', prefix: '✖' };
    case 'cancelled':
      return { color: CANCELLED_COLOR, prefix: '⏹' };
    case 'exit':
      return { color: 'var(--td-text-color-primary)', prefix: '■' };
    default:
      return { color: 'var(--td-text-color-primary)' };
  }
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString('pt-BR', { hour12: false });
}

/**
 * Rótulo e cor da fase.
 *
 * 'cancelled' é laranja, não vermelho: o processo saiu com código não-zero,
 * mas por pedido do usuário. Pintar de vermelho diria "falhou" para algo que
 * deu certo.
 */
function phaseLabel(phase: RunState['phase']): string {
  switch (phase) {
    case 'running':
      return 'Executando';
    case 'done':
      return 'Concluído';
    case 'failed':
      return 'Falhou';
    case 'cancelled':
      return 'Cancelado';
    default:
      return 'Aguardando';
  }
}

function phaseColor(phase: RunState['phase']): string {
  switch (phase) {
    case 'running':
      return 'var(--td-brand-color)';
    case 'done':
      return '#2ba471';
    case 'failed':
      return '#e34d59';
    case 'cancelled':
      return CANCELLED_COLOR;
    default:
      return 'var(--td-text-color-placeholder)';
  }
}

/**
 * Texto e cor do rodapé, derivados da FASE e não do código de saída.
 *
 * O gate antigo era `exitCode !== null`, e isso quebrava no cancelamento: um
 * processo morto por sinal chega com `code === null`, então o rodapé
 * simplesmente desaparecia — justo no caso em que o usuário mais precisa de
 * uma frase dizendo o que aconteceu.
 *
 * Devolve `null` enquanto não há desfecho, que é o que mantém o rodapé fora
 * da tela durante a execução.
 */
function footerFor(state: RunState): { text: string; color: string } | null {
  switch (state.phase) {
    case 'done':
      return { text: 'Processo finalizado com sucesso.', color: '#2ba471' };
    case 'cancelled':
      return { text: 'Execução cancelada pelo usuário.', color: CANCELLED_COLOR };
    case 'failed':
      return state.exitCode === null
        ? {
            text: 'A execução falhou antes de o processo terminar. Veja o log acima.',
            color: '#e34d59',
          }
        : {
            text: `Processo finalizado com código ${state.exitCode}. Veja o log acima.`,
            color: '#e34d59',
          };
    default:
      return null;
  }
}

export function RunLogPanel({ state, onClose, onClear, onCancel }: RunLogPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);

  // Detecta se o usuário saiu do fim da lista. Só assim o autoscroll é
  // interrompido de forma intencional, e volta quando ele retorna ao fim.
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setStickToBottom(distanceFromBottom <= STICK_THRESHOLD);
  }, []);

  useEffect(() => {
    if (!stickToBottom) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.lines, stickToBottom]);

  const label = phaseLabel(state.phase);
  const color = phaseColor(state.phase);
  const footer = footerFor(state);

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{
        backgroundColor: 'var(--td-bg-color-container)',
        borderLeft: '1px solid var(--td-component-stroke)',
      }}
    >
      {/* Cabeçalho */}
      <div
        className="flex items-center gap-2 px-3 py-2.5 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--td-component-stroke)' }}
      >
        <span
          className="rounded-full flex-shrink-0"
          style={{
            width: 8,
            height: 8,
            backgroundColor: color,
            boxShadow: state.phase === 'running' ? `0 0 6px ${color}` : 'none',
          }}
        />
        <span className="text-sm font-medium" style={{ color: 'var(--td-text-color-primary)' }}>
          Painel de execução
        </span>
        <span className="text-xs" style={{ color }}>
          {label}
        </span>
        {state.runId && (
          <code
            className="text-xs px-1.5 py-0.5 rounded font-mono"
            style={{
              backgroundColor: 'var(--td-bg-color-component)',
              color: 'var(--td-text-color-placeholder)',
            }}
          >
            {state.runId}
          </code>
        )}

        <div className="flex items-center gap-1 ml-auto">
          {/* Só aparece quando o autoscroll está pausado — senão polui. */}
          {!stickToBottom && state.phase === 'running' && (
            <Tooltip content="Voltar ao fim" placement="bottom">
              <Button
                size="small"
                variant="text"
                onClick={() => setStickToBottom(true)}
                style={{ color: 'var(--td-brand-color)' }}
              >
                <MoveIcon />
              </Button>
            </Tooltip>
          )}
          {/*
            O freio. Só existe enquanto roda: depois do `exit` não há o que
            parar, e um botão morto ao lado de outros dois só confunde.

            Com confirmação (popover, não modal) porque este ícone fica numa
            fileira de botões sem rótulo e um clique errado custa caro: o plano
            vira `consumed`, então refazer exige gerar um plano novo.

            Sem Tooltip aqui de propósito: Popconfirm e Tooltip clonam o filho
            para pendurar o gatilho, e aninhar os dois não é suportado. O texto
            do popover já cumpre o papel de explicar o botão.
          */}
          {state.phase === 'running' && (
            <Popconfirm
              theme="warning"
              placement="bottom"
              content="Encerrar o processo em andamento? O progresso já feito é perdido."
              confirmBtn={{ content: 'Parar', theme: 'danger' }}
              cancelBtn={{ content: 'Voltar' }}
              onConfirm={onCancel}
            >
              <Button size="small" variant="text" style={{ color: '#e34d59' }}>
                <StopCircleIcon />
              </Button>
            </Popconfirm>
          )}
          <Tooltip content="Limpar log" placement="bottom">
            <Button
              size="small"
              variant="text"
              onClick={onClear}
              style={{ color: 'var(--td-text-color-secondary)' }}
            >
              <DeleteIcon />
            </Button>
          </Tooltip>
          <Tooltip content="Fechar painel" placement="bottom">
            <Button
              size="small"
              variant="text"
              onClick={onClose}
              style={{ color: 'var(--td-text-color-secondary)' }}
            >
              <CloseIcon />
            </Button>
          </Tooltip>
        </div>
      </div>

      {/* Linhas do log */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-3 py-2 font-mono text-xs leading-relaxed"
      >
        {state.lines.length === 0 ? (
          <div
            className="flex items-center justify-center h-full text-center"
            style={{ color: 'var(--td-text-color-placeholder)' }}
          >
            Nenhuma execução ainda.
            <br />
            Aprove um plano na conversa para começar.
          </div>
        ) : (
          state.lines.map((line) => {
            const style = lineStyle(line.type);
            return (
              <div key={line.id} className="flex gap-2 py-0.5">
                <span
                  className="flex-shrink-0 select-none"
                  style={{ color: 'var(--td-text-color-placeholder)' }}
                >
                  {formatTime(line.timestamp)}
                </span>
                <span
                  className="flex-shrink-0 select-none"
                  style={{ color: style.color, width: 10 }}
                >
                  {style.prefix ?? ''}
                </span>
                <span
                  className="whitespace-pre-wrap break-all"
                  style={{ color: style.color }}
                >
                  {line.line ?? line.message ?? ''}
                </span>
              </div>
            );
          })
        )}
      </div>

      {/* Rodapé com o desfecho */}
      {footer && (
        <div
          className="px-3 py-2 text-xs flex-shrink-0"
          style={{
            borderTop: '1px solid var(--td-component-stroke)',
            color: footer.color,
          }}
        >
          {footer.text}
        </div>
      )}
    </div>
  );
}
