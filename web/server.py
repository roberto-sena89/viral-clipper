"""Optional web UI server for viral-clipper.

Serves web/index.html and exposes a small JSON API that drives the real
pipeline (viralclipper.pipeline). Stdlib only: no extra dependency, so
`python web/server.py` works from the repo root with the project venv.

Endpoints:
  GET  /            -> the SPA
  GET  /status      -> {jobs, clips} current state
  POST /run         -> {options: {...}, plan_only: bool} -> {clips, log_lines} | {error}
  GET  /clips/<id>  -> static clip file from the output dir
"""

from __future__ import annotations

import html
import json
import threading
import unicodedata
import webbrowser
from http import server as http_server
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent

# Import the package itself; the server must run from the repo root so
# `viralclipper` resolves, but __file__ lets us be explicit.
import re
import sys
sys.path.insert(0, str(REPO_ROOT))

from viralclipper import config as config_mod  # noqa: E402
from viralclipper import download as download_mod  # noqa: E402
from viralclipper import ig_profile as ig_profile_mod  # noqa: E402
from viralclipper import pipeline, report, transcript_import, util, viral_report  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402

HOST = "127.0.0.1"
PORT = 7755

# Shared state. The HTTP handler runs on one thread; runs happen on a worker
# thread, so access goes through the lock.
_lock = threading.Lock()
_state: dict = {"jobs": [], "clips": [], "scrap": [], "download": None}


class CollectingLogger(util.Logger):
    """Logger that records lines so the UI can replay them.

    Recording happens before the print: a dead server console raises
    ``OSError`` on stdout, and the line must survive it for the UI.
    """

    def __init__(self) -> None:
        super().__init__(quiet=False, verbose=False)
        self.lines: list[str] = []

    def _emit(self, prefix: str, message: str) -> None:
        self.lines.append(f"{prefix} {message}")
        super()._emit(prefix, message)


def _clip_to_payload(clip: report.ClipRecord, output_dir: Path) -> dict:
    path = Path(clip.file) if clip.file else None
    rel = None
    if path and path.exists():
        try:
            rel = str(path.relative_to(output_dir.resolve())).replace("\\", "/")
        except ValueError:
            rel = path.name
    return {
        "title": f"Clip {clip.index}",
        "score": round(clip.score, 1),
        "start": clip.start_label,
        "end": clip.end_label,
        "duration": clip.duration,
        "video": rel,
        # False for a plan-only run: the cut was scored but no file was written,
        # so the UI must not offer it as a playable preview.
        "rendered": bool(rel),
        "thumb": None,
        "file": clip.file,
        "hook_terms": clip.hook_terms,
        "text": clip.text,
    }


def _download_worker(
    targets: list[download_mod.MediaTarget],
    root: Path,
    config: config_mod.ClipConfig,
    logger: CollectingLogger,
) -> None:
    """Run one batch off the request thread, publishing progress as it goes.

    Never raises: the record is where a caller looks, so a failure is written
    there instead of dying inside a thread where nothing can see it.
    """

    def progress(event) -> None:
        _publish_download(
            phase=event.phase,
            index=event.index,
            total=event.total,
            title=event.title,
            percent=0.0 if event.percent is None else event.percent,
            downloaded=event.downloaded,
            skipped=event.skipped,
            failed=event.failed,
        )

    try:
        summary = download_mod.download_many(
            targets, root, config, logger, on_progress=progress
        )
    except Exception as exc:  # noqa: BLE001 - a thread must not die silently
        _publish_download(
            active=False, state="erro", error=f"{exc!r}", lines=logger.lines
        )
        return

    _publish_download(
        active=False,
        state="concluido",
        phase="done",
        percent=100.0,
        total=summary.total,
        downloaded=summary.downloaded,
        skipped=summary.skipped,
        failed=summary.failed,
        root=_relative_to_repo(summary.root),
        lines=summary.lines(),
        errors=[{"item": name, "error": error} for name, error in summary.errors],
    )


def _options_to_config(options: dict) -> config_mod.ClipConfig:
    """Map the JSON payload onto ClipConfig, coercing types like the CLI does."""
    payload = dict(options or {})
    payload["url"] = str(payload.get("url") or "")
    output = payload.pop("output", "output") or "output"
    payload["output_dir"] = Path(output)
    cache_dir = payload.pop("cache_dir", None)
    if not cache_dir:
        payload["cache_dir"] = Path(output, "cache", "transcripts")

    def num(key, cast=float, default=0.0):
        try:
            return cast(payload.get(key))
        except (TypeError, ValueError):
            return default

    for key in ("min_duration", "max_duration", "target_duration", "min_score",
                "min_gap", "lufs"):
        if key in payload:
            payload[key] = num(key)
    for key in ("count", "beam_size", "crf", "workers"):
        if key in payload:
            payload[key] = num(key, cast=int, default=1)
    # font_size may come as null from the UI: None means "inherit the preset".
    if "font_size" in payload and payload["font_size"] is not None:
        payload["font_size"] = num("font_size", cast=int, default=84)

    # Supplied transcript: accept pasted text and/or a file path. Either one
    # makes the pipeline skip whisper entirely.
    text = payload.get("transcript_text")
    if isinstance(text, str) and text.strip():
        payload["transcript_text"] = text.strip()
    else:
        payload.pop("transcript_text", None)
    transcript_file = payload.get("transcript_file")
    if isinstance(transcript_file, str) and transcript_file.strip():
        payload["transcript_file"] = Path(transcript_file.strip())
    else:
        payload.pop("transcript_file", None)

    # A cookies file is not a ClipConfig field: it is sugar for
    # ``--ytdlp-arg --cookies <path>``. Folding it in here, before the unknown
    # keys are dropped, keeps the UI able to offer the one cookie route that
    # still works on Chrome/Edge 127+ (App-Bound Encryption sealed the other).
    # It wins over ``cookies_from_browser`` so both can stay selected in the
    # form without producing two competing sets of cookie flags.
    cookies_file = payload.pop("cookies_file", None)
    if isinstance(cookies_file, str) and cookies_file.strip():
        payload["cookies_from_browser"] = None
        payload["extra_ytdlp_args"] = [
            *_ytdlp_argv(payload.get("extra_ytdlp_args")),
            "--cookies", cookies_file.strip(),
        ]

    # Drop keys the dataclass does not declare, mirroring config_from_args.
    known = {field.name for field in __import__("dataclasses").fields(config_mod.ClipConfig)}
    clean = {k: v for k, v in payload.items() if k in known}

    # burn_captions is derived from caption_style in the UI: "none" means no
    # captions at all, anything else burns them.
    style = payload.get("caption_style")
    if style == "none":
        clean["caption_style"] = "none"
        clean["burn_captions"] = False

    cfg = config_mod.ClipConfig(**clean)
    cfg.validate()
    return cfg


