"""Import a transcript the user already has, instead of running whisper.

The pipeline's slowest stage is transcription. When the transcript is available
(YouTube's own caption panel, an .srt export, a copy/paste from a subtitle
site), importing it skips the model entirely and still feeds the scorer real
text with real timings.

Supported shapes:

* SRT blocks - ``1`` / ``00:00:05,000 --> 00:00:08,000`` / text;
* WebVTT - same with ``.`` instead of ``,`` and a ``WEBVTT`` header;
* pasted YouTube panel - a line with ``0:05`` (optionally ``[0:05]``), then the
  text either on the same line or the next one;
* plain prose - no timings at all; sentences are laid out on an estimated
  speech rate, which is the only honest thing to do with text that carries no
  clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .transcribe import Transcript, Word
from .util import ClipperError

# "0:05", "00:05", "1:02:03", "00:00:05,000", "00:00:05.000", "[00:05]".
_TIMESTAMP = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
_TIMESTAMP_RE = re.compile(rf"^{_TIMESTAMP}$")
_LEADING_TIMESTAMP_RE = re.compile(rf"^\[?{_TIMESTAMP}\]?\s*(.*)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

# Average narration speed used when the text has no timings. 2.6 words per
# second is a normal podcast/YouTube pace; the absolute timings matter far less
# than keeping the sentences evenly spaced and non-overlapping.
DEFAULT_WORDS_PER_SECOND = 2.6
# Shortest cue accepted; anything under this is a timing artifact.
MIN_CUE_SECONDS = 0.2
_CUE_GAP = 0.05


@dataclass
class Cue:
    """One subtitle line with its (possibly estimated) timing."""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def parse_timestamp(text: str) -> float | None:
    """Parse ``h:mm:ss,mmm`` / ``mm:ss`` / ``m:ss`` into seconds, else ``None``."""
    match = _TIMESTAMP_RE.match(text.strip().replace(" ", ""))
    if not match:
        return None
    hours, minutes, seconds, fraction = match.groups()
    total = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        total += int(fraction) / (10 ** len(fraction))
    return round(total, 3)


def _is_header(line: str) -> bool:
    upper = line.upper()
    return (
        upper.startswith("WEBVTT")
        or upper.startswith("NOTE")
        or upper.startswith("KIND:")
        or upper.startswith("LANGUAGE:")
    )


def _parse_cue_blocks(text: str) -> list[Cue]:
    """Parse the ``-->`` family (SRT, WebVTT)."""
    cues: list[Cue] = []
    buffer: list[str] = []
    start: float | None = None
    end: float | None = None

    def flush() -> None:
        nonlocal buffer, start, end
        if start is not None and end is not None and buffer:
            body = " ".join(" ".join(buffer).split())
            if body:
                cues.append(Cue(start=start, end=end, text=body))
        buffer = []
        start = None
        end = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if "-->" in line:
            flush()
            left, _, right = line.partition("-->")
            start = parse_timestamp(left)
            tail = right.strip().split()
            end = parse_timestamp(tail[0]) if tail else None
            if start is None or end is None:
                start = end = None
            continue
        if not line:
            flush()
            continue
        if start is None:
            # Block number, WEBVTT header or VTT metadata: not content.
            continue
        buffer.append(line)
    flush()
    return cues



def _parse_timestamp_lines(text: str) -> list[Cue]:
    """Parse the pasted-transcript shape: a timestamp, then its text."""
    cues: list[Cue] = []
    pending: float | None = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = _LEADING_TIMESTAMP_RE.match(line)
        if match:
            hours, minutes, seconds, fraction = match.groups()[:4]
            stamp = parse_timestamp(
                f"{hours + ':' if hours else ''}{minutes}:{seconds}"
                + (f".{fraction}" if fraction else "")
            )
            rest = (match.group(5) or "").strip(" -–—[]:")
            if stamp is None:
                continue
            if rest:
                cues.append(Cue(start=stamp, end=stamp, text=rest))
                pending = None
            else:
                pending = stamp
            continue
        if pending is not None:
            cues.append(Cue(start=pending, end=pending, text=line))
            pending = None
            continue
        if cues:
            # A wrapped line belongs to the cue above it.
            last = cues[-1]
            cues[-1] = Cue(start=last.start, end=last.end, text=f"{last.text} {line}".strip())
            continue
        # Text before any timestamp: this is not a timestamped transcript.
        return []
    return cues


def _parse_plain_text(text: str) -> list[Cue]:
    """Lay out prose without timings on an estimated speech rate."""
    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(" ".join(text.split()))]
    sentences = [part for part in sentences if part]
    cues: list[Cue] = []
    cursor = 0.0
    for sentence in sentences:
        words = max(1, len(sentence.split()))
        duration = max(MIN_CUE_SECONDS, words / DEFAULT_WORDS_PER_SECOND)
        cues.append(Cue(start=round(cursor, 3), end=round(cursor + duration, 3), text=sentence))
        cursor += duration + _CUE_GAP
    return cues


def _fill_missing_ends(cues: list[Cue]) -> list[Cue]:
    """Give every cue an end: the next start, or its own estimated duration."""
    fixed: list[Cue] = []
    for index, cue in enumerate(cues):
        end = cue.end
        if end <= cue.start:
            next_start = cues[index + 1].start if index + 1 < len(cues) else None
            if next_start is not None and next_start > cue.start:
                end = next_start
            else:
                words = max(1, len(cue.text.split()))
                end = cue.start + max(MIN_CUE_SECONDS, words / DEFAULT_WORDS_PER_SECOND)
        fixed.append(Cue(start=cue.start, end=round(end, 3), text=cue.text))
    return fixed


def _parse_any(raw: str) -> list[Cue]:
    """Parse into cues in the order they appear, without sorting or filling."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ClipperError("A transcricao informada esta vazia.")

    cues: list[Cue] = []
    if "-->" in text:
        cues = _parse_cue_blocks(text)
    if not cues:
        cues = _parse_timestamp_lines(text)
    if not cues:
        cues = _parse_plain_text(text)

    cues = [cue for cue in cues if cue.text.strip()]
    if not cues:
        raise ClipperError("Nao foi possivel extrair texto da transcricao informada.")
    return cues


