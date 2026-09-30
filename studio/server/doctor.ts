/**
 * Diagnostico do Studio: diz o estado de tudo que pode impedir o chat de
 * funcionar, em um lugar so.
 *
 * Por que existe: as tres causas de falha do Studio moram em lugares diferentes
 * e produzem sintomas parecidos. Uma chave no lugar errado, um `.env` que
 * ninguem le, e uma dependencia faltando no Python dao o mesmo resultado — o
 * chat abre e nao responde. Descobrir qual e o problema exigia abrir tres
 * arquivos e rodar quatro comandos.
 *
 * Este comando NAO conserta nada. Ele so relata. Um comando que mexe em
 * configuracao para "resolver" e perigoso quando o diagnostico estiver errado.
 *
 * ATENCAO — processo externo: usar `execFile` (assincrono), NUNCA `spawnSync`.
 * Neste ambiente o `spawnSync` falha com EBUSY em QUALQUER comando, inclusive
 * `cmd.exe` do sistema. O erro e enganoso: parece problema de arquivo travado,
 * de antivirus ou de PATH. Nao e — e o metodo sincrono. O mesmo comando via
 * `execFile` roda limpo. Sintoma que isso produzia: o doctor acusava ffmpeg
 * ausente e deps faltando enquanto o pipeline funcionava normalmente.
 *
 * Uso: npm run doctor
 */
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { inspectDotEnv } from './envFile.js';
import { resolveRoot } from './preflight.js';

const execFileAsync = promisify(execFile);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const STUDIO_ROOT = path.resolve(__dirname, '..');

// Chaves que o Studio usa. Agrupadas pelo que quebram quando faltam, porque
// "falta uma chave" so e acionavel se vier junto de "e o que para de funcionar".
//
// `REQUIRED` (CODEBUDDY_API_KEY / CODEBUDDY_AUTH_TOKEN) existia aqui e saiu com
// o SDK: nenhuma das duas afeta o chat. As chaves abaixo sao as que importam.
const OPTIONAL = ['VYCE_API_KEY', 'NVIDIA_API_KEY', 'KILO_API_KEY', 'OPENROUTER_API_KEY'];
const BRIDGE = ['VIRCAL_ROOT', 'VIRCAL_PYTHON', 'PORT'];

const OK = '  [ ok ]';
const WARN = '  [aviso]';
const FAIL = '  [FALHA]';

let failures = 0;

function line(mark: string, label: string, detail = ''): void {
  console.log(`${mark} ${label}${detail ? ` — ${detail}` : ''}`);
}

