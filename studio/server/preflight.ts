/**
 * Preflight do ambiente do viral-clipper.
 *
 * Descobertas que motivam cada checagem (medidas nesta máquina, não supostas):
 *  - o pacote `viralclipper` NÃO está instalado no venv; ele é encontrado via
 *    sys.path[0] = cwd. Rodar `-m viralclipper` de fora da raiz do repo falha
 *    com exit 1 e sem mensagem útil. Por isso o cwd é obrigatório e aqui é
 *    validado antes de liberar o chat;
 *  - `--version` não existe no CLI, então a checagem é feita por import direto;
 *  - a detecção de rosto é o **Haar cascade do OpenCV**, não mediapipe (o
 *    pipeline nunca importou mediapipe — verificado por grep). O risco real é
 *    outro: `opencv-python` 5.x removeu `CascadeClassifier`, e nesse caso
 *    `--layout focus` cai para centro SILENCIOSAMENTE. O preflight expõe isso
 *    para o agente poder avisar o usuário em vez de prometer algo que não
 *    acontece.
 */

import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

export interface PreflightReport {
  ok: boolean;
  root: string;
  python: string;
  checks: PreflightCheck[];
  /** Capacidades que mudam o que o agente pode prometer. */
  capabilities: {
    faceDetection: boolean;
    gpu: boolean;
    ffmpeg: boolean;
  };
  errors: string[];
}

export interface PreflightCheck {
  name: string;
  ok: boolean;
  detail: string;
  /** Fatal = sem isso nada roda. Não-fatal = degrada, mas funciona. */
  fatal: boolean;
}

/** Resolve o Python do projeto: explícito no .env, senão o venv ao lado da raiz. */
export function resolvePython(root: string): string {
  const explicit = process.env.VIRCAL_PYTHON?.trim();
  if (explicit) return explicit;
  const venv = path.join(root, '.venv', 'Scripts', 'python.exe');
  if (existsSync(venv)) return venv;
  const posixVenv = path.join(root, '.venv', 'bin', 'python');
  if (existsSync(posixVenv)) return posixVenv;
  return 'python';
}

export function resolveRoot(): string {
  const configured = process.env.VIRCAL_ROOT?.trim();
  if (configured) return path.resolve(configured);
  // O Studio vive dentro do repo (studio/), então a raiz é o diretório pai.
  return path.resolve(process.cwd(), '..');
}

async function run(
  file: string,
  args: string[],
  options: { cwd?: string; timeout?: number } = {},
): Promise<{ ok: boolean; stdout: string; stderr: string }> {
  try {
    const { stdout, stderr } = await execFileAsync(file, args, {
      cwd: options.cwd,
      timeout: options.timeout ?? 30_000,
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    });
    return { ok: true, stdout: String(stdout), stderr: String(stderr) };
  } catch (error) {
    const err = error as { stdout?: string; stderr?: string; message?: string };
    return {
      ok: false,
      stdout: String(err.stdout ?? ''),
      stderr: String(err.stderr ?? err.message ?? ''),
    };
  }
}

/**
 * Roda todas as checagens. Nunca lança: devolve o relatório e deixa a decisão
 * de bloquear para quem chamou.
 */
