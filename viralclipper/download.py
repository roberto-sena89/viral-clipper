"""yt-dlp wrappers: metadata, audio-only pass and per-section downloads.

Downloads run as ``python -m yt_dlp`` so the tool works even when the yt-dlp
console script is not on PATH. Sections are downloaded with a safety padding
and then cut precisely by ffmpeg, which avoids depending on the encoder GOP
boundaries of the source video.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable, Iterable
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


def _run_visible(
    cmd: list[str],
    url: str,
    logger: Logger | None,
    on_line: Callable[[str], None] | None = None,
) -> None:
    """Run a download with progress on screen, failing with the real cause.

    The output is captured as it streams, so a failure reports what yt-dlp
    actually said (and the flag that fixes it) instead of a bare exit code.
    ``on_line`` forwards each line as it arrives, which is what turns yt-dlp's
    own progress output into a percentage.
    """
    code, output = util.run_streaming_captured(cmd, logger=logger, on_line=on_line)
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


#: Language of the throwaway listing that restores the view counts. yt-dlp's
#: suffix parser knows "K", "M" and "B" and nothing else, so a listing fetched
#: in pt-BR — the language that keeps the titles readable — truncates every
#: count above a thousand: "57 mi de visualizações" comes back as 57.
_COUNT_LANGUAGE = "en"


def _walk_entries(document: dict):
    """Every video entry of a flat playlist document, nesting included.

    A channel tab can hold playlists that hold videos, which is the same shape
    ``_scrap_results`` flattens before showing the list.
    """
    stack = list(document.get("entries") or [])
    while stack:
        entry = stack.pop()
        if not isinstance(entry, dict):
            continue
        nested = entry.get("entries")
        if entry.get("_type") == "playlist" and nested:
            stack.extend(nested)
            continue
        yield entry


def unlocalized_view_counts(
    url: str, config: ClipConfig, logger: Logger | None = None
) -> dict[str, int]:
    """View counts of a listing, keyed by video id, with the scale intact.

    Localizing the request is what keeps the titles in pt-BR, and the counts
    travel in the same payload: YouTube writes them as "57 mi de visualizações"
    and yt-dlp reads only the digits. The listing is therefore asked for twice
    when a non-English language is configured — once localized for the titles,
    once in English for the numbers. Costs one request, and returns an empty
    map (rather than raising) when the extra call fails or is not needed,
    because a missing count must never take the list down.
    """
    language = str(getattr(config, "metadata_language", "") or "").strip()
    if not language or language.lower().startswith("en"):
        # Nothing was localized, so the listing the caller already holds is fine.
        return {}

    probe = dataclasses.replace(config, metadata_language=_COUNT_LANGUAGE)
    try:
        document = fetch_metadata(url, probe, logger)
    except ClipperError as exc:
        if logger:
            logger.warn(f"Contagem de views nao pode ser lida: {exc}")
        return {}

    counts: dict[str, int] = {}
    for entry in _walk_entries(document):
        video_id = str(entry.get("id") or "")
        value = entry.get("view_count")
        if video_id and isinstance(value, (int, float)):
            counts[video_id] = int(value)
    return counts


def repair_view_counts(
    entries: list[dict],
    url: str,
    config: ClipConfig,
    logger: Logger | None = None,
) -> int:
    """Put the real scale back into the view counts of ``entries``, in place.

    ``url`` has to be the listing the entries came from, and ``entries`` the
    flat playlist entries themselves, so the ids line up. Returns how many rows
    were corrected.
    """
    counts = unlocalized_view_counts(url, config, logger)
    if not counts:
        return 0

    corrected = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        exact = counts.get(str(entry.get("id") or ""))
        if exact is None or entry.get("view_count") == exact:
            continue
        entry["view_count"] = exact
        corrected += 1
    return corrected


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
    on_line: Callable[[str], None] | None = None,
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

    ``on_line`` is where the progress of a long item can be followed; a caller
    that does not care leaves it alone.
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
        on_line,
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
        if path.suffix.lower() not in _NOT_MEDIA_SUFFIXES
    ]
    if not candidates:
        raise ClipperError(
            f"yt-dlp did not produce a file matching {destination.parent / (stem + '.*')}"
        )
    return max(candidates, key=lambda path: path.stat().st_size)


#: Suffixes yt-dlp leaves behind that are not the media itself. A file named
#: like a download but ending in one of these is a leftover, not a result.
_NOT_MEDIA_SUFFIXES = {".part", ".ytdl", ".json", ".temp"}

#: Characters that are unsafe in a filename on Windows. A video title is
#: attacker-controlled text that ends up in a path, so this is correctness
#: rather than cosmetics.
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclasses.dataclass(frozen=True)
class MediaTarget:
    """One video of a selection: where it lives, and what to call the file.

    ``media_id`` is what makes the download resumable — a file whose name starts
    with it is the same video — so it is worth carrying even when the title is
    unknown.
    """

    url: str
    media_id: str = ""
    title: str = ""


#: yt-dlp writes its progress as ``[download]  54.9% of  230.98MiB at ...``. The
#: percentage is the only part of that line that means the same thing on every
#: site, so it is the only part that is read.
_YTDLP_PERCENT = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%")


@dataclasses.dataclass(frozen=True)
class DownloadEvent:
    """One step of a batch download, reported while it happens.

    ``percent`` is the byte progress of the item in hand, when yt-dlp knows the
    size. That is what keeps a single long video from freezing the bar at
    "1 de 20" for minutes; it is ``None`` for the events that are not about
    bytes (a skip, a failure, the end).
    """

    phase: str  # item | bytes | skipped | failed | done
    index: int = 0
    total: int = 0
    title: str = ""
    percent: float | None = None
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0


@dataclasses.dataclass
class DownloadSummary:
    """What one selection download did."""

    total: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    root: Path | None = None
    errors: list[tuple[str, str]] = dataclasses.field(default_factory=list)

    def lines(self) -> list[str]:
        """The report as plain text, the shape the log panel already shows."""
        lines = [
            f"Selecao: {self.total} item(ns)",
            f"  baixados : {self.downloaded}",
            f"  ja tinha : {self.skipped}",
            f"  falhas   : {self.failed}",
        ]
        if self.root is not None:
            lines.append(f"  destino  : {self.root}")
        for name, error in self.errors[:10]:
            lines.append(f"    ! {name}: {error}")
        if len(self.errors) > 10:
            lines.append(f"    ... e mais {len(self.errors) - 10} falha(s)")
        return lines


def _safe_stem(text: str) -> str:
    """A filename fragment: safe characters only, and never a bare ``.``."""
    cleaned = _UNSAFE_FILENAME.sub("", str(text or "")).strip().strip(".")
    return cleaned[:80]


def media_stem(target: MediaTarget) -> str:
    """Filename stem for one target: the id first, then the readable title.

    The id leads because that is what a second run recognises; the title follows
    so the folder reads like a human wrote it. Either one alone is enough, and
    the last resort is a generic name rather than an empty one.
    """
    ident = _safe_stem(target.media_id)
    slug = util.slugify(target.title, fallback="", max_length=48) if target.title else ""
    if ident and slug:
        return f"{ident} - {slug}"
    return ident or slug or "video"


def _already_downloaded(root: Path, target: MediaTarget) -> Path | None:
    """The file of ``target`` left by an earlier run, when it is on disk.

    The id is the anchor: a title can be edited upstream, an id cannot, and a
    file named after the id is the same video. The pattern is ``<id> -`` and not
    ``<id>`` so that an id which happens to be a prefix of another is not
    mistaken for it. Without an id only the stem is left to match on.
    """
    if not root.is_dir():
        return None
    patterns: list[str] = []
    ident = _safe_stem(target.media_id)
    if ident:
        patterns += [f"{ident} - *", f"{ident}.*"]
    patterns.append(f"{media_stem(target)}.*")
    for pattern in patterns:
        for candidate in sorted(root.glob(pattern)):
            if candidate.is_file() and candidate.suffix.lower() not in _NOT_MEDIA_SUFFIXES:
                return candidate
    return None


def download_many(
    targets: Iterable[MediaTarget],
    destination: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
    downloader: Callable[..., Path] | None = None,
    overwrite: bool = False,
    on_progress: Callable[[DownloadEvent], None] | None = None,
) -> DownloadSummary:
    """Download every target into one folder, resumable and failure-isolated.

    The unit of work is the selection the user marked, not a catalogue: one
    private or deleted video costs that item and never the batch, and a file
    already on disk is skipped so repeating the download continues where the
    previous one stopped. Order is preserved — the list is the order the user
    saw on screen.

    ``downloader`` is injected so the loop can be tested without a network,
    exactly like :func:`viralclipper.archive.archive_profile`; production passes
    :func:`download_media`.

    ``on_progress`` is called with a :class:`DownloadEvent` at every step: when
    an item starts, on each percentage yt-dlp reports, on a skip, on a failure
    and once at the end. A batch of twenty long videos takes minutes, and
    without this the only thing a caller can show is a spinner.
    """
    fetch = downloader or download_media
    root = Path(destination)
    items = list(targets)
    summary = DownloadSummary(total=len(items), root=root)
    if not items:
        return summary
    root.mkdir(parents=True, exist_ok=True)

    def report(phase: str, index: int = 0, title: str = "", percent: float | None = None) -> None:
        """Hand one step to the caller, with the counts as they stand now."""
        if on_progress is None:
            return
        on_progress(
            DownloadEvent(
                phase=phase,
                index=index,
                total=summary.total,
                title=title,
                percent=percent,
                downloaded=summary.downloaded,
                skipped=summary.skipped,
                failed=summary.failed,
            )
        )

    for position, target in enumerate(items, start=1):
        url = str(target.url or "").strip()
        if not url:
            summary.failed += 1
            summary.errors.append((media_stem(target), "sem URL"))
            report("failed", position, target.title)
            continue

        existing = None if overwrite else _already_downloaded(root, target)
        if existing is not None:
            summary.skipped += 1
            if logger:
                logger.info(f"[{position}/{summary.total}] ja existe: {existing.name}")
            report("skipped", position, target.title)
            continue

        stem = media_stem(target)
        if logger:
            logger.step(f"[{position}/{summary.total}] {stem}")
        report("item", position, target.title, 0.0)

        highest = 0.0

        def track(line: str, _position: int = position, _title: str = target.title) -> None:
            """Turn yt-dlp's own progress line into a percentage.

            The item arrives as two streams (video, then audio) and the
            percentage restarts at zero for the second one, so only a value
            higher than the last one seen is reported: the bar never goes
            backwards.
            """
            nonlocal highest
            match = _YTDLP_PERCENT.search(line)
            if match is None:
                return
            percent = float(match.group("percent"))
            if percent <= highest:
                return
            highest = percent
            report("bytes", _position, _title, percent)

        try:
            if on_progress is None:
                fetch(url, root / stem, config, logger)
            else:
                # Only a caller that wants progress asks the downloader for it:
                # an injected fake takes the four arguments it always took.
                fetch(url, root / stem, config, logger, on_line=track)
        except ClipperError as exc:
            summary.failed += 1
            summary.errors.append((stem, str(exc)))
            if logger:
                logger.warn(f"falhou: {stem}: {exc}")
            report("failed", position, target.title)
        except Exception as exc:  # noqa: BLE001 - one item must not stop the batch
            summary.failed += 1
            summary.errors.append((stem, repr(exc)))
            if logger:
                logger.warn(f"falhou: {stem}: {exc!r}")
            report("failed", position, target.title)
        else:
            summary.downloaded += 1

    report("done")
    return summary


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