/** Roda um comando e devolve a primeira linha da saida, ou null se falhar. */
async function probe(command: string, args: string[], cwd?: string): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync(command, args, {
      cwd,
      timeout: 90_000,
      windowsHide: true,
      maxBuffer: 4 * 1024 * 1024,
    });
    return stdout.trim();
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  // ─── 1. Arquivo .env ───────────────────────────────────────────────────────
  console.log('\n=== 1. Arquivo .env ===');
  const envPath = path.join(STUDIO_ROOT, '.env');
  if (!fs.existsSync(envPath)) {
    line(FAIL, '.env ausente', `esperado em ${envPath}`);
    failures++;
  } else {
    line(OK, '.env', envPath);
  }

  // ─── 2. Modelos de terceiros ───────────────────────────────────────────────
  // Aqui existia uma secao "Autenticacao do SDK (modo alternativo)", que lia
  // CODEBUDDY_API_KEY / CODEBUDDY_AUTH_TOKEN. Saiu junto com o SDK: essas chaves
  // nao afetam o chat em nada, entao reporta-las era ruido num comando cujo
  // proposito e dizer o que impede o chat de funcionar.
  //
  // A armadilha que aquela secao ensinou continua valendo, e por isso este
  // arquivo NAO chama `loadDotEnv`: `inspectDotEnv` consulta `process.env` E o
  // arquivo, na ordem de precedencia. Ler `process.env` direto aqui daria falso
  // negativo para quem guardou a chave no `.env` — que e o que a secao 1 manda
  // fazer. As chaves abaixo passam pelo mesmo caminho.
  // Estas sao as chaves que o modo padrao (loop proprio) realmente usa.
  // O OPENROUTER_API_KEY esta aqui por completude, mas esta invalida — o
  // diagnostics de /api/v1/key e assunto do README, nao deste comando.
  const optional = inspectDotEnv(envPath, OPTIONAL);

  if (optional.filled.length === 0) {
    line(FAIL, 'nenhuma chave de modelo de terceiros', 'o chat nao tem como responder');
    failures++;
  }

  console.log('\n=== 2. Modelos de terceiros (o que o chat usa) ===');
  for (const key of OPTIONAL) {
    if (optional.filled.includes(key)) {
      line(OK, key);
    } else if (optional.empty.includes(key)) {
      line(WARN, key, 'declarada mas vazia — os modelos que a usam serao pulados');
    } else {
      line(WARN, key, 'nao declarada — os modelos que a usam serao pulados');
    }
  }
  line(OK, 'validar de verdade', 'npm run check:models');

  // ─── 3. models.json ────────────────────────────────────────────────────────
  console.log('\n=== 3. models.json ===');
  let projectRoot = '';
  try {
    projectRoot = resolveRoot();
  } catch (error: any) {
    line(FAIL, 'nao foi possivel determinar a raiz do projeto', error?.message);
    failures++;
  }

  if (projectRoot) {
    const modelsFile = path.join(projectRoot, '.codebuddy', 'models.json');
    if (!fs.existsSync(modelsFile)) {
      line(WARN, 'models.json ausente', `esperado em ${modelsFile}`);
    } else {
      line(OK, 'models.json', modelsFile);
      try {
        const raw = JSON.parse(fs.readFileSync(modelsFile, 'utf-8'));
        const all: any[] = Array.isArray(raw.models) ? raw.models : [];
        const active = all.filter((m) => m.id && !m.$disabled);
        const disabled = all.filter((m) => m.$disabled);
        line(OK, 'modelos', `${active.length} ativo(s), ${disabled.length} desativado(s)`);
      } catch (error: any) {
        line(FAIL, 'models.json nao e JSON valido', error?.message);
        failures++;
      }
    }
  }

  // ─── 5. Ponte com o viral-clipper ──────────────────────────────────────────
  console.log('\n=== 4. Ponte com o viral-clipper ===');
  const bridge = inspectDotEnv(envPath, BRIDGE);
  for (const key of BRIDGE) {
    if (bridge.filled.includes(key)) line(OK, key);
    else if (bridge.empty.includes(key)) line(OK, key, 'vazio — usa o padrao');
    else line(OK, key, 'nao declarada — usa o padrao');
  }

  if (projectRoot) {
    if (fs.existsSync(projectRoot)) {
      line(OK, 'raiz do pipeline', projectRoot);
    } else {
      line(FAIL, 'raiz do pipeline nao existe', projectRoot);
      failures++;
    }
  }

  // O cwd e obrigatorio: o pacote `viralclipper` nao esta instalado no venv,
  // ele e resolvido por sys.path[0] = cwd. De qualquer outro diretorio o CLI
  // falha com exit 1 e sem mensagem util.
  const python =
    process.env.VIRCAL_PYTHON || path.join(projectRoot || '', '.venv', 'Scripts', 'python.exe');

  if (python && fs.existsSync(python)) {
    line(OK, 'interpretador', python);

    // Sem `viralclipper` importavel o CLI morre sem mensagem util.
    const deps = await probe(
      python,
      ['-c', 'import viralclipper, faster_whisper, yt_dlp; print("deps-ok")'],
      projectRoot,
    );
    if (deps?.includes('deps-ok')) {
      line(OK, 'deps do pipeline', 'viralclipper, faster_whisper, yt_dlp');
    } else {
      line(FAIL, 'deps do pipeline', (deps || 'sem saida').slice(0, 160));
      failures++;
    }
  } else {
    line(FAIL, 'interpretador nao encontrado', python || '(nao definido)');
    failures++;
  }

  const ffmpeg = await probe('ffmpeg', ['-version']);
  if (ffmpeg) {
    line(OK, 'ffmpeg', ffmpeg.split('\n')[0].slice(0, 60));
  } else {
    line(FAIL, 'ffmpeg indisponivel', 'o pipeline nao corta video sem ele');
    failures++;
  }

  // ─── Resumo ────────────────────────────────────────────────────────────────
  console.log('');
  if (failures === 0) {
    console.log('Nenhum bloqueio encontrado.');
    console.log('Proximo passo: npm run check:models  (testa cada modelo contra a API)');
    process.exit(0);
  } else {
    console.log(`${failures} bloqueio(s) encontrado(s). Veja os [FALHA] acima.`);
    console.log('Os [aviso] nao impedem o app de subir — limitam o que ele testa.');
    process.exit(1);
  }
}

main().catch((error) => {
  console.error('erro inesperado:', error?.message || error);
  process.exit(2);
});
