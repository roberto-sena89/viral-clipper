"""Shared fixtures for the viral-clipper unit tests.

The helpers build the lightweight data objects (``AudioAnalysis``, ``Unit``,
``Word``, ``ClipConfig``) directly, so the scoring and hook tests never need
ffmpeg, yt-dlp or a whisper model.
"""

from __future__ import annotations

import numpy as np

from viralclipper.audio import AudioAnalysis
from viralclipper.config import ClipConfig
from viralclipper.score import Unit
from viralclipper.transcribe import Transcript, Word


def make_analysis(
    duration: float = 120.0,
    *,
    hop_ms: int = 20,
    db_value: float = -20.0,
    silences: list[tuple[float, float]] | tuple[tuple[float, float], ...] = (),
) -> AudioAnalysis:
    """Build a synthetic analysis with a flat loudness curve.

    Every hop is voiced and carries the same dB value, which makes the derived
    scoring signals easy to predict in the assertions.
    """
    step = hop_ms / 1000.0
    count = max(1, int(round(duration / step)))
    db = np.full(count, float(db_value), dtype=np.float32)
    voiced = np.ones(count, dtype=bool)
    return AudioAnalysis(
        sample_rate=16_000,
        duration=duration,
        hop_ms=hop_ms,
        db=db,
        voiced=voiced,
        silences=[(float(a), float(b)) for a, b in silences],
        threshold_db=-30.0,
        noise_floor_db=-45.0,
        speech_level_db=-15.0,
    )


def make_unit(
    start: float,
    end: float,
    *,
    text: str = "",
    hook_score: float = 0.0,
    hook_terms: list[str] | None = None,
) -> Unit:
    """Build a ``Unit`` without going through the word grouping stage."""
    return Unit(
        start=start,
        end=end,
        text=text,
        word_count=len(text.split()),
        hook_score=hook_score,
        hook_terms=list(hook_terms or []),
    )


def make_word(start: float, end: float, text: str) -> Word:
    return Word(start=start, end=end, text=text)


def make_transcript(words: list[Word], language: str = "pt") -> Transcript:
    return Transcript(
        language=language,
        language_probability=0.99,
        model_name="fake",
        words=list(words),
    )


def make_config(**overrides) -> ClipConfig:
    payload = {"url": "https://youtu.be/fixture"}
    payload.update(overrides)
    return ClipConfig(**payload)
