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
import webbrowser
from http import server as http_server
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent

# Import the package itself; the server must run from the repo root so
# `viralclipper` resolves, but __file__ lets us be explicit.
import sys
sys.path.insert(0, str(REPO_ROOT))

from viralclipper import config as config_mod  # noqa: E402
from viralclipper import download as download_mod  # noqa: E402
from viralclipper import pipeline, report, transcript_import, util, viral_report  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402

HOST = "127.0.0.1"
PORT = 7755

# Shared state. The HTTP handler runs on one thread; runs happen on a worker
# thread, so access goes through the lock.
_lock = threading.Lock()
_state: dict = {"jobs": [], "clips": [], "scrap": []}


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


def _scrap_results(payload: dict) -> tuple[list[dict], str]:
    """Expand a link or a profile into a list of downloadable videos.

    Two modes, and the difference is one yt-dlp flag:

    - ``link``  -> a single post/reel/video. Answered by ``fetch_metadata``,
      which already exists and is already what the CLI uses.
    - ``profile`` -> the whole feed of an account. Here the flat playlist is
      read instead: one entry per item, no per-video extraction, which is the
      only shape that stays fast when the account has hundreds of posts.

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
    else:
        # Only reach here for a profile-shaped URL. A bare profile name is
        # accepted too, but a full URL is what yt-dlp can resolve without
        # guessing the site.
        limit = payload.get("limit")
        try:
            limit = max(1, min(int(limit), 100)) if limit is not None else 20
        except (TypeError, ValueError):
            limit = 20
        config.extra_ytdlp_args += ["--flat-playlist", "--playlist-end", str(limit)]
        info = download_mod.fetch_metadata(_with_videos_tab(url), config)
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
        entries = flattened[:limit]

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
    return results, title


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

    Instagram flat entries carry no image at all, so this asks yt-dlp for the
    single-video metadata, which does. The call is the expensive part of a
    search: ``_scrap_thumb`` is what keeps it off the critical path.
    """
    url = str(item.get("url") or item.get("webpage_url") or "").strip()
    if not url:
        return None
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
    # blank every image the day a host changes. `'unsafe-inline'` is required
    # because each page is a single file with its markup, style and script inline.
    CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline'; "
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
        if path == "/status":
            with _lock:
                self._send_json(dict(_state))
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
            ctype = "text/css; charset=utf-8" if asset.suffix == ".css" else "application/octet-stream"
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
        if path == "/transcript/normalize":
            self._handle_normalize(payload)
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
            results, title = _scrap_results(payload)
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self._send_json({"error": f"{exc!r}"}, 400)
            return
        self._send_json({"title": title, "count": len(results), "results": results})
        # Kept so /scrap/thumb can serve the k-th row by index. The page holds
        # the same list, but re-deriving it there means sending every item back
        # through the API, and the URL has to be buildable from the row alone.
        # ``_ytdlp_args`` travels with it because the thumbnail fetch has to
        # repeat the same authenticated request the search made.
        with _lock:
            _state["scrap"] = results

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
