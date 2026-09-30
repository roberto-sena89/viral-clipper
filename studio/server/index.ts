import express from "express";
import { v4 as uuidv4 } from "uuid";
import path from "path";
import { fileURLToPath } from "url";
import * as db from "./db.js";
import { registerStudioRoutes } from "./studioRoutes.js";
import { registerChatRoute, loadModelChain, listModels, DEFAULT_MODEL_ID } from "./chatRoute.js";
import { resolveRoot } from "./preflight.js";
import { loadDotEnv } from "./envFile.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Carrega o .env ANTES de ler qualquer process.env.
//
// Sem esta chamada o arquivo nao tinha efeito nenhum: o codigo abaixo usa
// process.env.X direto, e nada jamais povoava esse ambiente a partir do disco.
// O .env virava documentacao morta — parecia configuracao e nao era. O sintoma
// era o pior possivel: o validador (que LE o .env) reportava sucesso e o chat
// falhava, porque so o servidor ignorava o arquivo.
//
// Precisa vir antes do primeiro process.env, por isso esta no topo e nao dentro
// de uma funcao de bootstrap.
const envFile = path.join(__dirname, "..", ".env");
const loadedVars = loadDotEnv(envFile);
console.log(
  loadedVars.length > 0
    ? `[Env] .env carregado (${loadedVars.length} variavel(is)): ${loadedVars.join(", ")}`
    : `[Env] .env ausente ou vazio em ${envFile} — usando apenas o ambiente do processo`,
);

// 待处理的权限请求
//
// NOTA: este bloco ficou do caminho antigo baseado no SDK. O chat atual
// (chatRoute.ts) nao pede permissao por HTTP — quem barra execucao e o
// clipRunner.ts, que checa a aprovacao do plano. Mantido porque a rota
// /api/permission-response continua respondendo, e remover o par
// rota+estado junto e mais seguro do que deixar a rota orfa.
interface PendingPermission {
  resolve: (result: unknown) => void;
  reject: (error: Error) => void;
  toolName: string;
  input: Record<string, unknown>;
  sessionId: string;
  timestamp: number;
}

const pendingPermissions = new Map<string, PendingPermission>();

const app = express();
// `Number(...)` não é estilo: `process.env.PORT` é `string | undefined`, e sem
// a conversão o tipo de PORT vira `string | 3000`, que não casa com nenhuma
// sobrecarga de `app.listen` (TS2769). Em runtime funcionava por acidente — o
// Node coage string numérica —, então o erro só aparecia no `tsc`. E um
// `PORT=abc` chegava até o listen em vez de cair no padrão.
const PORT = Number(process.env.PORT) || 3000;
const HOST = '127.0.0.1';

// Middleware
app.use(express.json());