def parse_transcript(raw: str) -> list[Cue]:
    """Parse any supported transcript shape into cues ordered by time."""
    cues = sorted(_parse_any(raw), key=lambda cue: cue.start)
    return _fill_missing_ends(cues)


def cues_to_words(cues: list[Cue]) -> list[Word]:
    """Split each cue into words whose durations cover the cue's span.

    Subtitle files have no word timings, so each word gets a slice of the cue
    proportional to its length. Sentence boundaries and the karaoke highlight
    only need the relative order inside the line, which this preserves exactly.
    """
    words: list[Word] = []
    for cue in cues:
        tokens = cue.text.split()
        if not tokens:
            continue
        weights = [len(token) + 1 for token in tokens]
        total = float(sum(weights))
        span = max(MIN_CUE_SECONDS, cue.duration)
        cursor = cue.start
        for token, weight in zip(tokens, weights):
            end = min(cue.end, cursor + span * (weight / total))
            if end - cursor < 0.05:
                end = cursor + 0.05
            words.append(Word(start=round(cursor, 3), end=round(end, 3), text=token))
            cursor = end
    words.sort(key=lambda word: word.start)
    return words


def load_transcript_file(path: str | Path) -> str:
    """Read a transcript file, tolerating the common encodings."""
    target = Path(path)
    if not target.is_file():
        raise ClipperError(f"Arquivo de transcricao nao encontrado: {target}")
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return target.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ClipperError(f"Nao foi possivel ler {target} com nenhuma codificacao conhecida.")


def transcript_from_text(
    raw: str,
    *,
    language: str = "manual",
    model_name: str = "transcricao-fornecida",
) -> tuple[Transcript, list[Cue]]:
    """Build the pipeline's :class:`Transcript` from pasted text.

    The text goes through :func:`normalize_transcript` first, so duplicates and
    broken fragments never reach the scorer. Returns the transcript plus the
    cues, because the viral report quotes whole sentences and the cue list is
    the cleanest source of those.
    """
    cues = normalize_transcript(raw).cues
    words = cues_to_words(cues)
    transcript = Transcript(
        language=language,
        language_probability=1.0,
        model_name=model_name,
        words=words,
    )
    return transcript, cues


def transcript_from_file(
    path: str | Path,
    *,
    language: str = "manual",
) -> tuple[Transcript, list[Cue]]:
    return transcript_from_text(load_transcript_file(path), language=language)


# --- normalization -----------------------------------------------------------
# What a user pastes is rarely clean: YouTube's panel repeats the last line
# while scrolling, a copy can split one sentence into two lines, and the
# timestamps can end up out of order after editing. Normalization repairs all
# of that and re-emits a canonical "minute aligned left, speech aligned right"
# layout that the parser accepts on the way back in.

_SENTENCE_END_RE = re.compile(r"[.!?…:;\"')\]]\s*$")
_PUNCT_BEFORE_RE = re.compile(r"\s+([,.;:!?…])")
_PUNCT_REPEAT_RE = re.compile(r"([,.;:!?…])\1+")


