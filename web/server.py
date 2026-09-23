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

import json
import threading
import webbrowser
from http import server as http_server
from pathlib import Path
from urllib.parse import urlparse, unquote

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent

# Import the package itself; the server must run from the repo root so
# `viralclipper` resolves, but __file__ lets us be explicit.
import sys
sys.path.insert(0, str(REPO_ROOT))

from viralclipper import cli as cli_mod  # noqa: E402
from viralclipper import config as config_mod  # noqa: E402
from viralclipper import pipeline, report, transcript_import, util, viral_report  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402

HOST = "127.0.0.1"
PORT = 7755

# Shared state. The HTTP handler runs on one thread; runs happen on a worker
# thread, so access goes through the lock.
_lock = threading.Lock()
_state: dict = {"jobs": [], "clips": []}


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
        if path != "/run":
            if path == "/transcript/normalize":
                self._handle_normalize(payload)
                return
            self._send_json({"error": "not found"}, 404)
            return
        options = payload.get("options") or {}
        plan_only = bool(payload.get("plan_only"))
        if not str(options.get("url") or "").strip():
            self._send_json({"error": "url is required"}, 400)
            return
        result = _run_job(options, plan_only)
        self._send_json(result)

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


def main() -> int:
    # Same rationale as viralclipper.cli: job logs carry video titles, and a
    # legacy code page would turn the first combining mark into a crash.
    util.configure_stdio()
    addr = (HOST, PORT)
    try:
        httpd = UiServer(addr, Handler)
    except OSError as exc:
        print(f"[!] Nao foi possivel abrir a porta {PORT}: {exc}")
        print(f"[!] Ja existe uma viral-clipper web UI rodando na porta {PORT}?")
        print("[!] Feche as janelas antigas (ou encerre os processos python antigos) e tente de novo.")
        return 1
    url = f"http://{HOST}:{PORT}/"
    print(f"[*] viral-clipper web UI: {url}")
    print(f"[*] servindo {WEB_DIR / 'index.html'}")
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
