/**
 * 类型定义
 */

export type PermissionMode = 'default' | 'acceptEdits' | 'plan' | 'bypassPermissions';

export interface Model {
  modelId: string;
  name: string;
  description?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  input?: Record<string, unknown>;
  status: 'running' | 'completed' | 'error';
  result?: string;
  isError?: boolean;
}

/**
 * Plano de execução pendente de aprovação humana.
 *
 * Vem de `clip_plan` (dentro do `tool_result`) e é o ÚNICO caminho para
 * executar o pipeline. O agente nunca tem o `plan_id` aprovado por conta
 * própria: quem aprova é o clique na interface, via
 * `POST /api/studio/approve/:planId`.
 */
export interface ClipPlan {
  planId: string;
  url: string;
  command: string;
  summary: string[];
  params?: Record<string, unknown>;
  /** true depois que o usuário clicou em Executar. */
  approved?: boolean;
  /** true depois que o processo foi disparado. */
  consumed?: boolean;
  /** true se o POST de aprovação falhou. */
  failed?: boolean;
}

/**
 * Linha do log de execução do CLI, exibida no painel.
 * Espelha `RunEvent` de server/clipRunner.ts.
 */
export interface RunLogLine {
  id: string;
  type: 'started' | 'stdout' | 'stderr' | 'exit' | 'error' | 'cancelled';
  line?: string;
  code?: number | null;
  message?: string;
  timestamp: Date;
}

/** Estado do painel de execução, por conversa. */
export interface RunState {
  runId: string | null;
  /**
   * 'idle' antes de disparar; 'running' durante; no fim, um de quatro:
   * 'done', 'failed', 'cancelled' ou 'degraded'.
   *
   * 'cancelled' é separado de 'failed' de propósito: o processo morre com
   * código não-zero nos dois casos, mas mostrar "Falhou" em vermelho para uma
   * ação que o usuário pediu seria mentir sobre o que aconteceu.
   *
   * 'degraded' segue a mesma lógica e existe pelo mesmo motivo: exit 4 significa
   * que os cortes FORAM produzidos, só que sem legenda e com seleção por energia
   * de áudio. É pior que 'done' e melhor que 'failed' — pintar de vermelho
   * esconderia que há clipe no disco.
   */
  phase: 'idle' | 'running' | 'done' | 'failed' | 'cancelled' | 'degraded' | 'ocupado';
  lines: RunLogLine[];
  exitCode: number | null;
}

/**
 * 内容块类型 - 支持文字和工具调用按顺序排列
 */
export type ContentBlock = 
  | { type: 'text'; text: string }
  | { type: 'tool_use'; toolCall: ToolCall };

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;  // 保留用于兼容，存储纯文本摘要
  model?: string;
  timestamp: Date;
  isStreaming?: boolean;
  toolCalls?: ToolCall[];  // 保留用于兼容
  contentBlocks?: ContentBlock[];  // 新增：按顺序排列的内容块
}

export interface Session {
  id: string;
  title: string;
  model: string;
  agentId?: string;
  cwd?: string;
  permissionMode?: PermissionMode;
  createdAt: Date;
  messages: Message[];
}

export interface CustomAgent {
  id: string;
  name: string;
  description?: string;
  systemPrompt: string;
  icon?: string;
  color?: string;
  permissionMode?: PermissionMode;
  createdAt: Date;
  updatedAt: Date;
}

// Agent 是 CustomAgent 的别名
export type Agent = CustomAgent;

export type Theme = 'light' | 'dark';

/**
 * 权限请求 - 用于工具调用确认
 */
export interface PermissionRequest {
  requestId: string;
  toolUseId: string;
  toolName: string;
  input: Record<string, unknown>;
  sessionId: string;
  timestamp: number;
}

/**
 * 权限响应
 */
export interface PermissionResponse {
  requestId: string;
  behavior: 'allow' | 'deny';
  message?: string;
}
