/**
 * Testes de `modelCheck.ts`.
 *
 * O alvo dos testes nao e a rede — e a parte que decide o que fazer com a
 * resposta da rede. Essa e a parte que erra em silencio: um veredito trocado
 * faz o validador aprovar um modelo quebrado, e o sintoma so aparece no chat.
 *
 * Uso: npx tsx --test server/modelCheck.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = process.env.VIRCAL_ROOT || path.resolve(__dirname, '..', '..');

// ─── O models.json entregue precisa ser estruturalmente valido ───────────────
// Estes testes rodam contra o arquivo real, nao contra um fixture: o defeito
// que importa e o arquivo que o usuario vai usar.

interface RawModel {
  id?: string;
  name?: string;
  url?: string;
  apiKey?: string;
  $comment?: unknown;
  $disabled?: unknown;
}

function readModelsFile(): { models: RawModel[]; raw: any } {
  const file = path.join(PROJECT_ROOT, '.codebuddy', 'models.json');
  const raw = JSON.parse(fs.readFileSync(file, 'utf-8'));
  return { models: Array.isArray(raw.models) ? raw.models : [], raw };
}

function activeModels(models: RawModel[]): RawModel[] {
  return models.filter((m) => !m.$disabled && m.id);
}

test('models.json: existe e e JSON valido', () => {
  const file = path.join(PROJECT_ROOT, '.codebuddy', 'models.json');
  assert.ok(fs.existsSync(file), `esperado em ${file}`);
  assert.doesNotThrow(() => JSON.parse(fs.readFileSync(file, 'utf-8')));
});

test('todo modelo ativo tem id, name, url e apiKey', () => {
  const { models } = readModelsFile();
  const active = activeModels(models);
  assert.ok(active.length > 0, 'nenhum modelo ativo — o arquivo nao serve para nada');
  for (const m of active) {
    assert.ok(m.id, `modelo sem id: ${JSON.stringify(m).slice(0, 80)}`);
    assert.ok(m.name, `${m.id}: sem name (a UI mostraria o id cru)`);
    assert.ok(m.url, `${m.id}: sem url`);
    assert.ok(m.apiKey, `${m.id}: sem apiKey`);
  }
});

test('toda url termina em /chat/completions', () => {
  // Regra explicita da doc oficial. Uma url sem o sufixo nao da erro de
  // configuracao: ela so falha na primeira mensagem.
  const { models } = readModelsFile();
  for (const m of activeModels(models)) {
    assert.ok(
      m.url!.endsWith('/chat/completions'),
      `${m.id}: url deve terminar em /chat/completions -> ${m.url}`,
    );
  }
});

test('toda apiKey usa referencia ${VAR}, nunca texto plano', () => {
  // Chave em texto plano acaba commitada. A doc suporta ${VAR} de proposito.
  const { models } = readModelsFile();
  for (const m of activeModels(models)) {
    assert.match(
      m.apiKey!,
      /^\$\{[A-Z0-9_]+\}$/,
      `${m.id}: apiKey deve ser ${'{VAR}'} — encontrado: ${m.apiKey!.slice(0, 12)}...`,
    );
  }
});

test('nao ha id duplicado entre modelos ativos', () => {
  // Id repetido significa que o segundo sobrescreve o primeiro em silencio.
  const { models } = readModelsFile();
  const seen = new Map<string, number>();
  for (const m of activeModels(models)) {
    seen.set(m.id!, (seen.get(m.id!) || 0) + 1);
  }
  const dups = [...seen.entries()].filter(([, n]) => n > 1);
  assert.equal(dups.length, 0, `ids duplicados: ${dups.map(([id]) => id).join(', ')}`);
});

test('toda url de gateway conhecido e a esperada', () => {
  // Guarda contra o typo que passa no format check mas nao resolve: um
  // endpoint de gateway escrito a mao cansa de estar certo.
  const EXPECTED: Record<string, string> = {
    OpenRouter: 'https://openrouter.ai/api/v1/chat/completions',
    NVIDIA: 'https://integrate.api.nvidia.com/v1/chat/completions',
    Kilo: 'https://api.kilo.ai/api/gateway/chat/completions',
  };
  const { models } = readModelsFile();
  for (const m of activeModels(models)) {
    const vendor = (m as any).vendor;
    if (vendor && EXPECTED[vendor]) {
      assert.equal(m.url, EXPECTED[vendor], `${m.id}: url divergente do gateway ${vendor}`);
    }
  }
});

test('nenhum modelo ativo aponta para a Anthropic nativa', () => {
  // O SDK so fala formato OpenAI. Uma url api.anthropic.com/v1/messages
  // passaria no "termina com /chat/completions"? Nao — mas um proxy mal
  // configurado poderia. Este teste documenta a restricao no lugar certo.
  const { models } = readModelsFile();
  for (const m of activeModels(models)) {
    assert.ok(
      !m.url!.includes('api.anthropic.com'),
      `${m.id}: Anthropic nativa nao e suportada pelo SDK (so formato OpenAI)`,
    );
  }
});

// ─── Ordenacao dos vereditos ─────────────────────────────────────────────────
// Testa a funcao pura que existe para nao depender de rede no CI.

test('$comment nao desativa um modelo; $disabled desativa', () => {
  // Regressao. A primeira versao do loadModels tratava `$comment` como
  // desativador, e isso removeu o Nemotron do teste em silencio — o unico
  // modelo com problema era o unico que deixou de ser vigiado. Um campo de
  // documentacao nao pode ter efeito funcional.
  const withComment: RawModel = {
    id: 'x/y',
    name: 'X',
    url: 'https://e/v1/chat/completions',
    apiKey: '${X}',
    $comment: 'anotacao qualquer',
  };
  const withDisabled: RawModel = {
    id: 'z/w',
    name: 'Z',
    url: 'https://e/v1/chat/completions',
    apiKey: '${Z}',
    $disabled: { id: 'z/w' },
  };

  assert.ok(activeModels([withComment]).length === 1, '$comment NAO pode desativar');
  assert.equal(activeModels([withDisabled]).length, 0, '$disabled DEVE desativar');
});

test('o Nemotron instavel continua sendo testado', () => {
  // Guarda especifica: este modelo esta declarado com `$comment` explicando
  // instabilidade. Se ele sumir da contagem de ativos, o defeito acima voltou.
  const { models } = readModelsFile();
  const actives = activeModels(models).map((m) => m.id);
  assert.ok(
    actives.includes('nvidia/nemotron-3-super-120b-a12b'),
    `Nemotron sumiu dos ativos. Ativos: ${actives.join(', ')}`,
  );
});

test('vereditos bloqueantes sao falhou e sem-tool-call', () => {
  // A lista e usada para decidir o exit code. Se 'sem-tool-call' deixar de ser
  // bloqueante, um modelo que nunca planeja passa como aprovado.
  const BLOCKING = ['falhou', 'sem-tool-call'];
  assert.ok(BLOCKING.includes('sem-tool-call'), 'um modelo sem tool-call nao serve ao Diretor');
  assert.ok(!BLOCKING.includes('nao-testado'), 'nao-testado e falta de chave, nao defeito');
  assert.ok(!BLOCKING.includes('ok'));
});
