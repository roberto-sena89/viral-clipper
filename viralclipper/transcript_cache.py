"""On-disk cache for whisper transcripts.

Transcription is by far the slowest stage of the pipeline, and the same video
is routinely processed more than once while tuning ``--min``, ``--max`` or the
caption options. The cache key is derived from everything that can change the
produced words, so a differently configured run never reads a stale entry:

    cache key = sha256(
        source video id
        + whisper model, language, beam size, VAD flag, device, compute type
        + sha1 of the analysed WAV file
        + cache format version
    )

The WAV digest covers the actual audio, which keeps two different videos that
share a yt-dlp id (or a re-encoded download) from colliding. Entries are plain
JSON files named ``<key>.json``, so they stay inspectable and portable.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config import ClipConfig
from .transcribe import Transcript, Word
from .util import ClipperError, Logger

CACHE_VERSION = 1
DEFAULT_CACHE_DIR = Path("cache") / "transcripts"
# Refuse to hash a file larger than this: the digest pass would cost more than
# it saves, and a WAV that big is not a normal single video's audio track.
MAX_HASH_BYTES = 2 * 1024 * 1024 * 1024

_HEADER_KEYS = (
    "cache_version",
    "key",
    "source_id",
    "model",
    "language",
    "beam_size",
    "vad_filter",
    "device",
    "compute_type",
    "audio_sha1",
    "audio_bytes",
    "created_at",
    "word_count",
)


@dataclass
class CacheInfo:
    """Where the cache lives and whether this run may use it."""

    directory: Path
    enabled: bool
    reason: str = ""


def _audio_fingerprint(wav: Path) -> tuple[str, int]:
    """Return ``(sha1 or size/mtime marker, byte size)`` for the analysed WAV."""
    try:
        size = wav.stat().st_size
    except OSError as exc:
        raise ClipperError(f"Could not read the analysis audio at {wav}: {exc}") from exc

    if size > MAX_HASH_BYTES:
        # Too large to hash cheaply: fall back to size plus mtime and mark the
        # value as unhashed so the entry stays honest about it.
        return f"unhashed:{size}:{int(wav.stat().st_mtime)}", size

    digest = hashlib.sha1()
    with wav.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest(), size


def cache_key(
    source_id: str,
    wav_path: str | Path,
    config: ClipConfig,
    model: str | None = None,
) -> str:
    """Build the deterministic cache key for one transcription request.

    ``model`` is the checkpoint that actually loaded. It defaults to the
    configured one, but the loader may step down to a smaller checkpoint when
    the machine is short on memory, and an entry produced by a fallback must not
    be served to a later run that asks for the full size model.
    """
    audio_sha1, size = _audio_fingerprint(Path(wav_path))
    payload = "|".join(
        [
            f"v{CACHE_VERSION}",
            str(source_id or ""),
            str(model or config.whisper_model),
            str(config.language or "auto"),
            str(config.beam_size),
            str(bool(config.vad_filter)),
            str(config.whisper_device),
            str(config.whisper_compute_type),
            audio_sha1,
            str(size),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_cache(
    config: ClipConfig,
    logger: Logger | None = None,
) -> CacheInfo:
    """Decide which cache directory a run uses.

    ``config.cache_dir`` disables the cache, uses the given directory, or
    defaults to ``output/cache/transcripts`` — *outside* the work directory, so
    it survives the end-of-run cleanup.

    That default used to be ``work/cache/transcripts``, and ``cli.py`` removes
    the whole work directory in its ``finally``. The cache was written and then
    destroyed in the same run, so every run of the same video re-transcribed —
    the single most expensive step of the pipeline. The docstring here already
    described the correct behaviour; the code simply never implemented it.
    """
    if not config.transcript_cache:
        if logger:
            logger.debug("Transcript cache disabled for this run")
        return CacheInfo(directory=Path(), enabled=False, reason="--no-transcript-cache")

    configured = getattr(config, "cache_dir", None)
    directory = (
        Path(configured)
        if configured is not None
        else Path(config.output_dir) / DEFAULT_CACHE_DIR
    )
    return CacheInfo(directory=directory, enabled=True)


def load(
    directory: str | Path,
    key: str,
    logger: Logger | None = None,
) -> Transcript | None:
    """Read a cached transcript, or return ``None`` when it is unusable.

    A malformed or partially written entry is treated as a miss and removed,
    never as a hard failure: the run simply transcribes again.
    """
    path = Path(directory) / f"{key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for field in _HEADER_KEYS:
            if field not in payload:
                raise ValueError(f"missing field '{field}'")
        if int(payload["cache_version"]) != CACHE_VERSION:
            raise ValueError("cache format version changed")
        if str(payload["key"]) != key:
            raise ValueError("cache key mismatch")
        words = [
            Word(
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item["text"]),
                probability=float(item.get("probability", 1.0)),
            )
            for item in payload["words"]
        ]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        if logger:
            logger.warn(f"Ignoring unusable transcript cache entry {path.name}: {exc}")
        try:
            path.unlink()
        except OSError:
            pass
        return None

    transcript = Transcript(
        language=str(payload.get("detected_language") or "unknown"),
        language_probability=float(payload["language_probability"]),
        model_name=str(payload["model_name"]),
        words=words,
    )
    if logger:
        logger.ok(f"Transcript cache hit ({len(words)} words, key {key[:12]}...)")
    return transcript


def save(
    directory: str | Path,
    key: str,
    transcript: Transcript,
    *,
    source_id: str,
    wav_path: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
) -> Path | None:
    """Write a transcript to the cache.

    The entry is written to a temporary file and then moved into place, so a
    reader can never observe a half written JSON document. Failures here are
    never fatal: an unwritable cache must not break an otherwise good run.
    """
    path = Path(directory) / f"{key}.json"
    try:
        audio_bytes = Path(wav_path).stat().st_size
    except OSError:
        audio_bytes = 0

    payload = {
        "cache_version": CACHE_VERSION,
        "key": key,
        "source_id": str(source_id or ""),
        "model": str(config.whisper_model),
        # The requested language (``auto`` when unset) is what keys the cache;
        # the DETECTED language lives in the transcript fields below.
        "language": str(config.language or "auto"),
        "detected_language": str(transcript.language),
        "beam_size": int(config.beam_size),
        "vad_filter": bool(config.vad_filter),
        "device": str(config.whisper_device),
        "compute_type": str(config.whisper_compute_type),
        # The WAV digest is already folded into the key; this field carries the
        # leading part of it so two entries stay distinguishable by eye.
        "audio_sha1": key[:16],
        "audio_bytes": audio_bytes,
        "created_at": round(time.time(), 3),
        "word_count": len(transcript.words),
        "language_probability": float(transcript.language_probability),
        "model_name": str(transcript.model_name),
        "words": [
            {
                "start": word.start,
                "end": word.end,
                "text": word.text,
                "probability": word.probability,
            }
            for word in transcript.words
        ],
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        if logger:
            logger.warn(f"Could not write the transcript cache: {exc}")
        return None

    if logger:
        logger.debug(f"Cached transcript to {path}")
    return path


def clear(directory: str | Path, logger: Logger | None = None) -> int:
    """Delete every cached transcript in ``directory``. Returns the count."""
    target = Path(directory)
    if not target.exists():
        return 0
    removed = 0
    for entry in target.glob("*.json"):
        try:
            entry.unlink()
            removed += 1
        except OSError:
            continue
    if logger:
        logger.ok(f"Removed {removed} cached transcript(s) from {target}")
    return removed


def describe(directory: str | Path) -> list[dict]:
    """Return a small summary of every cache entry, newest first."""
    target = Path(directory)
    if not target.exists():
        return []
    entries: list[dict] = []
    for entry in target.glob("*.json"):
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append(
            {
                "file": entry.name,
                "source_id": payload.get("source_id", ""),
                "model": payload.get("model", ""),
                "language": payload.get("language", ""),
                "word_count": payload.get("word_count", 0),
                "created_at": payload.get("created_at", 0.0),
                "bytes": entry.stat().st_size,
            }
        )
    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries
