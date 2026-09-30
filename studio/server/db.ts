import Database from 'better-sqlite3';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// 数据库文件路径
//
// `STUDIO_DB` permite desviar o banco — inclusive para `:memory:`.
//
// Nao e conveniencia: sem isso, todo teste que sobe o servidor escrevia no
// banco de PRODUCAO. O `test:llm` manda uma mensagem em /api/chat, que cria uma
// sessao de verdade. Medido: 35 sessoes no banco, e as 5 mais recentes eram
// todas conversas de teste — a barra lateral do app enchia de "Corta
// https://www.youtube.com/..." a cada execucao.
const dbPath = process.env.STUDIO_DB || path.join(__dirname, '..', 'data', 'chat.db');

/**
 * Caminho efetivo do banco, exportado para o banner do servidor.
 *
 * O banner anunciava "data/chat.db" fixo no código. Com `STUDIO_DB=:memory:`
 * (que é o que os testes usam) ele continuava dizendo que o banco de produção
 * estava aberto — um diagnóstico que mente é pior que nenhum, porque manda
 * procurar o problema no lugar errado.
 */
export const DB_PATH = dbPath;

// 确保 data 目录存在
import fs from 'fs';
// Em `:memory:` nao ha diretorio para criar (`path.dirname(':memory:')` e `.`).
const dataDir = path.dirname(dbPath);
if (dbPath !== ':memory:' && !fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}

// 创建数据库连接
//
// A anotacao `Database.Database` NAO e decoracao. O tipo inferido de
// `new Database(...)` e `BetterSqlite3.Database`, que vive num namespace nao
// exportado do @types — e o `export default db` abaixo obriga o tsc a nomear
// esse tipo ao emitir a declaracao. Sem o alias ele falha com TS4023
// ("cannot be named") e o `tsc -b` inteiro para.
const db: Database.Database = new Database(dbPath);

// 启用 WAL 模式以提高性能
db.pragma('journal_mode = WAL');

// 初始化数据库表
db.exec(`
  -- 会话表
  CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT NOT NULL,
    sdk_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  -- 消息表
  CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    tool_calls TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
  );

  -- 为会话 ID 创建索引
  CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);

  -- Preferências duráveis que o agente aprendeu sobre o usuário.
  --
  -- Por que tabela própria em vez de "resumir a conversa": o que precisa durar
  -- entre sessões é um punhado de fatos estáveis (quantos cortes ele costuma
  -- querer, qual legenda, para qual plataforma), não o texto da conversa. Um
  -- resumo automático guardaria o que foi dito numa terça e não o que ele quer
  -- sempre — e ainda por cima sem o usuário poder ver nem corrigir. Aqui cada
  -- linha é explícita, inspecionável e apagável.
  CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );
`);

// 数据库迁移：添加 sdk_session_id 列（如果不存在）
try {
  const tableInfo = db.prepare("PRAGMA table_info(sessions)").all() as Array<{ name: string }>;
  const hasColumn = tableInfo.some(col => col.name === 'sdk_session_id');
  if (!hasColumn) {
    db.exec("ALTER TABLE sessions ADD COLUMN sdk_session_id TEXT");
    console.log("[DB] Added sdk_session_id column to sessions table");
  }
} catch (e) {
  // 忽略错误（列可能已存在）
}