def _run_job(options: dict, plan_only: bool) -> dict:
    """Execute one URL end to end. Runs off the request thread."""
    logger = CollectingLogger()
    url = str(options.get("url") or "")
    job = {"url": url, "status": "running",
           "meta": ("plan-only · " if plan_only else "") + str(options.get("whisper_model", "small"))}
    with _lock:
        _state["jobs"].append(job)
        _state["clips"] = []

    try:
        config = _options_to_config(options)
        config.dry_run = bool(plan_only)
    except (ValueError, TypeError) as exc:
        job["status"] = "fail"
        job["meta"] = f"erro: {exc}"
        return {"error": str(exc), "log_lines": logger.lines}

    try:
        work = util.ensure_dir(config.work_path())
        metadata, analysis, transcript, units = pipeline.analyse(config, work, logger)
        windows = pipeline.select_windows(units, analysis, config, logger)
        viral = pipeline.build_viral_report(windows, units, metadata, config, logger)
        records = pipeline.render_windows(
            windows, metadata, analysis, transcript, config, work, logger
        )
        output_dir = util.ensure_dir(config.output_dir)
        run_report = report.RunReport(
            url=config.url,
            title=str(metadata.get("title") or ""),
            video_id=str(metadata.get("id") or ""),
            uploader=str(metadata.get("uploader") or metadata.get("channel") or ""),
            source_duration=round(analysis.duration, 2),
            language=transcript.language if transcript else "nao transcrito",
            model=transcript.model_name if transcript else "-",
            engine=config.engine,
            min_duration=config.min_duration,
            max_duration=config.max_duration,
            clips=records,
        )
        report.write_json(run_report, output_dir / "clips.json")
        report.write_markdown(run_report, output_dir / "clips.md")
        clips = [_clip_to_payload(c, output_dir) for c in records]
        rendered = sum(1 for clip in clips if clip["rendered"])
        with _lock:
            _state["clips"] = clips
            job["status"] = "done"
            job["meta"] = (
                f"{len(clips)} cortes analisados (sem render)"
                if plan_only
                else f"{rendered} clips · {run_report.title[:40]}"
            )
        return {"clips": clips, "log_lines": logger.lines,
                "title": run_report.title,
                "plan_only": bool(plan_only),
                "viral": [analysis.to_dict() for analysis in viral],
                "viral_markdown": viral_report.format_markdown(viral, run_report.title)}
    except ClipperError as exc:
        job["status"] = "fail"
        job["meta"] = f"erro: {exc}"
        return {"error": str(exc), "log_lines": logger.lines}
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        job["status"] = "fail"
        job["meta"] = f"erro: {exc!r}"
        return {"error": f"{exc!r}", "log_lines": logger.lines}
    finally:
        if not bool(plan_only):
            try:
                import shutil
                shutil.rmtree(config.work_path(), ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass


#: Folder the templates page offers as ready-made text plates. It lives inside
#: ``web/`` on purpose: the static route already serves anything under there with
#: the right Content-Type, so the preview can show the very file the render will
#: read without a second endpoint and a second copy of the bytes.
PLATES_DIR = WEB_DIR / "fundo titulo"

#: Suffixes offered as plates. Every one of them is a still ffmpeg decodes, and
#: every one is a type the static route serves with an image Content-Type.
PLATE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def _image_size(path: Path) -> tuple[int, int] | None:
    """Read ``(width, height)`` out of an image header, without decoding it.

    The templates page shows each plate as a thumbnail with its real size, and
    that is a header read: pulling in Pillow to learn that a JPEG is 1904x544
    would add a dependency to a server that is stdlib-only on purpose. Returns
    ``None`` for a format it cannot read, which the page renders as "?" rather
    than as a broken image.
    """
    try:
        data = path.read_bytes()[:64]
    except OSError:
        return None
    # PNG: an 8-byte signature, then an IHDR chunk whose payload starts with the
    # two big-endian 32-bit dimensions.
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return (
            int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"),
        )
    # WebP: a RIFF container whose first chunk is VP8 (lossy), VP8L (lossless) or
    # VP8X (extended). Each spells its dimensions differently and none of them is
    # worth a decoder here, so only the two fixed-layout ones are read.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8 ":
            return (
                int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF,
            )
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X":
            return (
                int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1,
            )
        return None
    # JPEG: walk the marker segments to the frame header, which is the first SOF
    # and carries the dimensions. Segments are length-prefixed, so this is a real
    # walk and not a fixed offset — and a truncated read stops it by running out
    # of bytes, which the length check below turns into "unknown size".
    if data[:2] != b"\xff\xd8":
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    at = 2
    while at + 9 < len(raw):
        if raw[at] != 0xFF:
            at += 1
            continue
        marker = raw[at + 1]
        # SOF0..SOF15, minus the four markers in that range that are not frame
        # headers (DHT, JPG and DAC), each of which would carry other bytes.
        if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            return (
                int.from_bytes(raw[at + 7 : at + 9], "big"),
                int.from_bytes(raw[at + 5 : at + 7], "big"),
            )
        at += 2 + int.from_bytes(raw[at + 2 : at + 4], "big")
    return None


def _relative_to(path: Path, base: Path) -> str:
    """``path`` as seen from ``base``, with forward slashes.

    Falls back to the file name when ``path`` is not under ``base``: the callers
    pass a folder that may be a temporary one (the tests), and raising there would
    make a read-only listing fail on an unrelated path question.
    """
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def list_plates(base: Path | None = None) -> list[dict]:
    """The ready-made plates on offer, with the URL that serves each one.

    The URL is built with :func:`urllib.parse.quote` because the folder name has
    a space in it, and the static route percent-decodes before touching the disk —
    an unquoted space would arrive as a literal and 404. The path handed to the
    engine is the repo-relative one, because that is what a template file
    records: the CLI is run from the repo root, and a repo-relative path is the
    only spelling that still points at the file when the template moves.
    """
    folder = base if base is not None else PLATES_DIR
    if not folder.is_dir():
        return []
    plates: list[dict] = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.suffix.lower() not in PLATE_SUFFIXES:
            continue
        size = _image_size(path)
        plates.append(
            {
                "name": path.name,
                "path": _relative_to(path, REPO_ROOT),
                "url": "/" + quote(_relative_to(path, WEB_DIR)),
                "width": size[0] if size else 0,
                "height": size[1] if size else 0,
            }
        )
    return plates


#: Content types for the files served straight out of ``web/``. Every response
#: carries ``X-Content-Type-Options: nosniff``, so a type that is only "close
#: enough" is refused: a script sent as ``octet-stream`` never runs, and the
#: hero preview video sent as ``octet-stream`` never plays. Keyed by lowercase
#: suffix because a suffix on disk can be in any case.
_ASSET_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def asset_content_type(path: Path) -> str:
    """The Content-Type to serve a static asset with, by suffix.

    Unknown suffixes stay opaque on purpose: guessing ``text/html`` for a file
    nobody named would turn any uploaded asset into a script host.
    """
    return _ASSET_TYPES.get(path.suffix.lower(), "application/octet-stream")


def resolve_within(base: Path, rel: str) -> Path | None:
    """Resolve a client-supplied relative path under ``base``.

    Returns the existing file path, or ``None`` when the path escapes ``base``
    (path traversal, already percent-decoded by the caller) or does not exist.
    """
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


# Video containers the library view lists. Anything else in the output folder
# (json/md/srt reports, work directories) is not a preview candidate.
LIBRARY_SUFFIXES: frozenset[str] = frozenset({".mp4", ".mkv", ".webm", ".mov"})


def _ytdlp_argv(raw: object) -> list[str]:
    """Normalise a client-supplied yt-dlp argument list into argv words.

    ``download._base_args`` splices this straight into the command line, so each
    element has to be exactly one word. Three shapes arrive in practice:

    * ``["--cookies", "C:/x.txt"]`` — already argv; used as is.
    * ``"--cookies C:/x.txt"`` — a whole line. ``list()`` on it yields 18
      single-character arguments, so the failure surfaces as yt-dlp complaining
      about every letter of the alphabet rather than about the real mistake.
    * ``["--cookies C:/x.txt"]`` — one element holding a whole line, the shape
      someone reaches for when the docs show a command rather than a list.

    The last two are split on whitespace. Paths with spaces in them cannot be
    expressed through the convenience form; pass the pre-split list instead.
    """
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    argv: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            argv.extend(text.split())
    return argv


def _cookies_file_from_args(argv: list[str]) -> str:
    """Path given as ``--cookies`` in the argv the page sent, or "".

    ``ig_profile`` reads a Netscape jar from disk, so ``--cookies-from-browser``
    is of no use to it — and on Chrome/Edge 127+ that route is sealed by
    App-Bound Encryption anyway, which is why the page offers the file first.
    """
    for index, word in enumerate(argv):
        if word == "--cookies" and index + 1 < len(argv):
            return str(argv[index + 1])
        if word.startswith("--cookies="):
            return word.split("=", 1)[1]
    return ""


def _ig_session_from_payload(payload: dict) -> str:
    """Persist the pasted sessionid into the jar and return the normalised value.

    The value is a secret the page never sends twice: once it is in the jar,
    every later step — the listing, the archive and the yt-dlp download of the
    media — reads it from the same file. Writing it here, on the request thread,
    is what keeps the archive worker's signature unchanged.

    The jar path arrives in two shapes, because the two routes need it for
    different reasons: ``/scrap`` folds it into the yt-dlp argv (yt-dlp is what
    resolves a single item), while ``/scrap/archive`` sends it as its own field.
    """
    path = str(payload.get("cookies_file") or "").strip()
    if not path:
        path = _cookies_file_from_args(_ytdlp_argv(payload.get("extra_ytdlp_args")))
    if not path:
        return ""
    return ig_profile_mod.prepare_session(path, str(payload.get("ig_session") or ""))


def _ig_title(item) -> str:
    """First non-empty line of the caption, capped.

    The panel gives the title one line, and an Instagram caption is routinely
    paragraphs long with the useful sentence at the top. Capping here rather
    than in CSS keeps the JSON payload small for a 300-item listing.
    """
    for line in str(getattr(item, "caption", "") or "").splitlines():
        text = line.strip()
        if text:
            return text[:120]
    return getattr(item, "code", "") or getattr(item, "pk", "") or "(sem título)"


def _ig_profile_results(
    username: str, payload: dict, cookies_file: str, argv: list[str], session: str = ""
) -> tuple[list[dict], str, int]:
    """List an Instagram account through the same call the site itself makes.

    Deliberately NOT through yt-dlp: ``InstagramUserIE`` is disabled upstream
    and its ``_parse_graphql`` looks for a ``sharedData`` blob Instagram stopped
    emitting, so the flat playlist answers "Unable to extract data" for an
    account that is perfectly reachable in a browser. ``ig_profile`` reproduces
    the ``POST /graphql/query`` the React app issues instead.

    Keeping this beside the yt-dlp branch — rather than replacing it — is what
    leaves the YouTube path untouched: only an Instagram profile URL is
    diverted here.

    ``session`` is the sessionid the page pasted; it is already in the jar, and
    travels in memory too so a jar that could not be written still lists.
    """
    if not cookies_file:
        raise ClipperError(
            "Listar um perfil do Instagram exige um cookies.txt: o catálogo só é "
            "devolvido para uma sessão autenticada. Escolha os cookies por "
            "arquivo — a opção do navegador não serve para este caminho."
        )

    limit = payload.get("limit")
    try:
        limit = max(1, min(int(limit), 100)) if limit is not None else 20
    except (TypeError, ValueError):
        limit = 20
    viral = bool(payload.get("viral"))
    # "Mais viralizados": lista até o teto e ordena por views ANTES de cortar.
    # Sem isso o corte traria os N primeiros do feed, não os N maiores.
    fetch_end = 100 if viral else limit

    listing = ig_profile_mod.list_profile(
        username, cookies_file, limit=fetch_end, session=session
    )
    items = list(listing.items)
    if viral:
        items.sort(
            key=lambda item: item.play_count or item.like_count or 0, reverse=True
        )
    items = items[:limit]

    results = []
    for index, item in enumerate(items, start=1):
        results.append(
            {
                "index": index,
                "id": item.code or item.pk,
                "title": _ig_title(item),
                "url": item.url,
                "duration": item.duration,
                "uploader": listing.username,
                # Reels carry play_count, photo posts only like_count. Sending
                # whichever exists keeps the row informative instead of blank.
                "view_count": (
                    item.play_count if item.play_count is not None else item.like_count
                ),
                # The CDN URL from the GraphQL payload. The row never uses it as
                # a src (CSP forbids a foreign origin); it is what lets
                # /scrap/thumb skip a per-item yt-dlp round trip.
                "thumb": item.thumbnail,
                # Same classification the archiver uses, so the chip on the row
                # and the folder a download lands in cannot disagree.
                "folder": item.folder,
                "kind": item.kind,
                # False for a photo or a photo-only carousel: the row can say so
                # instead of letting the download fail with "no video in this
                # post" after a network round trip.
                "has_video": item.has_video,
                "_ytdlp_args": list(argv),
            }
        )
    return results, listing.username, 0


def _scrap_results(payload: dict) -> tuple[list[dict], str, int]:
    """Expand a link or a profile into a list of downloadable videos.

    Returns (results, title, removed): ``removed`` counts the repeated videos
    dropped from a profile listing (same id, or same title + duration).

    Two modes, and the difference is one yt-dlp flag:

    - ``link``  -> a single post/reel/video. Answered by ``fetch_metadata``,
      which already exists and is already what the CLI uses.
    - ``profile`` -> the whole feed of an account. Here the flat playlist is
      read instead: one entry per item, no per-video extraction, which is the
      only shape that stays fast when the account has hundreds of posts.
      An Instagram profile never takes that route — see ``_ig_profile_results``.

    ``--playlist-end`` is appended through ``extra_ytdlp_args``, which
    ``download._base_args`` appends LAST — that is what lifts the
    ``--no-playlist`` hardcoded at the top of the same list. So profile
    expansion needs no change to ``download.py`` at all.
    """
    url = str(payload.get("url") or "").strip()
    mode = str(payload.get("mode") or "link").strip()
    if not url:
        raise ClipperError("Informe o link do vídeo ou o perfil.")

    config = _options_to_config({"url": url, "output": payload.get("output") or "output"})
    # ``extra_ytdlp_args`` is a list of argv words, one flag per element. A bare
    # string is accepted as a convenience and split on whitespace, because
    # ``list("--cookies x.txt")`` silently becomes 21 one-character arguments
    # and yt-dlp then fails with a baffling "unrecognized arguments" list.
    # A whole command line as one element (``["--cookies x.txt"]``) is split
    # too, for the same reason.
    config.extra_ytdlp_args = _ytdlp_argv(payload.get("extra_ytdlp_args"))

    if mode == "link":
        # A single item: reuse the metadata path verbatim, so the answer the
        # panel shows is the same one a run would act on.
        meta = download_mod.fetch_metadata(url, config)
        entries = [meta]
        title = str(meta.get("title") or "")
        removed = 0
    else:
        # Instagram first, and only Instagram: its profile extractor is disabled
        # upstream, so the flat-playlist route below cannot answer for an
        # account. Diverting here — instead of inside download.py — is what
        # leaves the YouTube branch exactly as it was.
        ig_user = ig_profile_mod.profile_username(url)
        if ig_user:
            return _ig_profile_results(
                ig_user,
                payload,
                _cookies_file_from_args(config.extra_ytdlp_args),
                config.extra_ytdlp_args,
                _ig_session_from_payload(payload),
            )
        # Only reach here for a profile-shaped URL. A bare profile name is
        # accepted too, but a full URL is what yt-dlp can resolve without
        # guessing the site.
        limit = payload.get("limit")
        try:
            limit = max(1, min(int(limit), 100)) if limit is not None else 20
        except (TypeError, ValueError):
            limit = 20
        # "Mais viralizados": lista até o teto (100) e ordena por views antes
        # de cortar no limite pedido — sem isso o corte traria os N primeiros
        # do feed, não os N maiores. Sem a flag o custo é o de sempre.
        viral = bool(payload.get("viral"))
        fetch_end = 100 if viral else limit
        config.extra_ytdlp_args += ["--flat-playlist", "--playlist-end", str(fetch_end)]
        tab_url = _with_videos_tab(url)
        info = download_mod.fetch_metadata(tab_url, config)
        entries = list(info.get("entries") or [])
        title = str(info.get("title") or info.get("uploader") or "")
        # One more level: a tab that itself holds playlists. Flatten it, or the
        # list would offer "Videos" as if it were a video.
        flattened: list[dict] = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("_type") == "playlist":
                flattened.extend(e for e in (entry.get("entries") or []) if isinstance(e, dict))
            elif isinstance(entry, dict):
                flattened.append(entry)
        entries = flattened[:fetch_end]
        # Repetidos fora antes de qualquer corte: o extrator repete o mesmo
        # id entre páginas e reposts dividem título + duração. Sem isso o
        # limite de N itens vinha com furos e a ordem viral ranqueava cópias.
        entries, removed = _dedupe_entries(entries)
        # The request language that keeps the titles readable also localizes the
        # counts, and yt-dlp reads "57 mi de visualizações" as 57. The repair is
        # one extra listing in English, merged by id; when it fails the list
        # still works, just with the numbers YouTube wrote in words. Repair
        # runs BEFORE the viral sort, or the ranking would use broken numbers.
        download_mod.repair_view_counts(entries, tab_url, config)
        if viral:
            entries.sort(key=_viral_rank)
        entries = entries[:limit]

    results = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        # ``image`` is the largest still the extractor exposes. Instagram flat
        # entries have no ``thumbnail`` key; YouTube shorts do. Accept both
        # rather than picking one site's spelling.
        thumb = str(entry.get("thumbnail") or entry.get("image") or "").strip()
        results.append(
            {
                "index": index,
                "id": str(entry.get("id") or ""),
                "title": str(entry.get("title") or entry.get("id") or "(sem título)"),
                "url": str(entry.get("webpage_url") or entry.get("url") or ""),
                "duration": _as_float(entry.get("duration")),
                "uploader": str(entry.get("uploader") or entry.get("channel") or ""),
                # Flat extraction may not carry a duration (live, some
                # Instagram shapes). The UI shows what it has rather than a 0.
                "view_count": _as_int(entry.get("view_count")),
                "thumb": thumb,
                # The cookie flags that made THIS search work, carried per item
                # so the thumbnail fetch can repeat the same authenticated
                # request. Without them a private feed lists fine and every
                # image comes back empty.
                "_ytdlp_args": list(config.extra_ytdlp_args),
            }
        )
    return results, title, removed


def _normalise_title(text: object) -> str:
    """Canonical title for repost detection: no accents, no case, no tags.

    Links, @mentions and #hashtags are dropped because a repost usually keeps
    the sentence and changes the tags. "Parte 2" vs "parte 3" still differ,
    so episodes of a series are NOT merged — only true reposts are.
    """
    base = unicodedata.normalize(
        "NFKD", str(text if text is not None else ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.lower()
    base = re.sub(r"https?://\S+|@\w+|#\w+", " ", base)
    base = re.sub(r"[^a-z0-9 ]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _dedupe_entries(entries: list[dict]) -> tuple[list[dict], int]:
    """Drop repeated videos from a profile listing.

    Two levels, in order:

    1. Same id (or same URL when there is no id): the extractor repeated the
       item across pages/tabs. Keeps the first occurrence.
    2. Same normalised title AND same whole-second duration: a repost under
       another id. Keeps the highest view_count, so the surviving take is the
       one that performed best.

    Returns (unique_entries, removed_count). Non-dict items pass through.
    """
    unique: list[dict] = []
    removed = 0
    seen_ids: set[str] = set()
    seen_content: dict[tuple[str, int | None], int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            unique.append(entry)
            continue
        key = str(entry.get("id") or entry.get("webpage_url") or entry.get("url") or "")
        if key and key in seen_ids:
            removed += 1
            continue
        if key:
            seen_ids.add(key)
        title = _normalise_title(entry.get("title"))
        duration = entry.get("duration")
        bucket = round(float(duration)) if isinstance(duration, (int, float)) else None
        if title:
            sig = (title, bucket)
            if sig in seen_content:
                removed += 1
                previous = unique[seen_content[sig]]
                if isinstance(previous, dict):
                    cur_views = entry.get("view_count")
                    prev_views = previous.get("view_count")
                    cur_num = cur_views if isinstance(cur_views, (int, float)) else None
                    prev_num = prev_views if isinstance(prev_views, (int, float)) else None
                    if cur_num is not None and (prev_num is None or cur_num > prev_num):
                        unique[seen_content[sig]] = entry
                continue
            seen_content[sig] = len(unique)
        unique.append(entry)
    return unique, removed


def _viral_rank(entry: dict) -> float:
    """Sort key for "most viral first": negated view count, unknowns last.

    A missing count ranks below an explicit zero — zero means "flopped",
    missing means "the extractor did not say".
    """
    views = entry.get("view_count") if isinstance(entry, dict) else None
    if not isinstance(views, (int, float)):
        return 1.0
    return -views


def _as_float(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def esc(text: object) -> str:
    """Escape text for an HTML attribute or text node.

    The panel builds cards as HTML strings (there is no template engine and no
    build step), so every value that reaches the DOM has to pass through here.
    Titles and uploader names are attacker-controlled: a caption containing a
    double quote would otherwise close the ``data-item`` attribute and let the
    rest of the string become markup.
    """
    return html.escape(str(text if text is not None else ""), quote=True)


#: Thumbnail bytes are proxied, not hot-linked: Instagram and YouTube serve
#: from CDNs that send no permissive CORS headers, and their signed URLs expire
#: in hours. Caching the bytes on disk is also what makes the card survive a
#: refresh.
_THUMB_DIR_NAME = "thumbs"
_THUMB_MAX_BYTES = 6 * 1024 * 1024

#: Teto de itens numa selecao para download. A lista na tela tem no maximo 100
#: itens (o limite da busca), entao o teto existe para uma requisicao forjada
#: nao transformar o servidor num downloader de mil URLs de uma vez so.
_MAX_SELECTED_DOWNLOADS = 200


def _suggest_phrases(idea: str, count: int) -> list[str]:
    """Ask the ranker LLM for short hook phrases about a video idea.

    Reuses the ranker provider (model, URL and key), so phrases cost nothing
    new to configure. One phrase per line, faithful to the idea, caps the
    response at ``count`` non-empty lines.
    """
    from viralclipper import ranker

    config = config_mod.ClipConfig(url="", ranker="llm")
    provider = ranker.build_provider(config)
    if provider is None:  # pragma: no cover - build_provider raises first
        raise ClipperError("LLM desligado: use ranker='llm' com API key.")
    text = provider.complete(
        "Voce escreve ganchos curtos em pt-BR para videos verticais. "
        "Responda só com as frases, uma por linha, sem numerar, sem aspas, "
        "sem inventar fatos alem da ideia. Maximo 120 caracteres por frase.",
        f"Ideia: {idea}\nQuantidade: {count}",
    )
    phrases = []
    for line in text.splitlines():
        clean = re.sub(r"^[\s\-\*\d\.\)\]]+", "", line).strip().strip("\"'")
        if clean:
            phrases.append(clean[:140])
        if len(phrases) >= count:
            break
    if not phrases:
        raise ClipperError("o modelo nao devolveu frases; tente outra ideia.")
    return phrases


def _relative_to_repo(path: Path | None) -> str:
    """A path as the UI shows it: inside the repo when it fits, absolute when not.

    ``relative_to`` raises for a folder outside the project (a symlinked output
    directory, an absolute ``--output``). Showing that path is still better than
    failing the whole response, so the absolute form is the fallback.
    """
    if path is None:
        return ""
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


#: One batch at a time. Two selections writing into the same folder would fight
#: over the same file names and double the load on a site that already throttles;
#: the page posts, gets the job and then follows it.
_DOWNLOAD_SLOT = "download"

#: Same single-slot rule for the profile archive: one catalogue at a time, so
#: two runs never interleave files in output/instagram/ nor double the GraphQL
#: pressure on Instagram. The page polls /scrap/archive/progress like it does
#: for the selection download.
_ARCHIVE_SLOT = "archive"


def _archive_record(**fields) -> dict:
    """The state the arquivar-box paints while a profile archive runs.

    Mirrors _download_record on purpose: the page polls it the same way and
    reads the same core keys (active/state/phase/total/index/title/percent),
    plus the archive counters (reels/posts/photos/skipped/failed/downloaded).
    """
    record = {
        "active": False,
        "state": "ocioso",  # ocioso | listando | baixando | concluido | erro
        "phase": "",  # listando | item | bytes | skipped | photo | failed | done
        "total": 0,
        "index": 0,
        "title": "",
        "percent": 0.0,
        "item_fraction": 0.0,  # 0..1 progress inside the item being downloaded
        "current_line": "",  # the last yt-dlp line, so the page can echo it
        "archive_lines": [],  # every yt-dlp line, in order, for the log panel
        "items": [],  # one {code, folder, kind, thumb} per chosen item, for the cards
        "item_states": [],  # parallel phases ("", item, done, skipped, photo, failed)
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "photos": 0,
        "reels": 0,
        "posts": 0,
        "username": "",
        "root": "",
        "lines": [],
        "errors": [],
        "error": "",
    }
    record.update(fields)
    return record


#: Marca "deixe este campo como esta". Um `None` cru apagaria o valor no
#: merge abaixo (record.update), e nem todo publish quer tocar em todo campo.
_UNSET = object()


def _publish_archive(**fields) -> None:
    """Merge fields into the archive record, under the lock."""
    clean = {k: v for k, v in fields.items() if v is not _UNSET}
    with _lock:
        record = dict(_state.get(_ARCHIVE_SLOT) or _archive_record())
        record.update(clean)
        _state[_ARCHIVE_SLOT] = record


def _archive_worker(
    profile: str,
    cookies_file: str,
    kinds: list[str],
    limit: int | None,
    logger: CollectingLogger,
    order: str = "recent",
) -> None:
    """Run one profile archive off the request thread, publishing progress.

    Never raises: like _download_worker, the record is the channel and a dead
    thread would leave the page stuck on "Baixando…".
    """
    from viralclipper import archive as archive_mod

    _publish_archive(active=True, state="listando", phase="listando",
                     title=f"@{profile}", percent=0.0, index=0, total=0)
    try:
        listing = ig_profile_mod.list_profile(profile, cookies_file, logger)
    except ClipperError as exc:
        _publish_archive(active=False, state="erro", error=str(exc),
                         lines=logger.lines)
        return
    except Exception as exc:  # noqa: BLE001 - a thread must not die silently
        _publish_archive(active=False, state="erro", error=f"{exc!r}",
                         lines=logger.lines)
        return

    chosen = archive_mod.select_items(listing, kinds=kinds, limit=limit, order=order)
    if not chosen:
        _publish_archive(
            active=False, state="erro",
            error="o filtro não deixou nenhum item para baixar",
            lines=logger.lines, username=listing.username,
        )
        return

    root = (REPO_ROOT / "output" / "instagram").resolve()
    destination = root / archive_mod.safe_slug(
        listing.username, fallback="perfil", limit=40
    )
    total = len(chosen)
    # Cards da pagina: um retrato por item (codigo, pasta, tem-capa?) mais o
    # estado de cada um. As URLs das capas ficam fora do record — CDN expira
    # e a pagina busca os bytes em /scrap/archive/thumb por indice.
    # getattr porque os fakes de teste nao tem todos os campos.
    snapshots = [
        {
            "code": getattr(item, "code", "") or getattr(item, "pk", ""),
            "folder": getattr(item, "folder", ""),
            "kind": getattr(item, "kind", ""),
            "thumb": bool(getattr(item, "thumbnail", "")),
        }
        for item in chosen
    ]
    with _lock:
        _state["archive_items_full"] = [
            {
                "code": snap["code"],
                "thumbnail": getattr(item, "thumbnail", "") or "",
            }
            for snap, item in zip(snapshots, chosen)
        ]
    states = [""] * total
    _publish_archive(active=True, state="baixando", phase="item", total=total,
                     index=0, percent=0.0, username=listing.username,
                     title=f"@{listing.username} · {total} itens",
                     root=_relative_to_repo(destination),
                     items=snapshots, item_states=list(states))

    archive_lines: list[str] = []
    #: Fracao 0..1 do item que esta na mao, lida das linhas do yt-dlp. O notify
    #: do archive marca `position` como o item ATUAL (nao o fechado), entao
    #: position/total sozinho adianta a barra e a faz pular de item em item.
    inner = 0.0

    def progress(position: int, _total: int, code: str, phase: str) -> None:
        # `position` e o item na mao, nao o fechado. Publicar o limite inferior
        # (itens ja fechados) e deixar o on_line somar o pedaco do item atual:
        # assim a barra nunca anda para tras quando um item novo comeca.
        nonlocal inner
        fresh = phase in ("item", "bytes")
        if fresh:
            inner = 0.0  # item novo: o progresso do anterior nao vale mais
        closed = max(0, position - 1) if fresh else position
        done = min(1.0, closed / total) if total else 0.0
        if 1 <= position <= len(states):
            states[position - 1] = phase
        # Em item novo a linha do yt-dlp tambem e nova: zerar o current_line
        # evita mostrar o "99.0% of ..." do item anterior junto com frac 0.
        _publish_archive(index=position, title=code, phase=phase,
                         percent=round(done * 100.0, 1),
                         item_fraction=round(inner, 4),
                         current_line="" if fresh else _UNSET,
                         item_states=list(states))

    def on_line(line: str) -> None:
        # yt-dlp rewrites its progress in place with carriage returns; echo the
        # latest line so the page can show "1 de 20 · 54.9% of 230.98MiB"
        # instead of freezing the bar while one long reel downloads. Keep every
        # line too: the collapsible log panel shows the whole transcript of the
        # download, not just the last one.
        nonlocal inner
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", line or "")
        if match:
            try:
                inner = max(0.0, min(1.0, float(match.group(1).replace(",", ".")) / 100.0))
            except ValueError:
                pass
        archive_lines.append(line)
        # A barra tambem anda por dentro do item: republica o percentual sem
        # esperar o proximo notify, senao um reel longo congela a UI.
        idx = _state.get(_ARCHIVE_SLOT, {}).get("index", 0)
        if total and match:
            done = min(1.0, (max(0, idx - 1) + inner) / total)
            _publish_archive(percent=round(done * 100.0, 1), item_fraction=round(inner, 4),
                             current_line=line, archive_lines=list(archive_lines))
        else:
            _publish_archive(current_line=line, archive_lines=list(archive_lines))

    try:
        config = _options_to_config(
            {"url": "", "output": str(destination),
             "cookies_file": cookies_file}
        )
        summary = archive_mod.archive_profile(
            ig_profile_mod.ProfileListing(
                username=listing.username, title=listing.title,
                items=chosen, pages=listing.pages,
            ),
            destination, config, logger, on_progress=progress,
            on_line=on_line,
        )
    except ClipperError as exc:
        _publish_archive(active=False, state="erro", error=str(exc),
                         lines=logger.lines, archive_lines=list(archive_lines))
        return
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        _publish_archive(active=False, state="erro", error=f"{exc!r}",
                         lines=logger.lines, archive_lines=list(archive_lines))
        return

    _publish_archive(
        active=False,
        state="concluido",
        phase="done",
        index=total,
        percent=100.0,
        total=summary.total,
        downloaded=summary.downloaded,
        skipped=summary.skipped,
        failed=summary.failed,
        photos=summary.photos,
        reels=summary.reels,
        posts=summary.posts,
        username=summary.username,
        root=_relative_to_repo(summary.root),
        lines=summary.lines(),
        errors=[{"item": name, "error": error} for name, error in summary.errors],
        archive_lines=list(archive_lines),
    )


def _download_record(**fields) -> dict:
    """The state the scrap page paints while a batch runs.

    One flat record with every field always present: the page polls it twice a
    second and reads it directly, so a missing key would be a rendering bug
    rather than a smaller payload.
    """
    record = {
        "active": False,
        "state": "ocioso",  # ocioso | baixando | concluido | erro
        "phase": "",  # item | bytes | skipped | failed | done
        "total": 0,
        "index": 0,
        "title": "",
        "percent": 0.0,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "root": "",
        "lines": [],
        "errors": [],
        "error": "",
    }
    record.update(fields)
    return record


def _publish_download(**fields) -> None:
    """Merge fields into the download record, under the lock.

    The worker thread writes and the request thread reads, so this goes through
    ``_lock`` like every other piece of shared state.
    """
    with _lock:
        record = dict(_state.get(_DOWNLOAD_SLOT) or _download_record())
        record.update(fields)
        _state[_DOWNLOAD_SLOT] = record


def _thumb_dir() -> Path:
    """Where thumbnail bytes are cached, under the server's own directory.

    Deliberately NOT under ``output/``: that directory is the pipeline's
    deliverable folder, and a cache of other people's video frames does not
    belong in the same tree as the user's rendered clips.
    """
    path = WEB_DIR / _THUMB_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fetch_thumb_bytes(item: dict) -> bytes | None:
    """Resolve a thumbnail URL for one scrap result and return its bytes.

    A stored ``thumb`` wins: the Instagram listing already carries the signed
    CDN URL from the GraphQL payload, so a 300-item profile would otherwise pay
    300 extractions just to decorate the rows. yt-dlp is the fallback, for the
    flat-playlist shapes that carry no image at all.
    """
    url = str(item.get("url") or item.get("webpage_url") or "").strip()
    if not url:
        return None
    thumb = str(item.get("thumb") or "").strip()
    if not thumb:
        config = config_mod.ClipConfig(url=url)
        args = item.get("_ytdlp_args")
        if isinstance(args, list):
            config.extra_ytdlp_args = [str(a) for a in args]
        config.cache_dir = _thumb_dir().parent / "cache"
        meta = download_mod.fetch_metadata(url, config)
        thumb = str(meta.get("thumbnail") or "").strip()
        if not thumb:
            thumbs = meta.get("thumbnails")
            if isinstance(thumbs, list) and thumbs:
                last = thumbs[-1]
                if isinstance(last, dict):
                    thumb = str(last.get("url") or "").strip()
    if not thumb:
        return None
    request = Request(thumb, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - url comes from yt-dlp
        return response.read(_THUMB_MAX_BYTES)


def _scrap_thumb(item: dict) -> str | None:
    """Return the served path of one item's thumbnail, or None.

    Never raises. A thumbnail is decoration: a private post, an expired CDN
    signature or a slow host must leave the row intact and just without an
    image. Letting this bubble would turn a working list into "a busca falhou".
    """
    try:
        media_id = str(item.get("id") or "").strip()
        if not media_id:
            return None
        safe = "".join(ch for ch in media_id if ch.isalnum() or ch in "-_")[:64]
        if not safe:
            return None
        target = _thumb_dir() / f"{safe}.jpg"
        if not target.is_file():
            blob = _fetch_thumb_bytes(item)
            if not blob:
                return None
            tmp = target.with_suffix(".jpg.part")
            tmp.write_bytes(blob)
            tmp.replace(target)
        return f"/thumb/{quote(target.name)}"
    except Exception:  # noqa: BLE001 - decoration must never break the list
        return None


def _archive_thumb_path(position: int) -> Path | None:
    """Cached thumbnail file of the ``position``-th archived item, or None.

    Never raises: a capa e decoracao — um CDN expirado ou um item sem
    thumbnail deixa o card sem imagem, nunca derruba a lista.
    """
    try:
        with _lock:
            full = list(_state.get("archive_items_full") or [])
            username = (_state.get(_ARCHIVE_SLOT) or {}).get("username", "") or "perfil"
        if not 0 <= position < len(full):
            return None
        entry = full[position]
        url = str(entry.get("thumbnail") or "").strip()
        if not url.startswith("http"):
            return None
        safe = "".join(
            ch for ch in f"arch-{username}-{entry.get('code', '')}"
            if ch.isalnum() or ch in "-_"
        )[:64]
        if not safe:
            return None
        target = _thumb_dir() / f"{safe}.jpg"
        if not target.is_file():
            request = Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
            with urlopen(request, timeout=20) as response:  # noqa: S310 - url came from Instagram
                blob = response.read(_THUMB_MAX_BYTES)
            if not blob:
                return None
            tmp = target.with_suffix(".jpg.part")
            tmp.write_bytes(blob)
            tmp.replace(target)
        return target
    except Exception:  # noqa: BLE001 - decoration must never break the list
        return None


#: A channel URL with no tab resolves to a playlist OF playlists — "Videos",
#: "Live", "Shorts" — so expanding it yields three sub-tabs and zero videos.
#: yt-dlp explains this itself: "The URL does not have a videos tab, but it has
#: videos. Use .../@handle/videos". Appending the tab is what makes a pasted
#: profile URL behave the way someone pasting it expects.
_CHANNEL_TABS = ("videos", "shorts", "streams", "live", "playlists", "featured")

#: Path segments that mean "this URL names one item, not an account".
_ITEM_SEGMENTS = ("watch", "shorts", "embed", "v", "p", "reel", "tv", "video", "photo")


def _segments(url: str) -> tuple[str, list[str]]:
    """Split a URL into (host, path segments), ignoring query and fragment."""
    without_query = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    parts = [p for p in without_query.split("/") if p]
    if len(parts) < 2 or ":" not in parts[0]:
        return "", []
    return parts[1].lower(), parts[2:]


def _is_profile_url(url: str) -> bool:
    """True for an account URL rather than a single post."""
    host, tail = _segments(url)
    if not tail:
        return False
    # Any URL that names an item is a single video, whatever the site.
    if any(seg in _ITEM_SEGMENTS for seg in tail):
        return False
    if "tiktok.com" in host:
        return tail[0].startswith("@")
    if host.endswith("instagram.com"):
        return len(tail) == 1
    if "youtube.com" in host:
        return tail[0].startswith("@") or tail[0] in {"c", "user", "channel"}
    return False


def _with_videos_tab(url: str) -> str:
    """Point a YouTube channel URL at its videos tab when it has none."""
    host, tail = _segments(url)
    if "youtube.com" not in host or not tail:
        return url
    # /watch?v=ID has no item segment in the path — the id rides in the query.
    if "watch" in url.split("?", 1)[0].split("/") or "v=" in url:
        return url
    if any(seg in _CHANNEL_TABS for seg in tail):
        return url
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return base + "/videos"


def list_library(base: Path, limit: int = 200) -> list[dict]:
    """List the videos inside ``base``, newest first.

    The gallery only knows about the clips of the last job; this view exists so
    a file that was renamed by hand, produced by the CLI, or left over from an
    earlier run is still visible and playable.
    """
    if not base.is_dir():
        return []
    found: list[dict] = []
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in LIBRARY_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - file vanished mid-scan
            continue
        found.append(
            {
                "name": path.name,
                "rel": str(path.relative_to(base)).replace("\\", "/"),
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
            }
        )
    found.sort(key=lambda item: item["modified"], reverse=True)
    return found[:limit]


class UiServer(http_server.ThreadingHTTPServer):
    """HTTP server that refuses a silent duplicate bind on Windows.

    ``SO_REUSEADDR`` (the ``socketserver`` default on non-Windows) lets two
    processes listen on the same port at once on Windows; connections then go
    to an arbitrary one of them, which is how three orphaned servers ended up
    sharing port 7755. ``SO_EXCLUSIVEADDRUSE`` makes the second bind fail
    loudly instead.
    """

    allow_reuse_address = False

    def server_bind(self):
        import socket

        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(http_server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ViralClipper/1.0"

    # Thumbnails are served from /thumb/, i.e. from this same origin, instead of
    # being hot-linked from Instagram's CDN. That is what keeps the policy tight:
    # no `img-src https:` wildcard, and no per-CDN allowlist that would silently
    # blank every image the day a host changes. The styles and scripts are
    # external files served from 'self', so script-src needs no 'unsafe-inline';
    # it stays on style-src because the markup still carries inline style
    # attributes.
    CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "media-src 'self' blob:"
    )

    def end_headers(self) -> None:
        # Set on every response, JSON included: a policy applied only on the
        # HTML paths is one that a future route quietly escapes.
        self.send_header("Content-Security-Policy", self.CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _send_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, data: bytes, content_type: str, code: int = 200) -> None:
        # Range support so the <video> elements can seek and lazy-load: the
        # browser asks for "bytes=start-" chunks instead of whole files.
        range_header = self.headers.get("Range")
        if code == 200 and range_header and range_header.startswith("bytes="):
            start_str, _, end_str = range_header[len("bytes="):].partition("-")
            try:
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else len(data) - 1
            except ValueError:
                start, end = 0, len(data) - 1
            end = min(end, len(data) - 1)
            if 0 <= start <= end < len(data):
                chunk = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            index = WEB_DIR / "index.html"
            if index.exists():
                self._send_file(index.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send_json({"error": "index.html missing"}, 404)
            return
        if path in {"/templates", "/templates.html"}:
            page = WEB_DIR / "templates.html"
            if page.exists():
                self._send_file(page.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send_json({"error": "templates.html missing"}, 404)
            return
        if path in {"/scrap", "/scrap.html"}:
            page = WEB_DIR / "scrap.html"
            if page.exists():
                self._send_file(page.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send_json({"error": "scrap.html missing"}, 404)
            return
        if path == "/scrap/thumb":
            # The <img> tag hits this directly: a GET, not the POST above.
            # Both exist because only the caller knows which it needs — the
            # page uses the GET from the tag and lets the server do the work,
            # while the POST is there for a caller that already holds the item
            # dict and wants the resolved path back.
            query = parse_qs(urlparse(self.path).query)
            index = (query.get("i") or [""])[0]
            try:
                position = int(index)
            except (TypeError, ValueError):
                self._send_json({"error": "bad index"}, 400)
                return
            self._send_thumb_at(position, (query.get("s") or [""])[0])
            return
        if path == "/scrap/archive/thumb":
            # Capa do N-esimo item do ultimo arquivamento, para os cards do
            # progresso. Posicao em vez de id: a pagina monta o <img> direto.
            query = parse_qs(urlparse(self.path).query)
            try:
                position = int((query.get("i") or [""])[0])
            except (TypeError, ValueError):
                self._send_json({"error": "bad index"}, 400)
                return
            target = _archive_thumb_path(position)
            if target is None or not target.is_file():
                self._send_json({"error": "no thumbnail"}, 404)
                return
            self._send_file(target.read_bytes(), "image/jpeg")
            return
        if path.startswith("/thumb/"):
            # Cached thumbnail bytes. The name is validated against the cache
            # directory itself rather than pattern-matched: ``..\..\`` and an
            # absolute path both resolve outside the folder, and the resolved
            # path is what decides.
            name = unquote(path[len("/thumb/"):])
            candidate = (_thumb_dir() / name).resolve()
            try:
                candidate.relative_to(_thumb_dir().resolve())
            except ValueError:
                self._send_json({"error": "not found"}, 404)
                return
            if candidate.is_file():
                self._send_file(candidate.read_bytes(), "image/jpeg")
            else:
                self._send_json({"error": "not found"}, 404)
            return
        if path == "/templates/catalog":
            # The zone kinds and caption presets the wizard offers, read from
            # the engine rather than duplicated a third time in the page. The
            # page keeps its own copy for the numeric preview; this endpoint is
            # what keeps the two from drifting silently.
            from viralclipper import caption_presets, template as template_mod

            self._send_json(
                {
                    "presets": sorted(caption_presets.PRESETS),
                    "zone_kinds": list(template_mod.ZONE_KINDS),
                    "fit_modes": list(template_mod.FIT_MODES),
                    "builtin": sorted(template_mod.BUILTIN),
                }
            )
            return
        if path == "/templates/plates":
            # The ready-made text plates, read from disk rather than listed in the
            # page: dropping a file into the folder is how the user adds one, and a
            # list hardcoded here would be a second truth to keep in step.
            self._send_json({"plates": list_plates()})
            return
        if path == "/status":
            with _lock:
                snapshot = dict(_state)
            # The download/archive records have their own endpoints; keeping them
            # out of /status leaves that payload the {jobs, clips} contract.
            snapshot.pop(_DOWNLOAD_SLOT, None)
            snapshot.pop(_ARCHIVE_SLOT, None)
            self._send_json(snapshot)
            return
        if path == "/scrap/download/progress":
            with _lock:
                record = dict(_state.get(_DOWNLOAD_SLOT) or _download_record())
            self._send_json(record)
            return
        if path == "/scrap/archive/progress":
            with _lock:
                record = dict(_state.get(_ARCHIVE_SLOT) or _archive_record())
            self._send_json(record)
            return
        if path == "/library":
            # Everything playable in the output folder, not just the last job.
            base = (REPO_ROOT / "output").resolve()
            self._send_json({"files": list_library(base)})
            return
        if path.startswith("/clips/"):
            # Serve a rendered clip from the output dir. The UI passes the
            # relative path it received from /run. The browser percent-encodes
            # accented file names ("nao" is fine, "não" arrives as "na%C3%A3o"),
            # so the segment must be decoded before it touches the filesystem.
            rel = unquote(path[len("/clips/"):])
            base = (REPO_ROOT / "output").resolve()
            candidate = resolve_within(base, rel)
            if candidate is None:
                # A traversal attempt and a missing file get the same answer:
                # neither reveals anything about the filesystem.
                self._send_json({"error": "not found"}, 404)
                return
            ctype = "video/mp4" if candidate.suffix.lower() == ".mp4" else "application/octet-stream"
            self._send_file(candidate.read_bytes(), ctype)
            return
        # Static assets inside web/ (also percent-decoded, same reason).
        asset = resolve_within(WEB_DIR, unquote(path.lstrip("/")))
        if asset is not None:
            # The pages keep their CSS, JS and preview media in sibling files,
            # so the type has to be named correctly: nosniff is on, and a
            # script served as octet-stream is refused by the browser while the
            # hero video just never plays.
            ctype = asset_content_type(asset)
            self._send_file(asset.read_bytes(), ctype)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid json"}, 400)
            return
        if path == "/scrap":
            self._handle_scrap(payload)
            return
        if path == "/scrap/thumb":
            self._handle_thumb(payload)
            return
        if path == "/scrap/archive":
            self._handle_archive(payload)
            return
        if path == "/scrap/download":
            self._handle_selected_download(payload)
            return
        if path == "/transcript/normalize":
            self._handle_normalize(payload)
            return
        if path == "/phrases":
            self._handle_phrases(payload)
            return
        if path != "/run":
            self._send_json({"error": "not found"}, 404)
            return
        options = payload.get("options") or {}
        plan_only = bool(payload.get("plan_only"))
        if not str(options.get("url") or "").strip():
            self._send_json({"error": "url is required"}, 400)
            return
        result = _run_job(options, plan_only)
        self._send_json(result)

    def _handle_thumb(self, payload: dict) -> None:
        """Resolve one result's thumbnail after the list is already on screen.

        Two reasons this is a separate call rather than part of ``/scrap``:
        Instagram flat entries carry no image, so every thumbnail costs a full
        per-video metadata extraction — on a 100-item profile that would turn a
        fast list into a minute of waiting. And the failures are per item: one
        private post must not blank the other 99 rows.
        """
        item = payload.get("item")
        if not isinstance(item, dict):
            self._send_json({"error": "item is required"}, 400)
            return
        self._send_json({"thumb": _scrap_thumb(item)})

    def _send_thumb_at(self, position: int, _stamp: str) -> None:
        """Serve the thumbnail of the ``position``-th item of the last search.

        Keyed by position rather than by id so the page can put the URL straight
        into an ``<img src>`` without first knowing the id — and so the server,
        not the page, decides what "the item at row 3" currently means. ``_stamp``
        is only a cache-buster and is deliberately unused: the page bumps it to
        force a refetch when a new search reuses the same row numbers.
        """
        with _lock:
            last = list(_state.get("scrap") or [])
        if not 0 <= position < len(last):
            self._send_json({"error": "not found"}, 404)
            return
        served = _scrap_thumb(last[position])
        if not served:
            # 404, not an empty image: the page watches for the error event and
            # falls back to the placeholder, which is the honest outcome.
            self._send_json({"error": "no thumbnail"}, 404)
            return
        candidate = _thumb_dir() / Path(served).name
        if not candidate.is_file():
            self._send_json({"error": "no thumbnail"}, 404)
            return
        self._send_file(candidate.read_bytes(), "image/jpeg")

    def _handle_scrap(self, payload: dict) -> None:
        """Answer the ViceScrap search box.

        Read-only: it enumerates what a URL yields and hands the list back. It
        runs on the request thread and is a metadata fetch, not a download, so
        there is no job to queue — the pick from the list is what feeds /run.

        Failures go back as the same one-line cause the CLI prints, so an
        expired cookie or a private post reads the same in both places.
        """
        try:
            results, title, removed = _scrap_results(payload)
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self._send_json({"error": f"{exc!r}"}, 400)
            return
        self._send_json({"title": title, "count": len(results), "results": results,
                         "removed": removed})
        # Kept so /scrap/thumb can serve the k-th row by index. The page holds
        # the same list, but re-deriving it there means sending every item back
        # through the API, and the URL has to be buildable from the row alone.
        # ``_ytdlp_args`` travels with it because the thumbnail fetch has to
        # repeat the same authenticated request the search made.
        with _lock:
            _state["scrap"] = results

    def _handle_archive(self, payload: dict) -> None:
        """Baixa o catalogo de um perfil para ``reels/`` e ``posts/``.

        Diferente de ``/scrap`` (que so lista metadados) e de ``/run`` (que
        analisa UM video e produz clipes): aqui o produto e um arquivo da conta
        inteira. Responde o ACEITE e roda numa thread — um perfil com dezenas
        de itens leva minutos, e a pagina acompanha o record em
        ``/scrap/archive/progress`` com a barra de progresso.
        """
        profile = str(payload.get("profile") or "").strip()
        cookies_file = str(payload.get("cookies_file") or "").strip()
        if not profile:
            self._send_json({"error": "informe o perfil"}, 400)
            return
        if not cookies_file:
            self._send_json(
                {"error": "Informe o arquivo cookies.txt: o Instagram só devolve "
                          "o catálogo para uma sessão autenticada."},
                400,
            )
            return

        # Antes da thread: o worker le o MESMO arquivo, entao a sessao colada
        # tem de estar gravada nele quando o download comecar — e o yt-dlp, que
        # baixa a midia, tambem le esse arquivo.
        _ig_session_from_payload(payload)

        only = str(payload.get("only") or "").strip()
        kinds = [part.strip() for part in only.split(",") if part.strip()]
        valid = {ig_profile_mod.REELS_DIR, ig_profile_mod.POSTS_DIR}
        unknown = [k for k in kinds if k.lower() not in valid]
        if unknown:
            self._send_json(
                {"error": f"only aceita {', '.join(sorted(valid))}"}, 400
            )
            return

        try:
            limit = int(payload.get("max_items") or 0) or None
        except (TypeError, ValueError):
            limit = None

        order = str(payload.get("order") or "").strip().lower()
        if order not in ("", "recent", "viral"):
            self._send_json(
                {"error": "order aceita recent ou viral"}, 400
            )
            return
        if not order:
            order = "recent"

        with _lock:
            current = _state.get(_ARCHIVE_SLOT) or {}
            if current.get("active"):
                self._send_json(
                    {"error": "já existe um arquivamento em andamento; espere ele terminar"},
                    409,
                )
                return
            _state[_ARCHIVE_SLOT] = _archive_record(
                active=True, state="listando", phase="listando",
                title=f"@{profile}",
            )

        logger = CollectingLogger()
        threading.Thread(
            target=_archive_worker,
            args=(profile, cookies_file, kinds, limit, logger, order),
            name="scrap-archive",
            daemon=True,
        ).start()

        self._send_json({"started": True, "profile": profile})

    def _handle_selected_download(self, payload: dict) -> None:
        """Baixa exatamente os itens marcados na lista de resultados.

        Diferente de ``/scrap/archive`` (que le o catalogo de um perfil do
        Instagram a partir de cookies), aqui a lista ja esta na tela e o que
        chega e a selecao do usuario: cada URL e baixada por si, sem depender de
        sessao nem de o site ter um catalogo paginado. Serve para YouTube,
        TikTok e Instagram igualmente.

        Roda sincrono, como ``/scrap`` e ``/scrap/archive``: o resultado que
        importa e quantos arquivos foram escritos e onde.
        """
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            self._send_json({"error": "nenhum item selecionado"}, 400)
            return

        targets: list[download_mod.MediaTarget] = []
        for raw in raw_items[:_MAX_SELECTED_DOWNLOADS]:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            targets.append(
                download_mod.MediaTarget(
                    url=url,
                    media_id=str(raw.get("id") or ""),
                    title=str(raw.get("title") or ""),
                )
            )
        if not targets:
            self._send_json({"error": "nenhum item selecionado tem URL"}, 400)
            return

        # Uma pasta por busca, para duas buscas nao misturarem arquivos. O nome
        # vem do titulo da lista ("ANCAPSU - Vídeos"), que e o que o usuario ve.
        collection = str(payload.get("collection") or "").strip()
        slug = util.slugify(collection, fallback="selecionados", max_length=40)
        root = (REPO_ROOT / "output" / "downloads" / slug).resolve()

        cookies_file = str(payload.get("cookies_file") or "").strip()
        # A sessao colada tambem vale aqui: quem baixa a midia e o yt-dlp, e ele
        # le o MESMO jar. Sem gravar, uma busca autenticada seguida de download
        # baixaria os itens como anonimo.
        _ig_session_from_payload(payload)
        logger = CollectingLogger()
        try:
            config = _options_to_config(
                {"url": "", "output": str(root), "cookies_file": cookies_file}
            )
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return

        # O download roda fora da thread da requisicao: uma selecao de vinte
        # videos leva minutos, e uma requisicao que fica minutos sem responder
        # nao tem como mostrar progresso nenhum. A pagina recebe o aceite e
        # acompanha o record em /scrap/download/progress.
        with _lock:
            current = _state.get(_DOWNLOAD_SLOT) or {}
            if current.get("active"):
                self._send_json(
                    {"error": "já existe um download em andamento; espere ele terminar"},
                    409,
                )
                return
            _state[_DOWNLOAD_SLOT] = _download_record(
                active=True,
                state="baixando",
                phase="item",
                total=len(targets),
                index=0,
                root=_relative_to_repo(root),
            )

        threading.Thread(
            target=_download_worker,
            args=(targets, root, config, logger),
            name="scrap-download",
            daemon=True,
        ).start()

        self._send_json(
            {
                "started": True,
                "total": len(targets),
                "root": _relative_to_repo(root),
            }
        )

    def _handle_normalize(self, payload: dict) -> None:
        """Clean a pasted transcript and return it minute-aligned."""
        raw = payload.get("transcript")
        if not isinstance(raw, str) or not raw.strip():
            self._send_json({"error": "transcript is required"}, 400)
            return
        try:
            result = transcript_import.normalize_transcript(raw)
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        self._send_json(result.to_dict())

    def _handle_phrases(self, payload: dict) -> None:
        """Suggest short hook phrases for a video idea, via the ranker LLM."""
        idea = payload.get("idea")
        if not isinstance(idea, str) or not idea.strip():
            self._send_json({"error": "idea is required"}, 400)
            return
        try:
            count = max(1, min(10, int(payload.get("count") or 5)))
        except (TypeError, ValueError):
            count = 5
        try:
            phrases = _suggest_phrases(idea.strip(), count)
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001 - network errors surface as text
            self._send_json({"error": f"modelo indisponivel: {exc}"}, 400)
            return
        self._send_json({"phrases": phrases})

    def log_message(self, *args) -> None:  # keep the console quiet
        return


def _parse_port(argv: list[str]) -> int:
    """Read ``--port N`` (or ``PORT=N``) from the command line.

    The port used to be a constant, which made a stale listener a hard stop with
    no way out except killing processes. It stays defaulting to 7755 so the
    documented URL never changes.
    """
    for index, arg in enumerate(argv):
        if arg in {"--port", "-p"} and index + 1 < len(argv):
            try:
                candidate = int(argv[index + 1])
            except ValueError:
                continue
            if 1 <= candidate <= 65535:
                return candidate
        elif arg.startswith("--port="):
            try:
                candidate = int(arg.split("=", 1)[1])
            except ValueError:
                continue
            if 1 <= candidate <= 65535:
                return candidate
    return PORT


def main(argv: list[str] | None = None) -> int:
    # Same rationale as viralclipper.cli: job logs carry video titles, and a
    # legacy code page would turn the first combining mark into a crash.
    util.configure_stdio()
    port = _parse_port(list(argv if argv is not None else sys.argv[1:]))
    addr = (HOST, port)
    try:
        httpd = UiServer(addr, Handler)
    except OSError as exc:
        print(f"[!] Nao foi possivel abrir a porta {port}: {exc}")
        print(f"[!] Ja existe uma viral-clipper web UI rodando na porta {port}?")
        print(f"[!] Use --port {port + 1} para subir numa porta livre.")
        return 1
    url = f"http://{HOST}:{port}/"
    print(f"[*] viral-clipper web UI: {url}")
    print(f"[*] templates: {url}templates")
    print("[*] CTRL+C para parar")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - headless environments
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] encerrando")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