// 健康检查
app.get("/api/health", (_req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

// 获取可用模型列表
//
// Substituiu a versao que perguntava ao SDK. Aquele caminho tinha dois
// problemas: exigia CODEBUDDY_API_KEY, e `getAvailableModels()` nao le o
// models.json — devolvia um snapshot em cache que ignorava a configuracao do
// projeto. Agora a lista vem do proprio arquivo, resolvendo as chaves.
app.get("/api/models", (_req, res) => {
  const chain = loadModelChain(resolveRoot());
  if (chain.length === 0) {
    res.json({
      models: [],
      defaultModel: DEFAULT_MODEL_ID,
      error:
        "Nenhum modelo utilizavel. Preencha uma chave em studio/.env " +
        "(VYCE_API_KEY, KILO_API_KEY ou NVIDIA_API_KEY).",
    });
    return;
  }
  res.json(listModels(chain));
});

// ============= 会话 API =============

// 获取所有会话（包含消息数量）
app.get("/api/sessions", (_req, res) => {
  try {
    const sessions = db.getAllSessions();
    const sessionsWithMessages = sessions.map(session => {
      const messages = db.getMessagesBySession(session.id);
      return {
        ...session,
        messageCount: messages.length
      };
    });
    res.json({ sessions: sessionsWithMessages });
  } catch (error: any) {
    console.error("[Sessions] Error:", error);
    res.status(500).json({ error: error?.message || "获取会话失败" });
  }
});

// 获取单个会话及其消息
app.get("/api/sessions/:sessionId", (req, res) => {
  try {
    const { sessionId } = req.params;
    const session = db.getSession(sessionId);
    
    if (!session) {
      return res.status(404).json({ error: "会话不存在" });
    }
    
    const messages = db.getMessagesBySession(sessionId);
    
    // 解析 tool_calls JSON
    const parsedMessages = messages.map(msg => ({
      ...msg,
      tool_calls: msg.tool_calls ? JSON.parse(msg.tool_calls) : null
    }));
    
    res.json({ session, messages: parsedMessages });
  } catch (error: any) {
    console.error("[Session] Error:", error);
    res.status(500).json({ error: error?.message || "获取会话失败" });
  }
});

// 创建新会话
app.post("/api/sessions", (req, res) => {
  try {
    const { model = DEFAULT_MODEL_ID, title = "新对话" } = req.body;
    const now = new Date().toISOString();
    
    const session = db.createSession({
      id: uuidv4(),
      title,
      model,
      created_at: now,
      updated_at: now
    });
    
    res.json({ session });
  } catch (error: any) {
    console.error("[Create Session] Error:", error);
    res.status(500).json({ error: error?.message || "创建会话失败" });
  }
});

// 更新会话
app.patch("/api/sessions/:sessionId", (req, res) => {
  try {
    const { sessionId } = req.params;
    const { title, model } = req.body;
    
    const success = db.updateSession(sessionId, { title, model });
    
    if (!success) {
      return res.status(404).json({ error: "会话不存在" });
    }
    
    res.json({ success: true });
  } catch (error: any) {
    console.error("[Update Session] Error:", error);
    res.status(500).json({ error: error?.message || "更新会话失败" });
  }
});

// 删除会话
app.delete("/api/sessions/:sessionId", (req, res) => {
  try {
    const { sessionId } = req.params;
    const success = db.deleteSession(sessionId);
    
    if (!success) {
      return res.status(404).json({ error: "会话不存在" });
    }
    
    res.json({ success: true });
  } catch (error: any) {
    console.error("[Delete Session] Error:", error);
    res.status(500).json({ error: error?.message || "删除会话失败" });
  }
});

// ============= 聊天 API =============

// 权限响应 API
app.post("/api/permission-response", (req, res) => {
  const { requestId, behavior, message } = req.body;
  
  console.log(`[Permission] Response received: requestId=${requestId}, behavior=${behavior}`);
  
  const pending = pendingPermissions.get(requestId);
  if (!pending) {
    console.log(`[Permission] Request not found: ${requestId}`);
    return res.status(404).json({ error: "权限请求不存在或已超时" });
  }
  
  // 清除请求
  pendingPermissions.delete(requestId);
  
  if (behavior === 'allow') {
    pending.resolve({
      behavior: 'allow',
      updatedInput: pending.input
    });
  } else {
    pending.resolve({
      behavior: 'deny',
      message: message || '用户拒绝了此操作'
    });
  }
  
  res.json({ success: true });
});

// 发送消息并获取流式响应
//
// Substituiu a versao baseada no SDK do CodeBuddy. O contrato SSE e o mesmo —
// o frontend nao mudou. O que mudou e quem fala com o modelo: agora e o
// chatRoute.ts + llm.ts, que falam OpenAI-format direto com os provedores
// do models.json, sem exigir CODEBUDDY_API_KEY.
//
// O caminho antigo baseado no SDK foi removido daqui, e depois removido do
// projeto: `/api/check-login` e `/api/save-env-config` existiam so para ler e
// gravar CODEBUDDY_API_KEY / CODEBUDDY_AUTH_TOKEN, credenciais que o modo padrao
// nao usa. Mantinham de pe a dependencia `@tencent-ai/agent-sdk` inteira para
// exibir um status de login de um modo alternativo. O painel que os consumia
// saiu do SettingsPage na mesma mudanca — nao ha mais nada para configurar ali.
registerChatRoute(app, {
  projectRoot: () => resolveRoot(),
  ensureSession: (sessionId, firstMessage, model) => {
    const existing = sessionId ? db.getSession(sessionId) : null;
    if (existing) return existing.id;
    const now = new Date().toISOString();
    const created = db.createSession({
      id: sessionId || uuidv4(),
      title: firstMessage.slice(0, 30) + (firstMessage.length > 30 ? "..." : ""),
      model,
      sdk_session_id: null,
      created_at: now,
      updated_at: now,
    });
    return created.id;
  },
  saveUserMessage: (sessionId, content) => {
    const id = uuidv4();
    db.createMessage({
      id,
      session_id: sessionId,
      role: "user",
      content,
      model: null,
      created_at: new Date().toISOString(),
      tool_calls: null,
    });
    return id;
  },
  saveAssistantMessage: (sessionId, content, model, toolCalls) => {
    db.createMessage({
      id: uuidv4(),
      session_id: sessionId,
      role: "assistant",
      content,
      model,
      created_at: new Date().toISOString(),
      tool_calls: toolCalls.length > 0 ? JSON.stringify(toolCalls) : null,
    });
    db.updateSession(sessionId, { model });
  },
});

// 启动服务器
// Rotas do Studio (preflight, plano/confirmação e execução do viral-clipper).
registerStudioRoutes(app);

// Exportado para que os testes possam FECHAR o servidor.
//
// Sem isto, `npm run test:gate` termina os testes e o processo fica pendurado:
// o `app.listen` mantem o event loop vivo, e o runner do Node espera o loop
// drenar. O sintoma era um teste que "passa" e nao sai — seis minutos de
// espera depois do resultado.
export const server = app.listen(PORT, HOST, () => {
  // O caminho REAL do banco, nao um texto fixo. O banner dizia "data/chat.db"
  // sempre, entao ao rodar os testes (STUDIO_DB=:memory:) ele anunciava o
  // banco de producao aberto — e um diagnostico que mente manda procurar o
  // problema no lugar errado.
  const dbLabel = db.DB_PATH === ':memory:' ? ':memory: (descartavel)' : db.DB_PATH;
  const endereco = `地址: http://localhost:${PORT}`;
  const banco = `数据库: ${dbLabel}`;
  console.log(`
╔════════════════════════════════════════════╗
║                                            ║
║     ◉ API 服务器已启动                      ║
║                                            ║
║     ${endereco.padEnd(35)}║
║     ${banco.padEnd(35)}║
║                                            ║
╚════════════════════════════════════════════╝
  `);
});
