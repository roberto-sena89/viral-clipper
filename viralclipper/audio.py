"""Audio extraction and energy/silence analysis.

Everything here works on a mono 16 kHz PCM WAV file decoded by ffmpeg. The
energy curve computed by :func:`frame_db` is used both as a scoring signal
(loud, dense speech tends to mark the interesting parts of a video) and to find
clean cut points.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import util
from .util import ClipperError, Logger

SILENCE_FLOOR = -80.0


@dataclass
class AudioAnalysis:
    """Result of the audio pass over one source file."""

    sample_rate: int
    duration: float
    hop_ms: int
    db: np.ndarray  # loudness in dBFS, one value per hop
    voiced: np.ndarray  # boolean mask, one value per hop
    silences: list[tuple[float, float]]
    threshold_db: float
    noise_floor_db: float
    speech_level_db: float

    def db_at(self, start: float, end: float) -> np.ndarray:
        """Slice the dB curve by time range (clamped to the file length)."""
        lo = self._index(start)
        hi = self._index(end)
        if hi <= lo:
            hi = min(len(self.db), lo + 1)
        return self.db[lo:hi]

    def energy(self, start: float, end: float) -> tuple[float, float]:
        """Return ``(mean, p95)`` loudness in dB for a time range."""
        window = self.db_at(start, end)
        if window.size == 0:
            return SILENCE_FLOOR, SILENCE_FLOOR
        return float(window.mean()), float(np.percentile(window, 95))

    def voiced_ratio(self, start: float, end: float) -> float:
        """Fraction of the range that carries speech-level audio."""
        lo = self._index(start)
        hi = self._index(end)
        if hi <= lo:
            return 0.0
        return float(self.voiced[lo:hi].mean())

    def silence_overlaps(self, start: float, end: float) -> list[tuple[float, float]]:
        """Silences overlapping the range, useful for jump cutting."""
        return [
            (max(start, s), min(end, e))
            for s, e in self.silences
            if e > start and s < end and (min(end, e) - max(start, s)) > 0.05
        ]

    def _index(self, seconds: float) -> int:
        step = self.hop_ms / 1000.0
        return int(max(0.0, seconds) / step)


def extract_audio(
    ffmpeg: str,
    source: str | Path,
    destination: str | Path,
    *,
    sample_rate: int = 16_000,
    logger: Logger | None = None,
) -> Path:
    """Decode any media file into a mono 16 kHz WAV file."""
    destination = Path(destination)
    util.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        logger=logger,
    )
    if not destination.exists():
        raise ClipperError(f"ffmpeg did not produce the audio file: {destination}")
    return destination


def load_wav_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV file as float32 in the ``[-1, 1]`` range."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ClipperError(f"Unsupported WAV sample width: {width * 8} bits")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(data), rate


def frame_db(samples: np.ndarray, sample_rate: int, hop_ms: int = 20) -> np.ndarray:
    """Compute loudness in dBFS per hop using a Hann-windowed RMS."""
    hop = max(1, int(sample_rate * hop_ms / 1000))
    win = max(hop, int(sample_rate * hop_ms * 2 / 1000))
    if samples.size == 0:
        return np.full(1, SILENCE_FLOOR, dtype=np.float32)

    frame_count = max(1, int(np.ceil(samples.size / hop)))
    padded = np.zeros(frame_count * hop + win, dtype=np.float32)
    padded[: samples.size] = samples
    window = np.hanning(win).astype(np.float32)
    norm = float(window.sum()) or 1.0

    power = np.empty(frame_count, dtype=np.float32)
    for index in range(frame_count):
        chunk = padded[index * hop : index * hop + win]
        power[index] = float(np.dot(chunk * chunk, window * window)) / norm
    db = 10.0 * np.log10(np.maximum(power, 1e-12))
    return db.astype(np.float32)


def adaptive_threshold(db: np.ndarray) -> tuple[float, float, float]:
    """Derive a silence threshold from the recording itself.

    Returns ``(threshold, noise_floor, speech_level)``. Videos vary a lot in
    overall gain, so a fixed dB value would either mark everything as silence
    or nothing at all.
    """
    if db.size == 0:
        return SILENCE_FLOOR, SILENCE_FLOOR, SILENCE_FLOOR
    noise_floor = float(np.percentile(db, 10))
    speech_level = float(np.percentile(db, 90))
    span = max(1.0, speech_level - noise_floor)
    threshold = noise_floor + 0.30 * span
    threshold = min(max(threshold, SILENCE_FLOOR + 5.0), speech_level - 3.0)
    return threshold, noise_floor, speech_level


def detect_silences(
    db: np.ndarray,
    hop_ms: int,
    threshold_db: float,
    min_silence: float,
) -> list[tuple[float, float]]:
    """Find silence ranges at least ``min_silence`` seconds long."""
    if db.size == 0:
        return []
    quiet = db < threshold_db
    min_hops = max(1, int(round(min_silence / (hop_ms / 1000.0))))
    ranges: list[tuple[float, float]] = []
    start: int | None = None

    for index, is_quiet in enumerate(quiet):
        if is_quiet and start is None:
            start = index
        elif not is_quiet and start is not None:
            if index - start >= min_hops:
                ranges.append((start * hop_ms / 1000.0, index * hop_ms / 1000.0))
            start = None
    if start is not None and quiet.size - start >= min_hops:
        ranges.append((start * hop_ms / 1000.0, quiet.size * hop_ms / 1000.0))
    return [(round(a, 3), round(b, 3)) for a, b in ranges]


def quiet_segments(
    analysis: AudioAnalysis,
    min_silence: float = 0.32,
    threshold_db: float | None = None,
) -> list[tuple[float, float]]:
    """Silences, or a single full-length range when the audio never goes quiet."""
    if threshold_db is None:
        threshold_db = analysis.threshold_db
    ranges = detect_silences(analysis.db, analysis.hop_ms, threshold_db, min_silence)
    if ranges:
        return ranges
    return [(0.0, round(analysis.duration, 3))]


def analyze_audio(
    wav_path: str | Path,
    *,
    hop_ms: int = 20,
    min_silence: float = 0.32,
    silence_db: float | None = None,
) -> AudioAnalysis:
    """Full audio pass: curve, voice mask, silences and thresholds."""
    samples, rate = load_wav_mono(wav_path)
    db = frame_db(samples, rate, hop_ms)
    if silence_db is None:
        threshold, noise_floor, speech_level = adaptive_threshold(db)
    else:
        threshold = float(silence_db)
        noise_floor = float(np.percentile(db, 10)) if db.size else SILENCE_FLOOR
        speech_level = float(np.percentile(db, 90)) if db.size else SILENCE_FLOOR

    voiced = db >= (threshold + 2.0)
    silences = detect_silences(db, hop_ms, threshold, min_silence)
    duration = samples.size / float(rate) if rate else 0.0
    return AudioAnalysis(
        sample_rate=rate,
        duration=duration,
        hop_ms=hop_ms,
        db=db,
        voiced=voiced,
        silences=silences,
        threshold_db=round(threshold, 2),
        noise_floor_db=round(noise_floor, 2),
        speech_level_db=round(speech_level, 2),
    )

