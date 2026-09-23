"""Measure whisper throughput on this machine, per checkpoint.

The strategic question for turning the clipper into a business is not "does it
work" but "how long does one video take, and does that fit in a batch". This
script answers it with numbers instead of guesses: for each checkpoint it loads
the model, transcribes a real audio file, and reports the wall time, the
real-time factor (audio seconds processed per wall second) and the peak RSS.

Real-time factor is the number that matters: an RTF of 0.2 means a one hour
video costs five minutes of CPU, so a 20 video batch costs under two hours. An
RTF below 1 means the machine cannot transcribe faster than playback and a batch
will not fit in a day.

    python bench_transcribe.py --wav path/to/analysis.wav
    python bench_transcribe.py --url "https://youtu.be/..." --models tiny,base
    python bench_transcribe.py --wav a.wav --models small,base,tiny --repeat 2

Each checkpoint runs in its own child process. That is not ceremony: peak RSS
only ever grows within a process, so measuring several models in one process
would report the first model's footprint for all of them. A child per model also
means every checkpoint pays the same native-library start-up cost, which keeps
the load times comparable.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from viralclipper import audio as audio_mod  # noqa: E402
from viralclipper import transcribe, util  # noqa: E402
from viralclipper.config import ClipConfig  # noqa: E402
from viralclipper.util import ClipperError, Logger  # noqa: E402

DEFAULT_MODELS = "small,base,tiny"
CHILD_FLAG = "--_bench-child"


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_rss_mb() -> float:
    """Peak resident set size of this process in MB, or ``-1`` when unknown.

    ``resource.getrusage`` reports ``ru_maxrss`` on POSIX and a stub on Windows,
    so the Windows path goes through ``GetProcessMemoryInfo`` instead. The
    process handle must be declared ``c_void_p``: the default ``c_int`` return
    type truncates it on 64-bit and the call then fails silently.
    """
    if sys.platform == "win32":
        try:
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            handle = kernel32.GetCurrentProcess()
        except Exception:  # noqa: BLE001 - a missing counter must not fail the bench
            return -1.0

        # K32GetProcessMemoryInfo ships with kernel32 since Windows 7; psapi is
        # the older home of the same call.
        for dll_name, function_name in (
            ("kernel32", "K32GetProcessMemoryInfo"),
            ("psapi", "GetProcessMemoryInfo"),
        ):
            try:
                function = getattr(ctypes.WinDLL(dll_name, use_last_error=True), function_name)
            except (AttributeError, OSError):
                continue
            function.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_ProcessMemoryCounters),
                ctypes.c_ulong,
            ]
            function.restype = ctypes.c_int
            if function(handle, ctypes.byref(counters), counters.cb):
                return counters.PeakWorkingSetSize / (1024 * 1024)
        return -1.0
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes, macOS reports bytes.
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return usage / divisor
    except Exception:  # noqa: BLE001
        return -1.0


def available_ram_gb() -> tuple[float, float]:
    """Total and available physical RAM in GB, or ``(0, 0)`` when unknown."""
    if sys.platform != "win32":
        return 0.0, 0.0
    try:
        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullTotalPhys / 2**30, status.ullAvailPhys / 2**30
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def bench_child(model: str, wav: str, duration: float) -> int:
    """Measure one checkpoint and print the result as JSON.

    Runs in a fresh process so the peak memory reading belongs to this
    checkpoint alone.
    """
    config = ClipConfig(url="bench")
    config.whisper_device = "cpu"
    config.whisper_compute_type = "int8"
    config.whisper_model = model

    result: dict = {"model": model, "ok": False}
    try:
        started = time.perf_counter()
        loaded = transcribe.load_model(config, None)
        result["load_seconds"] = time.perf_counter() - started
        result["resolved"] = loaded.name
        result["fell_back"] = loaded.name != model

        started = time.perf_counter()
        transcript = transcribe.transcribe(wav, config, None, model=loaded)
        result["seconds"] = time.perf_counter() - started
        result["rtf"] = duration / result["seconds"] if result["seconds"] > 0 else 0.0
        result["words"] = len(transcript.words)
        result["language"] = transcript.language
        result["ok"] = True
    except ClipperError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - a crashed checkpoint is a result too
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["peak_mb"] = peak_rss_mb()
    print(json.dumps(result))
    return 0


def bench_one(model: str, wav: Path, duration: float) -> dict:
    """Run one checkpoint in a child process and parse its JSON line."""
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            CHILD_FLAG,
            model,
            str(wav),
            repr(duration),
        ],
        capture_output=True,
        text=True,
    )
    stdout = (proc.stdout or "").strip()
    if stdout:
        try:
            return json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            pass
    detail = (proc.stderr or "").strip().splitlines()
    return {
        "model": model,
        "ok": False,
        "error": detail[-1][:200] if detail else "the child process produced no result",
    }


def prepare_wav(url: str, work: Path, logger: Logger) -> tuple[Path, float]:
    """Download and decode a URL into the mono 16 kHz WAV the pipeline uses."""
    from viralclipper import download

    config = ClipConfig(url=url)
    logger.step("Downloading the audio track")
    source = util.ensure_dir(work) / "source_audio"
    audio_source = download.download_audio(url, source, config, logger)
    wav = work / "analysis.wav"
    audio_mod.extract_audio(config.ffmpeg, audio_source, wav, logger=logger)
    return wav, audio_mod.analyze_audio(wav).duration


def format_report(results: list[dict], duration: float) -> str:
    lines = [
        f"Audio: {util.fmt_clock(duration)} ({duration:.1f}s)",
        "",
        f"{'model':<10} {'carregou':<10} {'load':>7} {'transcricao':>12} {'RTF':>7} "
        f"{'palavras':>9} {'pico MB':>9}",
        f"{'-' * 10} {'-' * 10} {'-' * 7} {'-' * 12} {'-' * 7} {'-' * 9} {'-' * 9}",
    ]
    for item in results:
        if not item.get("ok"):
            lines.append(f"{item['model']:<10} {'FALHOU':<10} {item.get('error', '')[:70]}")
            continue
        peak = f"{item['peak_mb']:.0f}" if item.get("peak_mb", -1) >= 0 else "n/d"
        resolved = item["resolved"] + (" *" if item.get("fell_back") else "")
        lines.append(
            f"{item['model']:<10} {resolved:<10} {item['load_seconds']:>6.1f}s "
            f"{item['seconds']:>11.1f}s {item['rtf']:>7.2f} {item['words']:>9} "
            f"{peak:>9}"
        )
    lines.append("")
    lines.append("RTF = segundos de audio por segundo de parede (maior e melhor).")
    lines.append("RTF < 1 significa que a maquina nao transcreve mais rapido que o video.")
    lines.append("'*' marca checkpoint que desceu na escada (nao carregou o pedido).")
    lines.append("'n/d' = leitura de memoria indisponivel nesta plataforma.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == CHILD_FLAG:
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "child needs model, wav and duration"}))
            return 2
        return bench_child(argv[1], argv[2], float(argv[3]))

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wav", help="audio file to transcribe (mono 16 kHz preferred)")
    source.add_argument("--url", help="download the audio of this URL first")
    parser.add_argument("--models", default=DEFAULT_MODELS, help="comma separated checkpoints")
    parser.add_argument("--repeat", type=int, default=1, help="runs per checkpoint")
    parser.add_argument("--keep-wav", action="store_true", help="keep the downloaded audio")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    logger = Logger(quiet=args.quiet)
    models = [name.strip() for name in args.models.split(",") if name.strip()]
    if not models:
        logger.warn("Nenhum modelo informado.")
        return 2

    total, free = available_ram_gb()
    if total:
        logger.info(f"RAM: {free:.1f} GB livres de {total:.1f} GB")

    work = Path(tempfile.mkdtemp(prefix="vc-bench-"))
    try:
        if args.wav:
            wav = Path(args.wav).resolve()
            if not wav.exists():
                logger.warn(f"Arquivo nao encontrado: {wav}")
                return 2
            duration = audio_mod.analyze_audio(wav).duration
        else:
            wav, duration = prepare_wav(args.url, work, logger)

        results: list[dict] = []
        for model in models:
            for run in range(max(1, args.repeat)):
                result = bench_one(model, wav, duration)
                if run:
                    result["model"] = f"{model} (2a)"
                results.append(result)
                if not result.get("ok"):
                    logger.warn(f"{model}: {result.get('error', 'falhou')}")

        print()
        print(format_report(results, duration))
    finally:
        if not args.keep_wav:
            shutil.rmtree(work, ignore_errors=True)
        else:
            logger.info(f"WAV mantido em {work}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
