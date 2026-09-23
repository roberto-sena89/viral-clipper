"""Builds speech units and ranks candidate clip windows.

A *unit* is a short, self-contained piece of speech (usually one sentence). A
*window* is a run of consecutive units that lasts between ``min_duration`` and
``max_duration`` seconds. Windows are scored on several signals and the best
non-overlapping ones become the final clips.

Scores are **absolute**: every signal maps to ``[0, 1]`` through a fixed,
video-independent rule (see :data:`_BOUNDED_SIGNALS` and
:data:`_BANDED_SIGNALS`), so a ``score`` of 62 means the same thing in every
video. That is what makes ``min_score`` a usable quality gate and what makes
weight tuning possible at all: a relative score always produces a "best clip",
even in a video that contains no good clip whatsoever.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import hooks
from .audio import AudioAnalysis
from .config import ClipConfig
from .transcribe import Transcript, Word

SENTENCE_ENDINGS = ".!?…"
SPLIT_PAUSE = 0.55
MAX_UNIT_SECONDS = 16.0
HARD_UNIT_SECONDS = 24.0

# Relative importance of every scoring signal.
WEIGHTS: dict[str, float] = {
    "hook_start": 1.00,
    "hook_peak": 0.70,
    "hook_density": 0.55,
    "question": 0.25,
    "speech": 0.60,
    "energy": 0.35,
    "energy_peak": 0.20,
    "boundary": 0.40,
    "length": 0.45,
    "clean": 0.25,
}

# Every signal except loudness is already absolute in ``[0, 1]`` by
# construction: ``hooks.score_text`` saturates with ``1 - exp(-total)``, the
# ratios are ratios and the flags are flags. Rescaling those across the
# candidate set of one video would make the final score *relative to the
# video*: a video with uniformly weak hooks would still produce a "score 95"
# clip, and two clips from different videos could never be compared. So the
# bounded signals are passed through untouched and only dB is banded.
_BOUNDED_SIGNALS: frozenset[str] = frozenset(
    {
        "hook_start",
        "hook_peak",
        "hook_density",
        "question",
        "speech",
        "boundary",
        "length",
        "clean",
    }
)

# Loudness is absolute dBFS, so a fixed band is the right mapping to 0..1.
_BANDED_SIGNALS: dict[str, tuple[float, float]] = {
    "energy": (-45.0, -12.0),
    "energy_peak": (-35.0, -8.0),
}

# Signals derived from transcript text. Without a transcript they are all zero
# (``build_units_from_audio`` produces textless units), which caps the score
# any window can reach: see :func:`max_score_without_transcript`.
_TEXT_SIGNALS: frozenset[str] = frozenset(
    {"hook_start", "hook_peak", "hook_density", "question"}
)


def max_score_without_transcript() -> float:
    """Highest score a window can reach when no transcript was produced.

    The ceiling is the share of the total weight carried by the signals that
    do not need words (~47 with the shipped weights). ``pipeline`` uses it to
    warn the moment transcription is lost that a ``--min-score`` above the
    ceiling makes the selection fail at the end of the run, instead of letting
    the user discover it only there.
    """
    weight_sum = sum(WEIGHTS.values()) or 1.0
    reachable = sum(weight for key, weight in WEIGHTS.items() if key not in _TEXT_SIGNALS)
    return reachable / weight_sum * 100.0


@dataclass
class Unit:
    """A short span of speech with its hook information."""

    start: float
    end: float
    text: str = ""
    word_count: int = 0
    hook_score: float = 0.0
    hook_terms: list[str] = field(default_factory=list)
    is_question: bool = False
    filler_ratio: float = 0.0
    gap_before: float = 0.0
    gap_after: float = 0.0
    index: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def words_per_second(self) -> float:
        if self.duration <= 0:
            return 0.0
        return self.word_count / self.duration


@dataclass
class Window:
    """A candidate clip: consecutive units inside the duration limits."""

    start: float
    end: float
    unit_start: int
    unit_end: int
    text: str
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    hook_terms: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _gap_before(analysis: AudioAnalysis, moment: float, lookback: float = 2.0) -> float:
    for start, end in analysis.silences:
        if 0.0 <= moment - end <= 0.30:
            return end - start
        if start <= moment <= end:
            return moment - start
    return 0.0


def _gap_after(analysis: AudioAnalysis, moment: float) -> float:
    for start, end in analysis.silences:
        if 0.0 <= start - moment <= 0.30:
            return end - start
        if start <= moment <= end:
            return end - moment
    return 0.0


def _finalize(unit: Unit, analysis: AudioAnalysis) -> Unit:
    unit.gap_before = round(_gap_before(analysis, unit.start), 3)
    unit.gap_after = round(_gap_after(analysis, unit.end), 3)
    return unit


def build_units_from_words(words: list[Word], analysis: AudioAnalysis) -> list[Unit]:
    """Group words into sentence-like units, splitting on punctuation/pauses."""
    units: list[Unit] = []
    bucket: list[Word] = []

    def flush() -> None:
        if not bucket:
            return
        text = " ".join(word.text for word in bucket).strip()
        result = hooks.score_text(text)
        units.append(
            _finalize(
                Unit(
                    start=bucket[0].start,
                    end=bucket[-1].end,
                    text=text,
                    word_count=len(bucket),
                    hook_score=result.score,
                    hook_terms=result.matched_terms,
                    is_question=result.is_question,
                    filler_ratio=round(hooks.filler_ratio(text), 4),
                ),
                analysis,
            )
        )
        bucket.clear()

    for position, word in enumerate(words):
        bucket.append(word)
        if not bucket:
            continue
        duration = bucket[-1].end - bucket[0].start
        next_word = words[position + 1] if position + 1 < len(words) else None
        pause = next_word.start - word.end if next_word else 99.0
        ends_sentence = word.text.rstrip().endswith(tuple(SENTENCE_ENDINGS))
        too_long = duration >= MAX_UNIT_SECONDS and pause >= 0.18
        oversized = duration >= HARD_UNIT_SECONDS
        unit_full = duration >= MAX_UNIT_SECONDS and len(bucket) >= 12
        if ends_sentence or pause >= SPLIT_PAUSE or too_long or oversized or unit_full:
            flush()
    flush()

    for index, unit in enumerate(units):
        unit.index = index
    return units



def build_units_from_audio(analysis: AudioAnalysis) -> list[Unit]:
    """Fallback units when no transcript is used: voiced runs split by silence.

    Without text there is no hook information, so scoring relies on energy,
    speech density and boundary quality only.
    """
    units: list[Unit] = []
    voiced = analysis.voiced
    hop = analysis.hop_ms / 1000.0
    index = 0
    total = len(voiced)
    while index < total:
        if not bool(voiced[index]):
            index += 1
            continue
        start_index = index
        while index < total and bool(voiced[index]):
            index += 1
        start = start_index * hop
        end = index * hop
        if end - start < 0.6:
            continue
        pieces = max(1, int(np.ceil((end - start) / MAX_UNIT_SECONDS)))
        step = (end - start) / pieces
        for piece in range(pieces):
            units.append(
                _finalize(
                    Unit(
                        start=round(start + piece * step, 3),
                        end=round(start + (piece + 1) * step, 3),
                    ),
                    analysis,
                )
            )
    for position, unit in enumerate(units):
        unit.index = position
    return units


def build_units(transcript: Transcript | None, analysis: AudioAnalysis) -> list[Unit]:
    """Prefer transcript units, fall back to audio-only units when empty."""
    if transcript is not None and not transcript.empty:
        units = build_units_from_words(transcript.words, analysis)
        if units:
            return units
    return build_units_from_audio(analysis)


def _normalize(raw: np.ndarray) -> np.ndarray:
    """Scale values to 0..1 using the 5th and 95th percentiles as bounds.

    This is a *relative* mapping and therefore only a last resort: it is kept
    for signals that have neither a natural ``[0, 1]`` range nor a known
    physical band. Every signal currently in :data:`WEIGHTS` picks one of the
    two absolute policies instead, so adding a new signal without declaring a
    policy degrades to relative scoring rather than crashing.
    """
    if raw.size == 0:
        return raw
    low = float(np.percentile(raw, 5))
    high = float(np.percentile(raw, 95))
    if high - low < 1e-9:
        return np.full_like(raw, 0.5, dtype=np.float64)
    return np.clip((raw - low) / (high - low), 0.0, 1.0)


def _band(value: float, low: float, high: float) -> float:
    if high - low < 1e-9:
        return 0.5
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))



def build_candidates(units: list[Unit], config: ClipConfig) -> list[Window]:
    """All unit runs whose duration fits inside the configured limits."""
    windows: list[Window] = []
    total = len(units)
    for first in range(total):
        for last in range(first, total):
            start = units[first].start
            end = units[last].end
            duration = end - start
            if duration < config.min_duration:
                continue
            if duration > config.max_duration:
                break
            slice_units = units[first : last + 1]
            terms: list[str] = []
            for unit in slice_units:
                for term in unit.hook_terms:
                    if term.lower() not in {item.lower() for item in terms}:
                        terms.append(term)
            windows.append(
                Window(
                    start=round(start, 3),
                    end=round(end, 3),
                    unit_start=first,
                    unit_end=last,
                    text=" ".join(unit.text for unit in slice_units).strip(),
                    hook_terms=terms[:8],
                )
            )
    return windows


def score_windows(
    candidates: list[Window],
    units: list[Unit],
    analysis: AudioAnalysis,
    config: ClipConfig,
) -> None:
    """Fill ``score`` and ``components`` on every candidate, in place."""
    if not candidates:
        return
    count = len(candidates)
    raw: dict[str, np.ndarray] = {key: np.zeros(count, dtype=np.float64) for key in WEIGHTS}
    length_span = max(1.0, (config.max_duration - config.min_duration) / 2.0)

    for index, window in enumerate(candidates):
        slice_units = units[window.unit_start : window.unit_end + 1]
        if not slice_units:
            continue
        first_text = " ".join(slice_units[0].text.split()[:12])
        raw["hook_start"][index] = hooks.opening_bonus(first_text)
        raw["hook_peak"][index] = max(unit.hook_score for unit in slice_units)
        total_duration = sum(unit.duration for unit in slice_units) or 1.0
        raw["hook_density"][index] = (
            sum(unit.hook_score * unit.duration for unit in slice_units) / total_duration
        )
        raw["question"][index] = 1.0 if slice_units[-1].is_question else 0.0
        raw["speech"][index] = analysis.voiced_ratio(window.start, window.end)
        mean_db, peak_db = analysis.energy(window.start, window.end)
        raw["energy"][index] = mean_db
        raw["energy_peak"][index] = peak_db
        raw["boundary"][index] = (0.5 if slice_units[0].gap_before >= 0.25 else 0.0) + (
            0.5 if slice_units[-1].gap_after >= 0.25 else 0.0
        )
        raw["length"][index] = 1.0 - min(
            1.0, abs(window.duration - config.target_duration) / length_span
        )
        filler = sum(unit.filler_ratio for unit in slice_units) / len(slice_units)
        raw["clean"][index] = 1.0 - min(1.0, filler * 3.0)

    normalized: dict[str, np.ndarray] = {}
    for key, values in raw.items():
        if key in _BANDED_SIGNALS:
            low, high = _BANDED_SIGNALS[key]
            normalized[key] = np.array([_band(value, low, high) for value in values])
        elif key in _BOUNDED_SIGNALS:
            normalized[key] = np.clip(values, 0.0, 1.0)
        else:  # pragma: no cover - a new signal must declare a policy
            normalized[key] = _normalize(values)

    weight_sum = sum(WEIGHTS.values()) or 1.0
    for index, window in enumerate(candidates):
        components = {key: round(float(values[index]), 4) for key, values in normalized.items()}
        components.update(
            {f"raw_{key}": round(float(values[index]), 3) for key, values in raw.items()}
        )
        window.components = components
        total = sum(WEIGHTS[key] * components[key] for key in WEIGHTS) / weight_sum
        window.score = round(total * 100.0, 2)


def pick_windows(candidates: list[Window], config: ClipConfig) -> list[Window]:
    """Apply the quality gate and keep the best non-overlapping windows.

    Split from :func:`rank_windows` so an optional precision stage (the
    semantic ranker) can rewrite ``window.score`` between scoring and picking
    without having to reimplement the selection rules.
    """
    eligible = (
        candidates
        if config.min_score <= 0.0
        else [window for window in candidates if window.score >= config.min_score]
    )
    ordered = sorted(eligible, key=lambda window: window.score, reverse=True)
    chosen: list[Window] = []
    for window in ordered:
        if len(chosen) >= config.count:
            break
        if any(_too_close(window, taken, config.min_gap) for taken in chosen):
            continue
        chosen.append(window)
    chosen.sort(key=lambda window: window.start)
    return chosen


def rank_windows(
    candidates: list[Window],
    units: list[Unit],
    analysis: AudioAnalysis,
    config: ClipConfig,
) -> list[Window]:
    """Score every candidate and pick the best non-overlapping ones.

    ``config.min_score`` is an absolute gate: because the score is comparable
    between videos, a clip below the floor is dropped instead of being
    force-fed to the output. With the default of ``0.0`` nothing is filtered
    and the best ``count`` windows are always returned.
    """
    score_windows(candidates, units, analysis, config)
    return pick_windows(candidates, config)


def _too_close(candidate: Window, taken: Window, min_gap: float) -> bool:
    return candidate.start < taken.end + min_gap and taken.start < candidate.end + min_gap
