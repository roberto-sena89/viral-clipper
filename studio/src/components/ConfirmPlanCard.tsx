/**
 * Cartão de confirmação do plano de corte.
 *
 * Este é o ponto de decisão do usuário: nada roda no pipeline sem um clique
 * aqui. O cartão mostra exatamente o comando que será executado — não um
 * resumo amigável do que o agente *disse* que faria.
 *
 * Dois botões, com pesos bem diferentes de propósito:
 *   - Executar (verde, primário)  → aprova + dispara
 *   - Descartar (texto, discreto) → só some da tela
 *
 * Não existe botão de "pular confirmação" nem checkbox de "não perguntar de
 * novo". Se existisse, o gate deixaria de ser um gate.
 */

import { useState, useCallback } from 'react';
import { Button, Tooltip } from 'tdesign-react';
import {
  CheckCircleIcon,
  CloseIcon,
  CopyIcon,
  FileCopyIcon,
  LinkIcon,
  TimeIcon,
} from 'tdesign-icons-react';
import type { ClipPlan } from '../types';
import { isDisplayableCommand } from '../utils/planParser';

interface ConfirmPlanCardProps {
  plan: ClipPlan;
  disabled?: boolean;
  onExecute: (planId: string) => void;
  onDiscard: (planId: string) => void;
}

export function ConfirmPlanCard({ plan, disabled, onExecute, onDiscard }: ConfirmPlanCardProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(plan.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Sem permissão de clipboard (contexto não-seguro). O usuário ainda
      // consegue selecionar o texto manualmente — por isso não é um erro fatal.
      setCopied(false);
    }
  }, [plan.command]);

  const showCommand = isDisplayableCommand(plan.command);

  // Depois de executado, o cartão vira um selo informativo.
  if (plan.consumed) {
    return (
      <div
        className="animate-fade-in flex items-center gap-2 px-3 py-2 rounded-lg text-sm"
        style={{
          backgroundColor: 'rgba(43, 164, 113, 0.1)',
          color: '#2ba471',
          border: '1px solid rgba(43, 164, 113, 0.3)',
        }}
      >
        <CheckCircleIcon />
        <span className="font-medium">Plano {plan.planId} enviado para execução.</span>
        <span className="opacity-75">Acompanhe no painel ao lado.</span>
      </div>
    );
  }

  return (
    <div
      className="animate-fade-in rounded-xl overflow-hidden"
      style={{
        border: '1px solid var(--td-brand-color)',
        backgroundColor: 'var(--td-bg-color-container)',
      }}
    >
      {/* Cabeçalho */}
      <div
        className="flex items-center gap-2 px-4 py-3"
        style={{ backgroundColor: 'rgba(0, 82, 217, 0.08)' }}
      >
        <TimeIcon style={{ color: 'var(--td-brand-color)' }} />
        <span className="font-medium text-sm" style={{ color: 'var(--td-brand-color)' }}>
          Confirmação necessária
        </span>
        <code
          className="text-xs px-1.5 py-0.5 rounded font-mono"
          style={{
            backgroundColor: 'var(--td-bg-color-component)',
            color: 'var(--td-text-color-secondary)',
          }}
        >
          {plan.planId}
        </code>
      </div>

      <div className="px-4 py-3 flex flex-col gap-3">
        {/* URL de origem */}
        {plan.url && (
          <div className="flex items-start gap-2 text-sm">
            <LinkIcon
              className="mt-0.5 flex-shrink-0"
              style={{ color: 'var(--td-text-color-placeholder)' }}
            />
            <span
              className="break-all"
              style={{ color: 'var(--td-text-color-secondary)' }}
            >
              {plan.url}
            </span>
          </div>
        )}

        {/* Resumo do que será feito */}
        {plan.summary.length > 0 && (
          <ul className="flex flex-col gap-1 text-sm m-0 pl-0 list-none">
            {plan.summary.map((item, i) => (
              <li key={i} className="flex items-start gap-2">
                <span
                  className="mt-1.5 flex-shrink-0 rounded-full"
                  style={{
                    width: 5,
                    height: 5,
                    backgroundColor: 'var(--td-text-color-placeholder)',
                  }}
                />
                <span style={{ color: 'var(--td-text-color-primary)' }}>{item}</span>
              </li>
            ))}
          </ul>
        )}

        {/* Comando exato — o que o servidor realmente vai rodar */}
        {showCommand && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <span
                className="text-xs uppercase tracking-wide"
                style={{ color: 'var(--td-text-color-placeholder)' }}
              >
                Comando exato
              </span>
              <Tooltip content={copied ? 'Copiado' : 'Copiar comando'} placement="left">
                <Button
                  size="small"
                  variant="text"
                  onClick={handleCopy}
                  style={{ color: copied ? '#2ba471' : 'var(--td-text-color-secondary)' }}
                >
                  {copied ? <FileCopyIcon /> : <CopyIcon />}
                  <span className="ml-1 text-xs">{copied ? 'Copiado' : 'Copiar'}</span>
                </Button>
              </Tooltip>
            </div>
            <pre
              className="text-xs p-3 rounded-lg overflow-x-auto m-0 font-mono leading-relaxed"
              style={{
                backgroundColor: 'var(--td-bg-color-component)',
                color: 'var(--td-text-color-primary)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}
            >
              {plan.command}
            </pre>
            <span className="text-xs" style={{ color: 'var(--td-text-color-placeholder)' }}>
              Copiar serve para rodar você mesmo no terminal, sem passar pelo Studio.
            </span>
          </div>
        )}

        {/* Falha ao aprovar */}
        {plan.failed && (
          <div
            className="text-sm px-3 py-2 rounded-lg"
            style={{ backgroundColor: 'rgba(227, 77, 89, 0.1)', color: '#e34d59' }}
          >
            Falha ao aprovar o plano. Ele pode ter expirado (validade de 10 min) — peça um novo
            plano ao agente.
          </div>
        )}

        {/* Ações */}
        <div className="flex items-center gap-2 pt-1">
          <Button
            theme="success"
            size="medium"
            disabled={disabled}
            onClick={() => onExecute(plan.planId)}
            style={{ backgroundColor: '#2ba471', borderColor: '#2ba471', color: 'white' }}
          >
            <CheckCircleIcon />
            <span className="ml-1">Executar agora</span>
          </Button>
          <Button
            variant="text"
            size="medium"
            disabled={disabled}
            onClick={() => onDiscard(plan.planId)}
            style={{ color: 'var(--td-text-color-secondary)' }}
          >
            <CloseIcon />
            <span className="ml-1">Descartar</span>
          </Button>
          <span
            className="text-xs ml-auto"
            style={{ color: 'var(--td-text-color-placeholder)' }}
          >
            Nada roda sem este clique
          </span>
        </div>
      </div>
    </div>
  );
}
