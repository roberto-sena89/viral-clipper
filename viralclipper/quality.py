"""Post-render quality checks for short-form video exports.

The checks are deliberately best-effort: a missing optional face detector or
an FFmpeg diagnostic problem must never turn a successfully rendered clip
into a failed job.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path


def _check(key: str, label: str, status: str, message: str) -> dict:
    return {"key": key, "label": label, "status": status, "message": message}


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def inspect_clip(path: str | Path, config, *, caption_text: str = "") -> dict:
    """Inspect a rendered clip and return UI-ready Portuguese diagnostics."""
    checks: list[dict] = []
    path = Path(path)

    # File validity and stream metadata.
    if not path.is_file() or path.stat().st_size < 1024:
        checks.append(_check("file", "Arquivo", "error", "Arquivo ausente ou pequeno demais para ser um vídeo válido."))
        return {"status": "critical", "score": 0, "checks": checks}
    try:
        probe = _run([str(config.ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], 30)
        data = json.loads(probe.stdout) if probe.returncode == 0 else {}
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        duration = float((data.get("format") or {}).get("duration") or 0)
        if not video or duration <= 0 or int(video.get("width") or 0) <= 0 or int(video.get("height") or 0) <= 0:
            checks.append(_check("file", "Arquivo", "error", "O vídeo não contém uma faixa de imagem válida ou não tem duração legível."))
            return {"status": "critical", "score": 0, "checks": checks}
        width, height = int(video["width"]), int(video["height"])
        checks.append(_check("file", "Arquivo", "ok", f"Arquivo reproduzível · {width}×{height} · {duration:.1f}s."))
    except Exception as exc:  # diagnostics must not interrupt a finished render
        checks.append(_check("file", "Arquivo", "warning", f"Não foi possível validar o arquivo: {exc}"))
        return {"status": "attention", "score": 65, "checks": checks}

    # Decode once to inspect black frames, long silences, and audio level.
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    filters = ["blackdetect=d=1.0:pix_th=0.10"]
    argv = [str(config.ffmpeg), "-hide_banner", "-nostats", "-i", str(path), "-vf", filters[0]]
    if audio_stream:
        argv += ["-af", "silencedetect=noise=-40dB:d=1.5,volumedetect"]
    argv += ["-f", "null", "-"]
    diagnostics = ""
    try:
        decoded = _run(argv, max(120, min(600, int(duration * 5))))
        diagnostics = (decoded.stderr or "")
        if decoded.returncode:
            checks.append(_check("decode", "Integridade", "error", "O FFmpeg encontrou erro ao decodificar o vídeo."))
        else:
            checks.append(_check("decode", "Integridade", "ok", "Imagem e faixas de áudio decodificadas sem erro."))
        black = [float(v) for v in re.findall(r"black_duration:([0-9.]+)", diagnostics)]
        longest_black = max(black, default=0.0)
        checks.append(_check("black", "Tela preta", "warning" if longest_black >= 1.0 else "ok",
                             f"Trecho preto de {longest_black:.1f}s detectado." if longest_black >= 1.0 else "Nenhuma tela preta contínua acima de 1s."))
        if audio_stream:
            silences = [float(v) for v in re.findall(r"silence_duration:\s*([0-9.]+)", diagnostics)]
            longest_silence = max(silences, default=0.0)
            checks.append(_check("silence", "Silêncio", "warning" if longest_silence >= 1.5 else "ok",
                                 f"Silêncio de {longest_silence:.1f}s detectado; confira se é intencional." if longest_silence >= 1.5 else "Sem silêncio contínuo acima de 1,5s."))
            mean = re.findall(r"mean_volume:\s*(-?[0-9.]+)\s*dB", diagnostics)
            db = float(mean[-1]) if mean else None
            checks.append(_check("audio", "Nível de áudio", "warning" if db is not None and db < -28 else "ok",
                                 f"Volume médio {db:.1f} dBFS, abaixo do recomendado." if db is not None and db < -28 else (f"Volume médio {db:.1f} dBFS." if db is not None else "Não foi possível medir o volume médio.")))
        else:
            checks.append(_check("audio", "Áudio", "warning", "O arquivo não contém faixa de áudio."))
    except Exception as exc:
        checks.append(_check("decode", "Imagem e áudio", "warning", f"Análise automática incompleta: {exc}"))

    # Framing check: inspect actual rendered frames, so it reflects the crop
    # that will be exported rather than the landscape source.
    try:
        from . import reframe
        detector = reframe.build_detector()
        if detector is None:
            checks.append(_check("face", "Enquadramento", "skipped", "Detector facial indisponível; confira o rosto na prévia."))
        else:
            with tempfile.TemporaryDirectory(prefix="viralclipper-quality-") as temp:
                frames = reframe.sample_frames(str(config.ffmpeg), path, 0, duration, temp, count=7)
                detections = [detector.detect(frame) for frame in frames]
            faces = [box for frame in detections for box in frame]
            if not faces:
                checks.append(_check("face", "Enquadramento", "skipped", "Nenhum rosto detectado; confira o corte manualmente."))
            else:
                edge_hits = sum(1 for box in faces if box.x < .025 or box.x + box.width > .975 or box.y < .02 or box.y + box.height > .88)
                risky = edge_hits / len(faces) >= .35
                checks.append(_check("face", "Enquadramento", "warning" if risky else "ok",
                                     "Rosto muito próximo da borda; refaça com o quadro completo para preservar a pessoa." if risky else "Rosto detectado dentro da área visível."))
    except Exception as exc:
        checks.append(_check("face", "Enquadramento", "skipped", f"Não foi possível analisar rostos: {exc}"))

    # The renderer applies its social safe-area margin when burning captions.
    # This check reports that guarantee; long text is flagged for a visual pass.
    if getattr(config, "burn_captions", True):
        longest_word = max((len(word) for word in caption_text.split()), default=0)
        needs_review = longest_word > 28
        checks.append(_check("captions", "Legendas", "warning" if needs_review else "ok",
                             "Há palavra muito longa; confira se a legenda não ultrapassa a tela." if needs_review else "Margem inferior segura aplicada pelo renderizador; confira a prévia."))
    else:
        checks.append(_check("captions", "Legendas", "skipped", "Legendas desativadas para este corte."))

    warnings = sum(c["status"] == "warning" for c in checks)
    errors = sum(c["status"] == "error" for c in checks)
    return {"status": "critical" if errors else ("attention" if warnings else "ok"),
            "score": max(0, 100 - errors * 35 - warnings * 12), "checks": checks}
