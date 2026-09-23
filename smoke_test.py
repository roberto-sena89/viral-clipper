"""Local end-to-end smoke test for viral-clipper.

The test needs no network and no whisper model: it builds its own fixture with
ffmpeg, fakes the transcript and exercises every stage (audio analysis, window
selection, rendering with burned captions, jump cutting, sidecar subtitles and
reporting).

Run it from the project root:

    python smoke_test.py                 # ffmpeg decides the thread count
    python smoke_test.py --threads 1     # less memory, slower encode
    python smoke_test.py --keep          # keep the temporary work directory
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from viralclipper import audio as audio_mod
from viralclipper import cli, pipeline, render as render_mod, report, score, util
from viralclipper.config import ClipConfig
from viralclipper.transcribe import Transcript, Word
from viralclipper.util import Logger

FIXTURE_SECONDS = 45.0
BURST_SECONDS = 6.0
GAP_SECONDS = 1.5


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--keep", action="store_true")
    return parser.parse_args()


def main() -> int:
    options = build_args()
    log = Logger()
    tmp = Path(tempfile.mkdtemp(prefix="vc_smoke_"))
    ffmpeg, ffprobe = util.ffmpeg_binaries()
    source = tmp / "fixture.mp4"
    period = BURST_SECONDS + GAP_SECONDS

    # Fixture: 45 s of video; the analysis WAV carries 6 s bursts separated by
    # 1.5 s of true silence, while the mp4 only needs playable audio.
    util.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"sine=frequency=220:sample_rate=44100:duration={FIXTURE_SECONDS:g}",
            "-f", "lavfi", "-i",
            f"color=c=0x203040:s=1280x720:r=30:d={FIXTURE_SECONDS:g}",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", str(source),
        ],
        logger=log,
    )
    print("fixture:", source.stat().st_size, "bytes",
          round(util.probe_duration(ffprobe, source), 2), "s")

    rate = 16_000
    positions = np.arange(int(FIXTURE_SECONDS * rate), dtype=np.float64) / rate
    carrier = 0.25 * np.sin(2 * np.pi * 220 * positions)
    gated = np.where((positions % period) < BURST_SECONDS, carrier, 0.0)
    wav_fixture = tmp / "fixture_audio.wav"
    with wave.open(str(wav_fixture), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((gated * 32767).astype("<i2").tobytes())

    decoded = audio_mod.extract_audio(ffmpeg, source, tmp / "decoded.wav", logger=log)
    print("extract_audio:", round(util.probe_duration(ffprobe, decoded), 2), "s from mp4")

    analysis = audio_mod.analyze_audio(wav_fixture, hop_ms=20, min_silence=0.32)
    print("analysis:", round(analysis.duration, 2), "s, threshold",
          analysis.threshold_db, "dB, silences", len(analysis.silences))
    assert analysis.silences, "silence detection failed"

    # --- CLI argument mapping ---------------------------------------------
    args = cli.build_parser().parse_args(
        ["https://youtu.be/fixture", "--engine", "audio", "-n", "2",
         "--min", "10", "--max", "16", "--threads", str(options.threads)]
    )
    config = cli.config_from_args(args)
    config.validate()
    print("cli config:", config.output_dir, config.engine, config.count,
          config.min_duration, config.max_duration, "threads", config.threads)
    return run_pipeline(config, tmp, analysis, source, ffmpeg, ffprobe, log, options)


def run_pipeline(
    config: ClipConfig,
    tmp: Path,
    analysis,
    source: Path,
    ffmpeg: str,
    ffprobe: str,
    log: Logger,
    options: argparse.Namespace,
) -> int:
    config.output_dir = tmp / "out"
    config.work_dir = tmp / "work"
    config.ffmpeg, config.ffprobe = ffmpeg, ffprobe

    # --- audio-only engine (no transcript) --------------------------------
    units = score.build_units(None, analysis)
    windows = pipeline.select_windows(units, analysis, config, log)
    assert windows, "select_windows returned nothing"
    print("audio windows:",
          [(round(w.start, 1), round(w.end, 1), w.score) for w in windows])

    # --- dry run of render_windows (no download, no render) ---------------
    plan = pipeline.render_windows(
        windows, {"id": "fixture"}, analysis, None,
        ClipConfig(**{**config.__dict__, "dry_run": True}), tmp / "dry", log,
    )
    assert plan and all(record.file == "" for record in plan), plan
    print("dry run records:", [record.duration for record in plan])

    # --- transcript engine: hooks, ranking and karaoke captions -----------
    phrases = [
        "Ninguem te conta esse segredo sobre dinheiro.",
        "Eu testei e o resultado mudou tudo.",
        "Pouca gente sabe o que realmente funciona.",
        "Atencao: esse erro comum custa caro.",
    ]
    words: list[Word] = []
    cursor = 0.6
    while cursor < analysis.duration - 3.0:
        for phrase in phrases:
            for token in phrase.split():
                words.append(Word(start=round(cursor, 3), end=round(cursor + 0.34, 3), text=token))
                cursor += 0.36
            cursor += 0.5
    transcript = Transcript(
        language="pt", language_probability=0.99, model_name="fake", words=words
    )
    print("fake transcript words:", len(words))

    config.engine = "hybrid"
    units2 = score.build_units(transcript, analysis)
    windows2 = pipeline.select_windows(units2, analysis, config, log)
    assert windows2, "no transcript windows"
    print("transcript windows:",
          [(round(w.start, 1), round(w.end, 1), w.score, w.hook_terms[:2]) for w in windows2])

    window = windows2[0]
    rendered = render_mod.render_clip(
        source=source,
        destination=config.output_dir / "clip_karaoke.mp4",
        clip_start=window.start,
        clip_end=window.end,
        config=config,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        words=transcript.words_between(window.start, window.end),
        silences=analysis.silence_overlaps(window.start, window.end),
        work_dir=tmp / "render_karaoke",
        logger=log,
    )
    print("rendered:", rendered.path.name, rendered.duration, "s",
          f"{rendered.width}x{rendered.height}")
    assert rendered.duration >= config.min_duration - 0.05, rendered.duration
    assert (rendered.width, rendered.height) == (config.width, config.height)
    assert (tmp / "render_karaoke" / "captions.ass").exists(), "ASS captions missing"

    # --- jump cut must never break the minimum duration -------------------
    jump_config = ClipConfig(**{**config.__dict__, "jump_cut": True})
    jumped = render_mod.render_clip(
        source=source,
        destination=config.output_dir / "clip_jumpcut.mp4",
        clip_start=window.start,
        clip_end=window.end,
        config=jump_config,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        words=transcript.words_between(window.start, window.end),
        silences=analysis.silence_overlaps(window.start, window.end),
        work_dir=tmp / "render_jump",
        logger=log,
    )
    print("jump cut:", jumped.duration, "s (window", round(window.duration, 2), "s)")
    assert jumped.duration >= jump_config.min_duration - 0.05, jumped.duration


    # --- sidecar captions -------------------------------------------------
    side_config = ClipConfig(
        **{**config.__dict__, "burn_captions": False, "caption_style": "block"}
    )
    side_dest = config.output_dir / "clip_sidecar.mp4"
    render_mod.render_clip(
        source=source,
        destination=side_dest,
        clip_start=window.start,
        clip_end=window.end,
        config=side_config,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        words=transcript.words_between(window.start, window.end),
        silences=[],
        work_dir=tmp / "render_sidecar",
        logger=log,
    )
    assert side_dest.with_suffix(".srt").exists(), "sidecar subtitles missing"
    print("sidecar:", side_dest.with_suffix(".srt").name)

    # --- reporting --------------------------------------------------------
    record = report.ClipRecord(
        index=1, start=round(window.start, 3), end=round(window.end, 3),
        duration=rendered.duration, score=window.score, meets_minimum=True,
        file=str(rendered.path), hook_terms=window.hook_terms, text=window.text,
        components=window.components, width=rendered.width, height=rendered.height,
    )
    run_report = report.RunReport(
        url="local", title="fixture", video_id="fixture", uploader="me",
        source_duration=analysis.duration, language="pt", model="fake",
        engine=config.engine, min_duration=config.min_duration,
        max_duration=config.max_duration, clips=[record],
    )
    print(report.format_table(run_report))
    json_path = report.write_json(run_report, config.output_dir / "clips.json")
    markdown_path = report.write_markdown(run_report, config.output_dir / "clips.md")
    json.loads(json_path.read_text(encoding="utf-8"))
    assert markdown_path.read_text(encoding="utf-8").startswith("# Clips:")
    print("reports:", json_path.name, markdown_path.name,
          json_path.stat().st_size, markdown_path.stat().st_size)

    print("output files:", sorted(path.name for path in config.output_dir.iterdir()))
    if options.keep:
        print("work dir kept:", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