export async function runPreflight(): Promise<PreflightReport> {
  const root = resolveRoot();
  const python = resolvePython(root);
  const checks: PreflightCheck[] = [];
  const errors: string[] = [];

  // 1. Raiz do repo -------------------------------------------------------
  const packageDir = path.join(root, 'viralclipper');
  const rootOk = existsSync(path.join(packageDir, '__main__.py'));
  checks.push({
    name: 'Raiz do repositório',
    ok: rootOk,
    detail: rootOk
      ? root
      : `${root} não contém viralclipper/__main__.py. Ajuste VIRCAL_ROOT no .env.`,
    fatal: true,
  });
  if (!rootOk) errors.push(`Raiz inválida: ${root}`);

  // 2. Interpretador existe ----------------------------------------------
  const pythonOk = python === 'python' ? true : existsSync(python);
  checks.push({
    name: 'Interpretador Python',
    ok: pythonOk,
    detail: pythonOk
      ? python
      : `${python} não existe. Rode o instalador ou defina VIRCAL_PYTHON no .env.`,
    fatal: true,
  });
  if (!pythonOk) errors.push(`Python não encontrado: ${python}`);

  // 3. Dependências importáveis ------------------------------------------
  let faceDetection = false;
  let gpu = false;
  if (rootOk && pythonOk) {
    const imported = await run(
      python,
      [
        '-c',
        'import viralclipper, faster_whisper, yt_dlp; ' +
          'print("core ok"); ' +
          'from importlib.util import find_spec; ' +
          // A deteccao de rosto NAO e mediapipe: e o Haar cascade do OpenCV.
          // Perguntar pelo modulo errado dava falso negativo — reportava
          // "AUSENTE" com a deteccao funcionando (o cascade existe e acha
          // rosto de verdade). A fonte de verdade e o proprio pipeline:
          // reframe.available_backend() so devolve algo se o cascade estiver
          // no lugar. Ver viralclipper/reframe.py.
          'from viralclipper import reframe; ' +
          'backend = reframe.available_backend(); ' +
          'print("face:", backend or ""); ' +
          'print("focus_works:", bool(backend)); ' +
          'print("cuda:", bool(find_spec("torch")))',
      ],
      { cwd: root },
    );

    // Este é o teste que falha com exit 1 quando o cwd está errado.
    const coreOk = imported.ok && imported.stdout.includes('core ok');
    checks.push({
      name: 'Pacote + dependências (cwd = raiz)',
      ok: coreOk,
      detail: coreOk
        ? 'viralclipper, faster_whisper e yt_dlp importam a partir da raiz.'
        : `Falhou ao importar com cwd=${root}. ${imported.stderr.trim().split('\n').slice(-3).join(' | ')}`,
      fatal: true,
    });
    if (!coreOk) {
      errors.push(
        'Import falhou. O pacote viralclipper não está instalado: ele precisa ser ' +
          'executado com o diretório de trabalho na raiz do repositório.',
      );
    }

    const backendMatch = imported.stdout.match(/face:\s*(\S+)/);
    const backend = backendMatch?.[1] || '';
    faceDetection = /focus_works:\s*True/.test(imported.stdout);
    gpu = /cuda:\s*True/.test(imported.stdout);

    checks.push({
      name: 'Detecção de rosto (Haar cascade do OpenCV)',
      ok: faceDetection,
      detail: faceDetection
        ? `Disponível (backend: ${backend}). --layout focus reenquadra de verdade.`
        : 'AUSENTE. --layout focus cai para centro silenciosamente. ' +
          'Causa provável: opencv-python 5.x removeu o CascadeClassifier — ' +
          'corrija com: pip install "opencv-python-headless<5"',
      fatal: false,
    });
    checks.push({
      name: 'Aceleração por GPU',
      ok: gpu,
      detail: gpu
        ? 'Torch presente: transcrição pode usar GPU.'
        : 'Sem torch/CUDA: Whisper roda em CPU (int8). Vídeo longo demora.',
      fatal: false,
    });
  }

  // 4. ffmpeg -------------------------------------------------------------
  const ffmpeg = await run('ffmpeg', ['-version'], { timeout: 15_000 });
  const ffmpegOk = ffmpeg.ok || ffmpeg.stdout.includes('ffmpeg version');
  checks.push({
    name: 'ffmpeg',
    ok: ffmpegOk,
    detail: ffmpegOk
      ? (ffmpeg.stdout.split('\n')[0] || 'disponível').trim()
      : 'Não encontrado no PATH. O render dos cortes não funciona sem ele.',
    fatal: true,
  });
  if (!ffmpegOk) errors.push('ffmpeg não está no PATH.');

  return {
    ok: errors.length === 0,
    root,
    python,
    checks,
    capabilities: { faceDetection, gpu, ffmpeg: ffmpegOk },
    errors,
  };
}
