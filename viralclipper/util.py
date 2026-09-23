"""Small shared helpers: process execution, logging and time formatting."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


class ClipperError(RuntimeError):
    """Raised for expected, user-facing failures (bad URL, missing tool, ...)."""


def configure_stdio() -> None:
    """Force UTF-8 stdio with replacement on error.

    On Windows, a redirected stdout (log file, pipe, ``Start-Process
    -RedirectStandardOutput``) falls back to the legacy ANSI code page
    (cp1252 on pt-BR), which cannot encode combining marks such as U+0327 —
    present in decomposed YouTube titles like "Ligações". Without this, the
    first log line of a run kills the whole process with UnicodeEncodeError.
    Idempotent; a stream without ``reconfigure`` (e.g. a test double) is left
    alone.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


class Logger:
    """Minimal logger that keeps output readable and optionally quiet."""

    def __init__(self, quiet: bool = False, verbose: bool = False) -> None:
        self.quiet = quiet
        self.verbose = verbose

    def _emit(self, prefix: str, message: str) -> None:
        if self.quiet and prefix != "!":
            return
        try:
            print(f"{prefix} {message}", flush=True)
        except OSError:
            # A console can die mid-run (window closed, orphaned server): a
            # broken stdout must never abort the pipeline. Web clients still
            # get the line through the collecting logger, which records it
            # before calling this method.
            pass

    def info(self, message: str) -> None:
        self._emit("[*]", message)

    def step(self, message: str) -> None:
        self._emit("[>]", message)

    def ok(self, message: str) -> None:
        self._emit("[+]", message)

    def warn(self, message: str) -> None:
        self._emit("!", message)

    def debug(self, message: str) -> None:
        if self.verbose and not self.quiet:
            self._emit("[.]", message)


def require_binary(name: str, hint: str = "") -> str:
    """Return the absolute path of ``name`` or raise a helpful error."""
    found = shutil.which(name)
    if not found:
        extra = f" {hint}" if hint else ""
        raise ClipperError(f"Required binary '{name}' was not found on PATH.{extra}")
    return found


def module_available(name: str) -> bool:
    """True when the optional module ``name`` can be imported.

    Used to gate optional features (face detection, hosted rankers) so a
    missing dependency degrades into a fallback instead of an ImportError.
    """
    return importlib.util.find_spec(name) is not None


# Thread pools created by numpy's BLAS backend and by OpenMP. Each one sizes its
# scratch buffers from the core count, and they are inherited by child processes.
_NATIVE_THREAD_VARS: tuple[str, ...] = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def limit_native_threads(threads: int = 1) -> None:
    """Cap the BLAS/OpenMP thread pools inherited by child processes.

    A render worker imports numpy, whose BLAS backend allocates per-thread
    scratch space sized from the core count. With several workers on a machine
    where memory is the scarce resource, that allocation fails and the pool dies
    with ``BrokenProcessPool`` - the parallelism is already across processes, so
    one thread each is both cheaper and faster here.

    Existing values are respected, so a user who tuned these keeps their setting.
    Must run in the parent *before* the pool is created, because children
    inherit the environment rather than this module's state.
    """
    for name in _NATIVE_THREAD_VARS:
        os.environ.setdefault(name, str(threads))


