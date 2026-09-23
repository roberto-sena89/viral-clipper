"""yt-dlp wrappers: metadata, audio-only pass and per-section downloads.

Downloads run as ``python -m yt_dlp`` so the tool works even when the yt-dlp
console script is not on PATH. Sections are downloaded with a safety padding
and then cut precisely by ffmpeg, which avoids depending on the encoder GOP
boundaries of the source video.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import util
from .config import ClipConfig
from .util import ClipperError, Logger

SECTION_PADDING = 4.0

_YTDLP_ERROR = re.compile(r"^ERROR:\s*(.+)$", re.MULTILINE)

# yt-dlp reports the cause; the fix is the tool's job. Each entry is
# (fragment matched case-insensitively, what to tell the user).
_HINTS: tuple[tuple[str, str], ...] = (
    (
        "sign in to confirm your age",
        "The video is age restricted. Retry with --cookies-from-browser chrome "
        "(or firefox, edge, ...).",
    ),
    (
        "confirm you're not a bot",
        "YouTube is challenging the request. Retry with "
        "--cookies-from-browser chrome.",
    ),
    (
        "unavailable",
        "The video is unavailable: it may be private, deleted, region locked "
        "or a premiere that has not started.",
    ),
    (
        "private video",
        "The video is private. Retry with --cookies-from-browser chrome and an "
        "account that can watch it.",
    ),
    (
        "members-only",
        "The video is members-only. Retry with --cookies-from-browser chrome "
        "and a subscribed account.",
    ),
    (
        "this live event has ended",
        "The live event has ended and no recording is available.",
    ),
    (
        "requested format is not available",
        "The requested format is not offered. Try a lower --max-height.",
    ),
    (
        "unsupported url",
        "That URL is not supported. Pass a single video URL, not a channel or "
        "playlist page.",
    ),
    (
        "no video formats found",
        "No downloadable format was found for this URL.",
    ),
    ("http error 429", "YouTube is rate limiting. Wait a few minutes and retry."),
    (
        "unable to download video data",
        "The download was interrupted before finishing. Retry; if it keeps "
        "happening, pass --cookies-from-browser chrome.",
    ),
    (
        "http error 403",
        "YouTube refused the request (403). Wait a few minutes and retry, or pass "
        "--cookies-from-browser chrome.",
    ),
    ("giving up after", "yt-dlp gave up after repeated attempts. Check the connection and retry."),
    ("timed out", "The connection timed out. Check the connection and retry."),
    ("connection reset", "The connection was reset mid-download. Retry."),
    ("no space left", "The disk is full. Free space and retry."),
    (
        "ffmpeg exited with code",
        "yt-dlp failed to cut a section with ffmpeg. Retry, or use "
        "--download-mode full to download the whole video at once.",
    ),
    ("unable to download webpage", "Could not reach the site. Check the connection."),
)


def explain_failure(output: str, url: str = "") -> str:
    """Turn yt-dlp's failure output into one actionable sentence.

    yt-dlp prints the reason on an ``ERROR:`` line and, with
    ``--dump-single-json``, also dumps a literal ``null`` to stdout. Dumping the
    raw command and its output buries the one line that matters, so the cause is
    extracted and paired with the flag that fixes it.
    """
    messages = [match.strip() for match in _YTDLP_ERROR.findall(output or "")]
    if not messages:
        # Keep whatever is left once the JSON noise is gone.
        leftovers = [
            line.strip()
            for line in (output or "").splitlines()
            if line.strip() and line.strip() not in {"null", "{", "}"}
        ]
        cause = leftovers[-1] if leftovers else "yt-dlp failed without a message"
    else:
        cause = messages[-1]

    lowered = cause.lower()
    for fragment, hint in _HINTS:
        if fragment in lowered:
            return f"{cause} {hint}"
    target = f" for {url}" if url else ""
    return f"yt-dlp failed{target}: {cause}"


def _base_args(config: ClipConfig) -> list[str]:
    args = ["--no-playlist", "--no-warnings", "--retries", "5", "--fragment-retries", "5"]
    if config.cookies_from_browser:
        args += ["--cookies-from-browser", config.cookies_from_browser]
    args += list(config.extra_ytdlp_args)
    return args


def _ytdlp(*args: str) -> list[str]:
    return util.python_module_command("yt_dlp", *args)


def _run_captured(cmd: list[str], url: str, logger: Logger | None) -> str:
    """Run a yt-dlp command, raising a clean error instead of a command dump."""
    proc = util.run(cmd, logger=logger, check=False)
    if proc.returncode != 0:
        raise ClipperError(explain_failure(proc.stdout or "", url))
    return proc.stdout or ""


def _run_visible(cmd: list[str], url: str, logger: Logger | None) -> None:
    """Run a download with progress on screen, failing with the real cause.

    The output is captured as it streams, so a failure reports what yt-dlp
    actually said (and the flag that fixes it) instead of a bare exit code.
    """
    code, output = util.run_streaming_captured(cmd, logger=logger)
    if code != 0:
        if not _YTDLP_ERROR.search(output or ""):
            # yt-dlp died without printing a reason: the exit code is all there is.
            raise ClipperError(f"Download failed for {url} (yt-dlp exit code {code}).")
        raise ClipperError(explain_failure(output, url))


def fetch_metadata(url: str, config: ClipConfig, logger: Logger | None = None) -> dict:
    """Return the yt-dlp metadata document for a single video."""
    output = _run_captured(
        _ytdlp(*_base_args(config), "--dump-single-json", "--skip-download", url),
        url,
        logger,
    )
    text = output.strip()
    if not text:
        raise ClipperError(f"yt-dlp returned no metadata for {url}")

    # The happy path is exactly one JSON document, so try that first and keep
    # the brace scan only as a fallback for a noisy stdout.
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = json.loads(_extract_json_object(text, url))
    if not isinstance(document, dict):
        raise ClipperError(f"yt-dlp returned an unexpected metadata document for {url}")
    return document


def _extract_json_object(text: str, url: str) -> str:
    """Slice out the first balanced ``{...}`` block from noisy output."""
    start = text.find("{")
    if start == -1:
        raise ClipperError(
            f"Could not find metadata JSON in the yt-dlp output for {url}: "
            f"{util.tail_lines(text, 3)}"
        )
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ClipperError(f"Unbalanced metadata JSON from yt-dlp for {url}")


def _template(output: str | Path) -> str:
    return str(Path(output).with_suffix(".%(ext)s"))


def download_audio(
    url: str,
    destination: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
) -> Path:
    """Download only the audio track, used for the analysis pass."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_visible(
        _ytdlp(
            *_base_args(config),
            "-f",
            "bestaudio/best",
            "-o",
            _template(destination),
            url,
        ),
        url,
        logger,
    )
    return _resolve_downloaded(destination)