@dataclass
class NormalizedTranscript:
    """A cleaned transcript plus what changed while cleaning it."""

    normalized: str
    cues: list[Cue]
    duplicates_removed: int = 0
    fragments_merged: int = 0
    # How many cues changed position when the text was put back in time order.
    reordered: int = 0

    @property
    def word_count(self) -> int:
        return sum(len(cue.text.split()) for cue in self.cues)

    def to_dict(self) -> dict:
        return {
            "normalized": self.normalized,
            "cues": [
                {
                    "start": cue.start,
                    "end": cue.end,
                    "label": short_clock(cue.start),
                    "end_label": short_clock(cue.end),
                    "duration": round(cue.duration, 2),
                    "text": cue.text,
                }
                for cue in self.cues
            ],
            "stats": {
                "cues": len(self.cues),
                "words": self.word_count,
                "duplicates_removed": self.duplicates_removed,
                "fragments_merged": self.fragments_merged,
                "reordered": self.reordered,
            },
        }


def short_clock(seconds: float) -> str:
    """``m:ss`` (or ``h:mm:ss`` past an hour) - the shape YouTube shows."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def clean_cue_text(text: str) -> str:
    """Collapse whitespace, fix spacing around punctuation, start capitalized."""
    cleaned = " ".join(text.split())
    cleaned = _PUNCT_BEFORE_RE.sub(r"\1", cleaned)
    cleaned = _PUNCT_REPEAT_RE.sub(r"\1", cleaned)
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _dedupe(cues: list[Cue]) -> tuple[list[Cue], int]:
    """Drop consecutive repeats, keeping the widest time span."""
    kept: list[Cue] = []
    removed = 0
    for cue in cues:
        if kept and kept[-1].text.casefold() == cue.text.casefold():
            previous = kept[-1]
            kept[-1] = Cue(
                start=previous.start,
                end=max(previous.end, cue.end),
                text=previous.text,
            )
            removed += 1
            continue
        kept.append(cue)
    return kept, removed


# Fragment merging is deliberately strict. A pasted panel usually has no
# closing punctuation at all, so "ends without punctuation" alone would merge
# every pair of lines. The extra conditions keep it to the real case: a very
# short, adjacent line that clearly continues into the next one.
_FRAGMENT_MAX_SECONDS = 2.5
_FRAGMENT_GAP = 0.2


def _merge_fragments(cues: list[Cue]) -> tuple[list[Cue], int]:
    """Join a short line that clearly continues into the next one.

    Conditions, all required: the previous line is at most
    ``_FRAGMENT_MAX_SECONDS`` long, has no closing punctuation, the gap to the
    next line is at most ``_FRAGMENT_GAP``, and the next line starts lowercase.
    Lines without their own timestamp are already joined by the parser; this
    only repairs the fragments that kept a timestamp of their own.
    """
    kept: list[Cue] = []
    merged = 0
    for cue in cues:
        if kept:
            previous = kept[-1]
            continues = not _SENTENCE_END_RE.search(previous.text)
            starts_lower = cue.text[:1].islower()
            short = previous.duration <= _FRAGMENT_MAX_SECONDS
            if continues and starts_lower and short and (cue.start - previous.end) <= _FRAGMENT_GAP:
                kept[-1] = Cue(
                    start=previous.start,
                    end=max(previous.end, cue.end),
                    text=f"{previous.text} {cue.text}",
                )
                merged += 1
                continue
        kept.append(cue)
    return kept, merged


def format_cues(cues: list[Cue]) -> str:
    """Canonical layout: minute label left, speech right, columns aligned."""
    if not cues:
        return ""
    labels = [short_clock(cue.start) for cue in cues]
    width = max(len(label) for label in labels)
    return "\n".join(
        f"{label.ljust(width)}  {cue.text}" for label, cue in zip(labels, cues)
    )


def normalize_transcript(raw: str) -> NormalizedTranscript:
    """Clean, order and re-emit a pasted transcript.

    Order matters here: dedupe and fragment merging run on the *raw* text, so
    the "starts lowercase" signal that identifies a broken sentence is still
    there - capitalizing first would erase it. Capitalization and punctuation
    cleanup come last.
    """
    cues = _parse_any(raw)
    ordered = sorted(cues, key=lambda cue: cue.start)
    reordered = sum(1 for a, b in zip(cues, ordered) if a is not b)

    filled = _fill_missing_ends(ordered)
    deduped, duplicates = _dedupe(filled)
    merged_cues, merged = _merge_fragments(deduped)
    cleaned = [
        Cue(start=cue.start, end=cue.end, text=clean_cue_text(cue.text))
        for cue in merged_cues
    ]
    return NormalizedTranscript(
        normalized=format_cues(cleaned),
        cues=cleaned,
        duplicates_removed=duplicates,
        fragments_merged=merged,
        reordered=reordered,
    )

