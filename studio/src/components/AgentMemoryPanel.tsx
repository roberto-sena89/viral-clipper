/**
 * O que o agente aprendeu sobre o usuário — e o botão para apagar.
 *
 * ─── Por que isto existe na interface ────────────────────────────────────────
 *
 * A memória do agente é gravada por uma ferramenta (`lembrar_preferencia`), e a
 * decisão de o que guardar é do modelo. Isso é deliberado — mas cria um risco
 * que só a interface resolve: se ele registrar algo errado ("ele sempre quer 10
 * cortes"), essa preferência passa a valer em TODA conversa, e sem uma tela para
 * ler e apagar o usuário não teria como descobrir de onde veio nem como
 * desfazer.
 *
 * Memória que o usuário não consegue inspecionar não é uma ferramenta: é um
 * passivo. Esta tela é o que fecha o ciclo — o agente diz o que registrou, o
 * usuário confere aqui, e discorda apagando.
 *
 * A lista é lida do servidor a cada abertura, e não guardada em estado local:
 * uma preferência pode ser gravada no meio de uma conversa em outra aba, e um
 * painel que mostrasse um retrato antigo daria a impressão de que a memória
 * está vazia quando não está.
 */

import { useCallback, useEffect, useState } from 'react';
import { Button, Popconfirm, Tooltip } from 'tdesign-react';
import { DeleteIcon, RefreshIcon } from 'tdesign-icons-react';
import { Brain } from 'lucide-react';

interface Preference {
  key: string;
  value: string;
}

export function AgentMemoryPanel() {
  const [preferences, setPreferences] = useState<Preference[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(true);

  const carregar = useCallback(async () => {
    setCarregando(true);
    try {
      const res = await fetch('/api/studio/preferences');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setPreferences(Array.isArray(json?.preferences) ? json.preferences : []);
      setErro(null);
    } catch (e) {
      // Falha de leitura NÃO vira lista vazia. Uma tela que diz "nada
      // registrado" quando na verdade a API não respondeu é pior que um erro:
      // ela mente sobre o estado da memória, que é justamente o que o usuário
      // veio conferir aqui.
      setErro(e instanceof Error ? e.message : String(e));
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  const esquecer = async (key: string) => {
    try {
      const res = await fetch(`/api/studio/preferences/${encodeURIComponent(key)}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setPreferences(Array.isArray(json?.preferences) ? json.preferences : []);
      setErro(null);
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2
            className="text-lg font-medium flex items-center gap-2"
            style={{ color: 'var(--td-text-color-primary)' }}
          >
            <Brain size={18} />
            Memória do agente
          </h2>
          <p className="text-sm mt-1" style={{ color: 'var(--td-text-color-secondary)' }}>
            O que o agente registrou sobre o seu jeito de trabalhar. Vale como
            padrão nas próximas conversas — apague o que estiver errado.
          </p>
        </div>
        <Tooltip content="Recarregar">
          <Button
            variant="text"
            shape="circle"
            size="small"
            icon={<RefreshIcon />}
            loading={carregando}
            onClick={() => void carregar()}
          />
        </Tooltip>
      </div>

      <div
        className="p-4 rounded-xl border"
        style={{
          backgroundColor: 'var(--td-bg-color-container)',
          borderColor: 'var(--td-component-border)',
        }}
      >
        {erro ? (
          <div className="text-sm" style={{ color: 'var(--td-error-color, #d54941)' }}>
            Não consegui ler a memória do agente ({erro}). O servidor do Studio
            está no ar?
          </div>
        ) : preferences.length === 0 ? (
          <div className="text-sm" style={{ color: 'var(--td-text-color-placeholder)' }}>
            {carregando
              ? 'Carregando…'
              : 'Nada registrado ainda. Quando você disser algo que continua ' +
                'verdade no próximo vídeo ("eu sempre quero 4 cortes"), o agente ' +
                'grava aqui e avisa na conversa.'}
          </div>
        ) : (
          <div className="space-y-2">
            {preferences.map((p) => (
              <div
                key={p.key}
                className="flex items-center gap-3 p-2 rounded-lg"
                style={{ backgroundColor: 'var(--td-bg-color-component)' }}
              >
                <code
                  className="text-xs px-2 py-1 rounded flex-shrink-0"
                  style={{
                    backgroundColor: 'var(--td-bg-color-container)',
                    color: 'var(--td-text-color-secondary)',
                  }}
                >
                  {p.key}
                </code>
                <div className="flex-1 min-w-0 text-sm truncate" style={{ color: 'var(--td-text-color-primary)' }}>
                  {p.value}
                </div>
                <Popconfirm
                  content={`Esquecer "${p.key}"?`}
                  onConfirm={() => void esquecer(p.key)}
                >
                  <Tooltip content="Esquecer">
                    <Button variant="text" shape="circle" size="small" icon={<DeleteIcon />} />
                  </Tooltip>
                </Popconfirm>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default AgentMemoryPanel;