def download_section(
    url: str,
    start: float,
    end: float,
    destination: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
) -> Path:
    """Download one video section (with padding) at the configured height."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    padded_start = max(0.0, start - SECTION_PADDING)
    padded_end = end + SECTION_PADDING
    section = f"*{util.fmt_clock_ms(padded_start)}-{util.fmt_clock_ms(padded_end)}"
    height = max(240, config.max_height)
    _run_visible(
        _ytdlp(
            *_base_args(config),
            "-f",
            f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "--download-sections",
            section,
            "-o",
            _template(destination),
            url,
        ),
        url,
        logger,
    )
    return _resolve_downloaded(destination)


def download_full(
    url: str,
    destination: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
) -> Path:
    """Download the whole video once, then cut everything locally."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    height = max(240, config.max_height)
    _run_visible(
        _ytdlp(
            *_base_args(config),
            "-f",
            f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            _template(destination),
            url,
        ),
        url,
        logger,
    )
    return _resolve_downloaded(destination)


def _resolve_downloaded(destination: Path) -> Path:
    """yt-dlp appends its own extension, so find the file that appeared."""
    if destination.exists():
        return destination
    stem = destination.stem
    candidates = [
        path
        for path in destination.parent.glob(f"{stem}.*")
        if path.suffix.lower() not in {".part", ".ytdl", ".json", ".temp"}
    ]
    if not candidates:
        raise ClipperError(
            f"yt-dlp did not produce a file matching {destination.parent / (stem + '.*')}"
        )
    return max(candidates, key=lambda path: path.stat().st_size)


def resolve_origin(
    ffprobe: str,
    media: str | Path,
    requested_start: float,
    logger: Logger | None = None,
) -> float:
    """Timeline offset of ``media`` relative to the source video's 0:00.

    A section download comes out in one of two shapes, and the renderer has to
    know which one, because it seeks into the file it was handed:

    * the container keeps the source timestamps, so ``format.start_time``
      equals the section offset and seeking to an absolute time just works
      (origin ``0.0``);
    * the container timeline was reset to zero, so the file's 0:00 *is* the
      section offset and every absolute time has to be shifted by it.

    Guessing wrong silently renders the wrong part of the video, so this is
    measured with ``ffprobe`` rather than assumed. ``requested_start`` is the
    offset the section was asked for (``0.0`` for a full download).
    """
    if requested_start <= 0.0:
        return 0.0
    if util.probe_start_time(ffprobe, media, logger) > 0.5:
        if logger:
            logger.debug(f"{Path(media).name} kept its source timestamps")
        return 0.0
    if logger:
        logger.debug(
            f"{Path(media).name} has a reset timeline; shifting by "
            f"{requested_start:.2f}s"
        )
    return float(requested_start)
