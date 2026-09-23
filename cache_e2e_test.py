"""End to end check for the transcript cache (needs ffmpeg, no network).

Builds a synthetic WAV, runs the real whisper transcription twice through
``pipeline._transcribe_cached`` and asserts the second run is served from the
cache. Also verifies that changing a transcription setting misses the cache and
that ``--no-transcript-cache`` neither reads nor writes.

    python cache_e2e_test.py                 # model 'tiny'
    python cache_e2e_test.py --model small
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from viralclipper import pipeline, transcript_cache, util
from viralclipper.config import ClipConfig
from viralclipper.util import Logger


def build_wav(path: Path, seconds: float = 20.0, rate: int = 16_000) -> Path:
    """A tone burst train so whisper has something to transcribe."""
    positions = np.arange(int(seconds * rate), dtype=np.float64) / rate
    tone = 0.2 * np.sin(2 * np.pi * 180 * positions)
    gated = np.where((positions % 3.0) < 2.0, tone, 0.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((gated * 32767).astype("<i2").tobytes())
    return path


def run_once(wav: Path, work: Path, config: ClipConfig, log: Logger, label: str):
    start = time.perf_counter()
    transcript = pipeline._transcribe_cached(wav, "e2e-fixture", config, work, log)
    elapsed = time.perf_counter() - start
    print(f"  {label}: {len(transcript.words)} words in {elapsed:.2f}s")
    return transcript, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="tiny")
    options = parser.parse_args()

    log = Logger()
    tmp = Path(tempfile.mkdtemp(prefix="vc_cache_e2e_"))
    wav = build_wav(tmp / "analysis.wav")
    work = tmp / "work"
    cache_root = work / transcript_cache.DEFAULT_CACHE_DIR
    print("wav:", wav.stat().st_size, "bytes")

    base = ClipConfig(
        url="local://fixture",
        whisper_model=options.model,
        whisper_device="cpu",
        whisper_compute_type="int8",
        language="pt",
    )
    base.ffmpeg, base.ffprobe = util.ffmpeg_binaries()

    print("run 1 (cold):")
    first, cold = run_once(wav, work, base, log, "cold")
    entries = list(cache_root.glob("*.json"))
    assert entries, "no cache entry written"
    print("  cache entries:", [entry.name[:12] + "..." for entry in entries])

    print("run 2 (warm):")
    second, warm = run_once(wav, work, base, log, "warm")
    assert len(second.words) == len(first.words), "cache changed the word count"
    assert warm < cold, f"cache hit was not faster ({warm:.2f}s vs {cold:.2f}s)"

    print("run 3 (different model: must miss):")
    other = ClipConfig(**{**base.__dict__, "whisper_model": "base"})
    other.ffmpeg, other.ffprobe = base.ffmpeg, base.ffprobe
    key_other = transcript_cache.cache_key("e2e-fixture", wav, other)
    assert not (cache_root / f"{key_other}.json").exists(), "different model reused the cache"
    print("  ok, distinct key:", key_other[:12] + "...")

    print("run 4 (cache disabled: must not read or write):")
    off = ClipConfig(**{**base.__dict__, "transcript_cache": False})
    off.ffmpeg, off.ffprobe = base.ffmpeg, base.ffprobe
    before = len(list(cache_root.glob("*.json")))
    _, off_elapsed = run_once(wav, work, off, log, "disabled")
    after = len(list(cache_root.glob("*.json")))
    assert before == after, "disabled cache still wrote an entry"
    assert off_elapsed >= warm, "disabled cache appears to have read an entry"

    removed = transcript_cache.clear(cache_root)
    print("cleared:", removed, "entry(ies); remaining:",
          len(list(cache_root.glob("*.json"))))
    shutil.rmtree(tmp, ignore_errors=True)
    print("CACHE E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