// 类型定义
export interface DbSession {
  id: string;
  title: string;
  model: string;
  sdk_session_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface DbMessage {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  model: string | null;
  created_at: string;
  tool_calls: string | null;
}

// ============= 会话操作 =============

// 获取所有会话
export function getAllSessions(): DbSession[] {
  const stmt = db.prepare('SELECT * FROM sessions ORDER BY updated_at DESC');
  return stmt.all() as DbSession[];
}

// 获取单个会话
export function getSession(id: string): DbSession | undefined {
  const stmt = db.prepare('SELECT * FROM sessions WHERE id = ?');
  return stmt.get(id) as DbSession | undefined;
}

// 创建会话
//
// `sdk_session_id` e opcional na ENTRADA: toda sessao nasce sem sessao de SDK.
// Exigir o campo obrigava o chamador a escrever `sdk_session_id: null` so para
// satisfazer o tipo — e o unico chamador existente (POST /api/sessions) nao
// escrevia, o que passou despercebido ate o `tsc -b` ser consertado.
export function createSession(
  session: Omit<DbSession, 'sdk_session_id'> & { sdk_session_id?: string | null },
): DbSession {
  const row: DbSession = { ...session, sdk_session_id: session.sdk_session_id ?? null };
  const stmt = db.prepare(`
    INSERT INTO sessions (id, title, model, sdk_session_id, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `);
  stmt.run(row.id, row.title, row.model, row.sdk_session_id, row.created_at, row.updated_at);
  return row;
}

// 更新会话
export function updateSession(id: string, updates: Partial<Pick<DbSession, 'title' | 'model' | 'sdk_session_id'>>): boolean {
  const fields: string[] = [];
  const values: any[] = [];
  
  if (updates.title !== undefined) {
    fields.push('title = ?');
    values.push(updates.title);
  }
  if (updates.model !== undefined) {
    fields.push('model = ?');
    values.push(updates.model);
  }
  if (updates.sdk_session_id !== undefined) {
    fields.push('sdk_session_id = ?');
    values.push(updates.sdk_session_id);
  }
  
  if (fields.length === 0) return false;
  
  fields.push('updated_at = ?');
  values.push(new Date().toISOString());
  values.push(id);
  
  const stmt = db.prepare(`UPDATE sessions SET ${fields.join(', ')} WHERE id = ?`);
  const result = stmt.run(...values);
  return result.changes > 0;
}

// 删除会话
export function deleteSession(id: string): boolean {
  const stmt = db.prepare('DELETE FROM sessions WHERE id = ?');
  const result = stmt.run(id);
  return result.changes > 0;
}

// ============= 消息操作 =============

// 获取会话的所有消息
//
// `rowid` como desempate NAO e decoracao: `created_at` tem precisao de
// milissegundo e dois registros gravados no mesmo ms empatam. Sem desempate a
// ordem fica indefinida, e o historico do chat montado a partir daqui poderia
// sair com a resposta antes da pergunta.
export function getMessagesBySession(sessionId: string): DbMessage[] {
  const stmt = db.prepare(
    'SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC, rowid ASC',
  );
  return stmt.all(sessionId) as DbMessage[];
}

// 创建消息
export function createMessage(message: DbMessage): DbMessage {
  const stmt = db.prepare(`
    INSERT INTO messages (id, session_id, role, content, model, created_at, tool_calls)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);
  stmt.run(
    message.id,
    message.session_id,
    message.role,
    message.content,
    message.model,
    message.created_at,
    message.tool_calls
  );
  
  // 更新会话的 updated_at
  const updateStmt = db.prepare('UPDATE sessions SET updated_at = ? WHERE id = ?');
  updateStmt.run(new Date().toISOString(), message.session_id);
  
  return message;
}

// 更新消息内容
export function updateMessage(id: string, updates: Partial<Pick<DbMessage, 'content' | 'tool_calls'>>): boolean {
  const fields: string[] = [];
  const values: any[] = [];
  
  if (updates.content !== undefined) {
    fields.push('content = ?');
    values.push(updates.content);
  }
  if (updates.tool_calls !== undefined) {
    fields.push('tool_calls = ?');
    values.push(updates.tool_calls);
  }
  
  if (fields.length === 0) return false;
  
  values.push(id);
  
  const stmt = db.prepare(`UPDATE messages SET ${fields.join(', ')} WHERE id = ?`);
  const result = stmt.run(...values);
  return result.changes > 0;
}

// 删除消息
export function deleteMessage(id: string): boolean {
  const stmt = db.prepare('DELETE FROM messages WHERE id = ?');
  const result = stmt.run(id);
  return result.changes > 0;
}

// 批量创建消息（用于保存对话）
export function createMessages(messages: DbMessage[]): void {
  const stmt = db.prepare(`
    INSERT INTO messages (id, session_id, role, content, model, created_at, tool_calls)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);
  
  const insertMany = db.transaction((msgs: DbMessage[]) => {
    for (const msg of msgs) {
      stmt.run(msg.id, msg.session_id, msg.role, msg.content, msg.model, msg.created_at, msg.tool_calls);
    }
  });
  
  insertMany(messages);
}

// ============= Preferências =============

export interface DbPreference {
  key: string;
  value: string;
  updated_at: string;
}

/** Todas as preferências, em ordem estável (alfabética pela chave). */
export function listPreferences(): DbPreference[] {
  const stmt = db.prepare('SELECT * FROM preferences ORDER BY key ASC');
  return stmt.all() as DbPreference[];
}

/**
 * Grava (ou atualiza) uma preferência. Idempotente pela chave.
 *
 * `INSERT ... ON CONFLICT DO UPDATE` em vez de "apaga e insere": assim a
 * operação é uma só, atômica, e uma falha no meio não deixa a preferência
 * apagada sem substituta.
 */
export function setPreference(key: string, value: string): DbPreference {
  const row: DbPreference = { key, value, updated_at: new Date().toISOString() };
  const stmt = db.prepare(`
    INSERT INTO preferences (key, value, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `);
  stmt.run(row.key, row.value, row.updated_at);
  return row;
}

/** Remove uma preferência. `false` quando a chave não existia. */
export function deletePreference(key: string): boolean {
  const stmt = db.prepare('DELETE FROM preferences WHERE key = ?');
  return stmt.run(key).changes > 0;
}

// 清空所有数据
export function clearAllData(): void {
  db.exec('DELETE FROM messages');
  db.exec('DELETE FROM sessions');
  db.exec('DELETE FROM preferences');
}

export default db;
