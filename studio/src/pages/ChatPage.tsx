import { useState, useRef, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Model,
  Session,
  PermissionMode,
  CustomAgent,
  PermissionRequest,
  ClipPlan,
  RunState,
} from '../types';
import { NewChatView } from '../components/NewChatView';
import { ChatMessages } from '../components/ChatMessages';
import { ChatInput } from '../components/ChatInput';
import { RunLogPanel } from '../components/RunLogPanel';

interface ChatPageProps {
  currentSession: Session | undefined;
  models: Model[];
  selectedModel: string;
  agents: CustomAgent[];
  isLoading: boolean;
  inputValue: string;
  permissionRequest: PermissionRequest | null;
  pendingPlan: ClipPlan | null;
  runState: RunState;
  runPanelOpen: boolean;
  permissionMode: PermissionMode;
  onSendMessage: (message: string, newChatOptions?: NewChatOptions, onNavigate?: (path: string) => void) => void;
  onStop: () => void;
  onInputChange: (value: string) => void;
  onModelChange: (modelId: string) => void;
  onPermissionAllow: () => void;
  onPermissionDeny: () => void;
  onPermissionModeChange: (mode: PermissionMode) => void;
  onExecutePlan: (planId: string) => void;
  onDiscardPlan: (planId: string) => void;
  onCloseRunPanel: () => void;
  onClearRunLog: () => void;
  /**
   * Para o render em andamento. Devolve void de propósito: o resultado (a
   * linha "cancelado" ou uma linha de erro) aparece no próprio log, então
   * nenhum chamador precisa tratar retorno.
   */
  onCancelRun: () => void;
}

interface NewChatOptions {
  agentId: string;
  cwd: string;
  permissionMode: PermissionMode;
}

export function ChatPage({
  currentSession,
  models,
  selectedModel,
  agents,
  isLoading,
  inputValue,
  permissionRequest,
  pendingPlan,
  runState,
  runPanelOpen,
  permissionMode,
  onSendMessage,
  onStop,
  onInputChange,
  onModelChange,
  onPermissionAllow,
  onPermissionDeny,
  onPermissionModeChange,
  onExecutePlan,
  onDiscardPlan,
  onCloseRunPanel,
  onClearRunLog,
  onCancelRun,
}: ChatPageProps) {
  const navigate = useNavigate();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  // 新对话页面状态
  const [newChatAgentId, setNewChatAgentId] = useState('default');
  const [newChatCwd, setNewChatCwd] = useState('');

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [currentSession?.messages]);

  // 处理发送消息
  const handleSend = useCallback((message: string) => {
    if (!currentSession) {
      // 新对话
      onSendMessage(message, {
        agentId: newChatAgentId,
        cwd: newChatCwd,
        permissionMode: permissionMode,
      }, (path) => {
        // 重置新对话选项
        setNewChatAgentId('default');
        setNewChatCwd('');
        navigate(path);
      });
    } else {
      onSendMessage(message);
    }
  }, [currentSession, newChatAgentId, newChatCwd, permissionMode, onSendMessage, navigate]);

  const showNewChatView = !currentSession || currentSession.messages.length === 0;

  return (
    <div className="flex-1 flex min-h-0">
      {/* 对话栏 */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* 消息区域 */}
        <div className="flex-1 overflow-y-auto p-6">
          {showNewChatView ? (
            <NewChatView
              agents={agents}
              models={models}
              selectedModel={selectedModel}
              newChatAgentId={newChatAgentId}
              newChatCwd={newChatCwd}
              newChatPermissionMode={permissionMode}
              onSelectModel={onModelChange}
              onSelectAgent={setNewChatAgentId}
              onSetCwd={setNewChatCwd}
              onSetPermissionMode={onPermissionModeChange}
            />
          ) : (
            <ChatMessages
              messages={currentSession!.messages}
              models={models}
              messagesEndRef={messagesEndRef}
              permissionRequest={permissionRequest}
              pendingPlan={pendingPlan}
              onPermissionAllow={onPermissionAllow}
              onPermissionDeny={onPermissionDeny}
              onExecutePlan={onExecutePlan}
              onDiscardPlan={onDiscardPlan}
            />
          )}
        </div>

        {/* 输入区域 */}
        <ChatInput
          inputValue={inputValue}
          selectedModel={selectedModel}
          models={models}
          isLoading={isLoading}
          permissionMode={permissionMode}
          onSend={handleSend}
          onStop={onStop}
          onChange={onInputChange}
          onModelChange={onModelChange}
          onPermissionModeChange={onPermissionModeChange}
        />
      </div>

      {/* 执行面板：固定 420px，独立于对话滚动 */}
      {runPanelOpen && (
        <div className="flex-shrink-0" style={{ width: 420 }}>
          <RunLogPanel
            state={runState}
            onClose={onCloseRunPanel}
            onClear={onClearRunLog}
            onCancel={onCancelRun}
          />
        </div>
      )}
    </div>
  );
}
