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
    (
        # Chrome 127+ seals cookie values with App-Bound Encryption (the v20
        # prefix). The master key in Local State still unwraps, which is why the
        # message says DPAPI, but the per-cookie layer is bound to the browser
        # process and cannot be opened from outside it. Telling the user to
        # retry with the --cookies-from-browser flag they already passed is a
        # dead end, so point at the file-based escape hatch instead.
        "failed to decrypt with dpapi",
        "Esse navegador usa App-Bound Encryption (Chrome/Edge 127+) e o yt-dlp "
        "não consegue ler os cookies dele. Exporte os cookies para um arquivo "
        "cookies.txt e passe --cookies /caminho/cookies.txt.",
    ),
    (
        "could not copy chrome cookie database",
        "O navegador está aberto e travando o banco de cookies. Feche-o e "
        "repita, ou exporte os cookies para um arquivo cookies.txt e passe "
        "--cookies /caminho/cookies.txt.",
    ),
    (
        # Instagram rejects any request whose TLS fingerprint is not a real
        # browser's, and answers with a bare 400 that names nothing. Without
        # curl_cffi yt-dlp cannot impersonate, so the failure looks like a
        # broken extractor instead of a missing optional dependency.
        "unable to extract data",
        "Se for Instagram, falta o curl_cffi: pip install \"curl_cffi>=0.7\". "
        "O Instagram recusa requisições cujo TLS não seja de um navegador real.",
    ),
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


#: yt-dlp keeps only the LAST ``--extractor-args`` of a given extractor: a
#: second flag for ``youtube`` REPLACES the first instead of merging with it.
#: That is why the metadata language is folded into the caller's own flag
#: instead of being appended as one more flag of its own.
_EXTRACTOR_ARGS = "--extractor-args"


def _extractor_arg_values(argv: list[str]) -> list[str]:
    """Every value passed through ``--extractor-args``, in either spelling."""
    values: list[str] = []
    for index, arg in enumerate(argv):
        if arg.startswith(f"{_EXTRACTOR_ARGS}="):
            values.append(arg.split("=", 1)[1])
        elif arg == _EXTRACTOR_ARGS and index + 1 < len(argv):
            values.append(argv[index + 1])
    return values


def _pins_metadata_language(argv: list[str]) -> bool:
    """True when the caller already chose the YouTube metadata language.

    An explicit ``youtube:lang=...`` in ``--ytdlp-arg`` is an instruction, not
    something to be overridden by the default, so the default steps aside.
    """
    for value in _extractor_arg_values(argv):
        key, _, rest = value.partition(":")
        if key != "youtube":
            continue
        for part in rest.split(";"):
            if part.split("=", 1)[0].strip() == "lang":
                return True
    return False


def _extra_ytdlp_args(config: ClipConfig) -> list[str]:
    """The extra yt-dlp words, with the metadata language folded in.

    YouTube localizes every metadata field to the language of the request: the
    same video is listed as "LULA HAS LOST CONTROL OF THE GOVERNMENT" with the
    default request and as "LULA PERDEU CONTROLE do GOVERNO" with ``pt``. Since
    titles are what the report, the file listing and the web panel show, the
    language is set on every call.

    Two flags cannot express that (see :data:`_EXTRACTOR_ARGS`), so the caller's
    youtube arguments are collected and re-emitted as a single flag with the
    language first. Duplicates of the same ``key=value`` are dropped, while the
    same key with different values is kept — that is how yt-dlp takes more than
    one ``player_client``.
    """
    argv = [str(word) for word in config.extra_ytdlp_args]
    language = str(getattr(config, "metadata_language", "") or "").strip()
    if not language or _pins_metadata_language(argv):
        return argv

    rebuilt: list[str] = []
    youtube: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        value: str | None = None
        consumed = 1
        if arg.startswith(f"{_EXTRACTOR_ARGS}="):
            value = arg.split("=", 1)[1]
        elif arg == _EXTRACTOR_ARGS and index + 1 < len(argv):
            value = argv[index + 1]
            consumed = 2
        if value is None or not value.startswith("youtube:"):
            rebuilt.append(arg)
            index += 1
            continue
        for part in value.partition(":")[2].split(";"):
            stripped = part.strip()
            if stripped and stripped.split("=", 1)[0].strip() != "lang":
                if stripped not in youtube:
                    youtube.append(stripped)
        index += consumed

    merged = ["lang=" + language] + youtube
    rebuilt += [_EXTRACTOR_ARGS, "youtube:" + ";".join(merged)]
    return rebuilt


def _base_args(config: ClipConfig) -> list[str]:
    args = ["--no-playlist", "--no-warnings", "--retries", "5", "--fragment-retries", "5"]
    if config.cookies_from_browser:
        args += ["--cookies-from-browser", config.cookies_from_browser]
    args += _extra_ytdlp_args(config)
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


def download_media(
    url: str,
    destination: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
) -> Path:
    """Download one media item whole, at the configured height.

    The difference from :func:`download_full` is the filter. A carousel post is
    a *set* of images and videos sharing one shortcode, so yt-dlp enumerates
    every entry of that post; requesting the plain video filter on it downloads
    nothing at all. Falling back through every shape (video, then audio+video,
    then best) is what makes one code path work for a reel, a video post and a
    photo carousel.

    Audio is merged into mp4 rather than kept separate: the destination is a
    personal archive of the profile, and one playable file per post is the
    point. ``-S`` orders the format selection so the merge is deterministic
    instead of whatever the site happened to list first.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    height = max(240, config.max_height)
    _run_visible(
        _ytdlp(
            *_base_args(config),
            "-f",
            f"bv*[height<={height}]+ba/b[height<={height}]/b",
            "-S",
            "res,vcodec:h264,acodec:aac",
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