def ffmpeg_binaries(ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> tuple[str, str]:
    """Resolve ffmpeg and ffprobe once, so every stage uses the same pair."""
    return require_binary(ffmpeg), require_binary(ffprobe)


# Proxy variables an embedding host may set for its own traffic (an IDE with a
# local interception proxy, a corporate VPN shim). Inherited by the tools we
# spawn, they route yt-dlp's downloads through a proxy that has no business
# seeing them - and one that is often only listening for the host's own
# requests. The first symptom is not a proxy error: it is a generic
# "Unable to extract data", because the proxy answered instead of the site.
_PROXY_VARS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

# Set to "1" to pass the ambient proxy through anyway.
KEEP_PROXY_ENV = "VIRALCLIPPER_KEEP_PROXY"


def scrub_proxy_env(env: dict[str, str]) -> dict[str, str]:
    """Drop inherited proxy settings unless the user opted back in.

    Only the *ambient* variables are touched: a proxy the user asks for lives in
    ``config.extra_ytdlp_args`` (``--proxy``), which is explicit and survives.
    """
    if env.get(KEEP_PROXY_ENV) == "1":
        return env
    for name in _PROXY_VARS:
        env.pop(name, None)
    return env


def run(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    logger: Logger | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command, capture output and raise ``ClipperError`` on failure."""
    if logger:
        logger.debug("run: " + " ".join(str(part) for part in cmd))
    env = scrub_proxy_env(dict(os.environ))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and proc.returncode != 0:
        tail = tail_lines(proc.stdout or "", 12)
        raise ClipperError(
            f"Command failed with exit code {proc.returncode}: "
            f"{' '.join(str(part) for part in cmd)}\n{tail}"
        )
    return proc


def run_streaming(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    logger: Logger | None = None,
    check: bool = True,
) -> int:
    """Run a long command (yt-dlp, ffmpeg) letting it write to the terminal."""
    if logger:
        logger.debug("run: " + " ".join(str(part) for part in cmd))
    proc = subprocess.run([str(part) for part in cmd], cwd=str(cwd) if cwd else None)
    if check and proc.returncode != 0:
        raise ClipperError(
            f"Command failed with exit code {proc.returncode}: "
            f"{' '.join(str(part) for part in cmd)}"
        )
    return proc.returncode


def run_streaming_captured(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    logger: Logger | None = None,
    echo: bool = True,
) -> tuple[int, str]:
    """Run a long command, echoing its output and keeping a copy.

    ``run_streaming`` hands the console to the child, so a failing yt-dlp
    prints its reason where the tool can no longer read it - which is how a
    download failure ends up reported as a bare "exit code 1". This variant
    streams every chunk straight through (progress bars keep their carriage
    returns) while accumulating the tail of the output for the caller to turn
    into an actionable message.

    Returns ``(returncode, captured_output)``.
    """
    if logger:
        logger.debug("run: " + " ".join(str(part) for part in cmd))
    env = scrub_proxy_env(dict(os.environ))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        [str(part) for part in cmd],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    captured: list[str] = []
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        text = chunk.decode("utf-8", "replace")
        captured.append(text)
        if echo:
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except OSError:
                # A dead console must not abort a download in progress.
                echo = False
    proc.wait()
    # Only the tail matters for diagnostics, and it keeps memory flat on a
    # multi-minute render.
    output = "".join(captured)[-8000:]
    if logger:
        for line in output.splitlines():
            if line.strip():
                logger.debug(line.rstrip())
    return proc.returncode, output


def tail_lines(text: str, count: int) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-count:])


def json_from_command(cmd: list[str], logger: Logger | None = None) -> dict:
    """Run a command whose stdout is a JSON document and parse it."""
    proc = run(cmd, logger=logger)
    out = (proc.stdout or "").strip()
    if not out:
        raise ClipperError(f"Command produced no JSON output: {' '.join(cmd)}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        start = out.find("{")
        end = out.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(out[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ClipperError(f"Could not parse JSON output: {exc}") from exc


def fmt_clock(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS`` (or ``MM:SS`` below one hour)."""
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def fmt_clock_ms(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS.mmm`` (used by ffmpeg and ffprobe)."""
    seconds = max(0.0, float(seconds))
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def ass_timestamp(seconds: float) -> str:
    """Format seconds for ASS subtitle timestamps (``H:MM:SS.cc``)."""
    seconds = max(0.0, float(seconds))
    centis = int(round(seconds * 100))
    hours, rest = divmod(centis, 360_000)
    minutes, rest = divmod(rest, 6_000)
    secs, cs = divmod(rest, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def slugify(text: str, fallback: str = "clip", max_length: int = 48) -> str:
    """Turn arbitrary text into a safe file name fragment."""
    keep: list[str] = []
    for char in text.lower():
        if char.isalnum():
            keep.append(char)
        elif char in " -_":
            keep.append("_")
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug[:max_length].strip("_")
    return slug or fallback


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def probe_duration(ffprobe: str, media: str | Path, logger: Logger | None = None) -> float:
    """Return the duration of a media file in seconds (0.0 when unknown)."""
    proc = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media),
        ],
        logger=logger,
        check=False,
    )
    try:
        return float((proc.stdout or "0").strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0.0


def probe_start_time(ffprobe: str, media: str | Path, logger: Logger | None = None) -> float:
    """Return the container start timestamp in seconds (0.0 when unknown).

    This is what tells a yt-dlp section download apart: a file whose timeline
    was reset starts at 0, while one that kept the source timestamps starts at
    the section offset. ``render_clip`` needs to know which, because it seeks
    into the file it was given.
    """
    proc = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=start_time",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media),
        ],
        logger=logger,
        check=False,
    )
    raw = (proc.stdout or "").strip().splitlines()
    if not raw:
        return 0.0
    try:
        value = float(raw[0])
    except ValueError:
        return 0.0
    return value if value > 0.0 else 0.0


def probe_video_size(
    ffprobe: str, media: str | Path, logger: Logger | None = None
) -> tuple[int, int]:
    """Return ``(width, height)`` of the first video stream, ``(0, 0)`` if absent."""
    proc = run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(media),
        ],
        logger=logger,
        check=False,
    )
    first = (proc.stdout or "").strip().splitlines()
    if not first:
        return 0, 0
    parts = first[0].split("x")
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def python_module_command(module: str, *args: str) -> list[str]:
    """Build ``python -m module ...`` so yt-dlp is not required on PATH."""
    return [sys.executable, "-m", module, *args]
