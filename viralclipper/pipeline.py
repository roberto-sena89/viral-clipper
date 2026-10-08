"""End to end orchestration: analyse the source, pick windows, render clips."""

from __future__ import annotations

import gc
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import audio as audio_mod
from . import download, ranker, render, report, score, transcribe, transcript_cache, util
from . import template as template_mod
from . import transcript_import, viral_report
from . import config as config_mod
from .config import ClipConfig
from .render_task import _RenderTask, _record, _render_task
from .util import ClipperError, Logger


def analyse(
    config: ClipConfig,
    work: Path,
    logger: Logger,
) -> tuple[dict, audio_mod.AudioAnalysis, transcribe.Transcript | None, list[score.Unit]]:
    """Download the audio pass and build the speech units used for scoring."""
    logger.step("Reading video metadata")
    metadata = download.fetch_metadata(config.url, config, logger)
    try:
        source_duration = float(metadata.get("duration") or 0.0)
    except (TypeError, ValueError):
        source_duration = 0.0
    source_id = str(metadata.get("id") or metadata.get("webpage_url") or config.url)
    logger.ok(
        f"{metadata.get('title', '(sem titulo)')} - "
        f"{util.fmt_clock(source_duration) if source_duration else 'duracao desconhecida'}"
    )

    if source_duration and source_duration < config.min_duration:
        raise ClipperError(
            f"The source video is {source_duration:.1f}s long, shorter than the "
            f"minimum clip duration of {config.min_duration:.1f}s."
        )

    logger.step("Downloading the audio track for analysis")
    audio_source = download.download_audio(config.url, work / "source_audio", config, logger)
    wav_path = audio_mod.extract_audio(
        config.ffmpeg, audio_source, work / "analysis.wav", logger=logger
    )

    analysis = audio_mod.analyze_audio(
        wav_path,
        hop_ms=config.hop_ms,
        min_silence=config.min_silence,
        silence_db=config.silence_db,
    )
    logger.ok(
        f"Audio analyzed: {analysis.duration:.1f}s, floor {analysis.noise_floor_db:.1f} dB, "
        f"speech {analysis.speech_level_db:.1f} dB, {len(analysis.silences)} silences"
    )

    transcript: transcribe.Transcript | None = None
    if config.transcript_text or config.transcript_file:
        transcript = _supplied_transcript(config, logger)
    elif config.engine == "transcript":
        # Transcription is the whole point of this engine, so a failure is fatal.
        transcript = _transcribe_cached(wav_path, source_id, config, work, logger)
    elif config.engine == "hybrid":
        # Hybrid means "speech when available, audio energy otherwise". A
        # checkpoint that will not fit in RAM must degrade the run, not abort
        # it: audio-only scoring still produces usable clips.
        transcript = _transcribe_hybrid(wav_path, source_id, config, work, logger)
    else:
        logger.info("Engine 'audio': skipping transcription")

    _warn_unreachable_min_score(config, transcript, logger)
    units = score.build_units(transcript, analysis)
    if not units:
        raise ClipperError("No speech units were found in the source audio.")
    logger.ok(f"{len(units)} speech units built")
    return metadata, analysis, transcript, units


def _transcribe_hybrid(
    wav_path: Path,
    source_id: str,
    config: ClipConfig,
    work: Path,
    logger: Logger,
) -> transcribe.Transcript | None:
    """Transcribe for the hybrid engine, degrading instead of aborting.

    Retries ONCE, and only on an allocation failure. Medido nesta máquina: o
    mesmo vídeo, com os mesmos parâmetros, transcreveu bem sozinho (~16 min) e
    falhou com ~0,9 GB livres — a falha é do estado da máquina naquele
    instante, não da entrada. Uma segunda tentativa custa um carregamento de
    modelo; não tentar custa a legenda de todos os cortes.

    Só a falha de memória é repetida. Um wav corrompido ou um checkpoint
    ausente falham igual na segunda vez, e pagar um carregamento para descobrir
    isso é desperdício.
    """
    try:
        return _transcribe_cached(wav_path, source_id, config, work, logger)
    except transcribe.TranscriptionOutOfMemory as exc:
        logger.warn(f"Transcription ran out of memory; retrying once ({exc})")
        gc.collect()
    except ClipperError as exc:
        _warn_degraded_transcript(exc, logger)
        return None

    try:
        return _transcribe_cached(wav_path, source_id, config, work, logger)
    except ClipperError as exc:
        _warn_degraded_transcript(exc, logger)
        return None


def _warn_degraded_transcript(exc: BaseException, logger: Logger) -> None:
    """Dizer o que a degradação CUSTA, não só que aconteceu.

    A linha antiga — "scoring on audio alone" — descrevia o mecanismo e
    escondia a consequência. Medido, mesmo vídeo e parâmetros: com transcrição
    os cortes marcam 68,3/68,1 e saem com legenda queimada; sem ela, 39,3/39,3
    e sem legenda nenhuma. O run terminava com exit 0, indistinguível de um
    sucesso, e é por isso que o `cli.py` agora devolve 4.
    """
    logger.warn(
        f"Transcription unavailable ({exc}). The clips will have NO burned "
        "captions, and the selection falls back to audio energy alone — which "
        "scores far lower and picks different moments."
    )


def _supplied_transcript(config: ClipConfig, logger: Logger) -> transcribe.Transcript:
    """Build the transcript from text the user provided, skipping whisper.

    This is the whole point of the manual-transcript path: transcription is the
    slowest stage, and a transcript that already exists should never pay for a
    model load.
    """
    language = config.language or "manual"
    if config.transcript_text:
        logger.step("Using the supplied transcript (whisper skipped)")
        transcript, cues = transcript_import.transcript_from_text(
            config.transcript_text, language=language
        )
    else:
        logger.step(f"Using transcript file {config.transcript_file} (whisper skipped)")
        transcript, cues = transcript_import.transcript_from_file(
            config.transcript_file, language=language
        )
    logger.ok(f"Imported {len(cues)} cues, {len(transcript.words)} words from the transcript")
    return transcript


def _warn_unreachable_min_score(
    config: ClipConfig,
    transcript: transcribe.Transcript | None,
    logger: Logger,
) -> None:
    """Warn early when the quality gate cannot be reached without a transcript.

    Hook and question signals are all zero without text, capping every window
    at :func:`score.max_score_without_transcript`. Without this warning the
    user pays for the whole selection stage just to learn something that was
    knowable the moment transcription was lost (or skipped with ``--engine
    audio``).
    """
    if transcript is not None or config.min_score <= 0.0:
        return
    ceiling = score.max_score_without_transcript()
    if config.min_score > ceiling:
        logger.warn(
            f"--min-score {config.min_score:.0f} is unreachable without a transcript: "
            f"audio-only scoring tops out at {ceiling:.1f}. Lower --min-score to "
            f"{ceiling:.0f} or below, or fix transcription (free memory, smaller "
            f"model) and re-run."
        )
    else:
        # Reachable in theory, but the audio-only signals rarely all peak at
        # once, so a gate just under the ceiling fails in practice. Say so now,
        # not at the selection error.
        logger.info(
            f"Audio-only scoring tops out at {ceiling:.1f}; check that "
            f"--min-score {config.min_score:.0f} leaves enough headroom below "
            f"that ceiling."
        )


def _transcribe_cached(
    wav_path: Path,
    source_id: str,
    config: ClipConfig,
    work: Path,
    logger: Logger,
) -> transcribe.Transcript:
    """Transcribe ``wav_path``, reusing a cached result when one matches.

    Two lookups are deliberate. The first one keys on the configured checkpoint
    and needs no model load at all, so the common repeat run stays cheap. Only a
    miss pays for loading, after which the key is rebuilt from the checkpoint
    that actually loaded - a fallback to a smaller model must not be served to,
    or hidden from, a run that asked for the full size one.
    """
    info = transcript_cache.resolve_cache(config, logger)
    if not info.enabled:
        return transcribe.transcribe(wav_path, config, logger)

    key = transcript_cache.cache_key(source_id, wav_path, config)
    cached = transcript_cache.load(info.directory, key, logger)
    if cached is not None:
        return cached

    loaded = transcribe.load_model(config, logger)
    if loaded.name != config.whisper_model:
        key = transcript_cache.cache_key(source_id, wav_path, config, model=loaded.name)
        cached = transcript_cache.load(info.directory, key, logger)
        if cached is not None:
            return cached

    logger.step(f"Transcribing {wav_path.name} (cache miss, key {key[:12]}...)")
    transcript = transcribe.transcribe(wav_path, config, logger, model=loaded)
    transcript_cache.save(
        info.directory, key, transcript,
        source_id=source_id, wav_path=wav_path, config=config, logger=logger,
    )
    return transcript


def select_windows(
    units: list[score.Unit],
    analysis: audio_mod.AudioAnalysis,
    config: ClipConfig,
    logger: Logger,
) -> list[score.Window]:
    """Build every valid window and keep the best non-overlapping ones."""
    candidates = score.build_candidates(units, config)
    if not candidates:
        raise ClipperError(
            "No candidate window fits inside the configured duration limits. "
            "Try lowering --min or raising --max."
        )
    logger.info(f"{len(candidates)} candidate windows evaluated")
    if config.count == 0 and config.ranker not in {"none", "", None}:
        # No modo automatico o teto real e ``ranker_top_n``, nao
        # ``auto_ceiling``: as janelas que o curador nao julgou sao
        # empurradas para score -1 e o piso relativo as descarta. Sem este
        # aviso, o usuario pediria um video inteiro de cortes e receberia
        # silenciosamente 24.
        if config.ranker_top_n < config.auto_ceiling:
            logger.warn(
                f"Modo automatico com curador: o curador julga no maximo "
                f"--ranker-top-n {config.ranker_top_n} janelas, entao a saida "
                f"nao passa disso mesmo que o video renda mais. Suba "
                f"--ranker-top-n para {config.auto_ceiling} para usar o teto inteiro."
            )
    score.score_windows(candidates, units, analysis, config)
    # Optional precision pass. A no-op unless --ranker llm is set, and it never
    # raises: a dead ranker leaves the heuristic scores in place.
    ranker.apply(candidates, config, logger)
    chosen = score.pick_windows(candidates, config)
    if 0 < len(chosen) < config.count:
        logger.info(
            f"{len(chosen)} of the {config.count} requested clips: the others "
            f"overlap each other or fall inside --min-gap {config.min_gap:g}s. "
            f"Lower --min-gap or --min to fit more."
        )
    if not chosen:
        best = max(window.score for window in candidates)
        if config.min_score > 0.0 and best < config.min_score:
            raise ClipperError(
                f"No window reached --min-score {config.min_score:.0f} "
                f"(best candidate scored {best:.1f}). Lower --min-score or "
                f"raise --count to see weaker windows."
            )
        raise ClipperError("Scoring produced no usable window.")
    if config.min_score > 0.0:
        logger.ok(
            f"{len(chosen)} of {len(candidates)} candidates cleared the "
            f"--min-score {config.min_score:.0f} gate"
        )
    return chosen


def build_viral_report(
    windows: list[score.Window],
    units: list[score.Unit],
    metadata: dict,
    config: ClipConfig,
    logger: Logger,
) -> list[viral_report.ViralAnalysis]:
    """Analyse the selected clips and write ``viral_report.md``.

    Runs after selection and before rendering, so the editorial read is
    available even for a plan-only run.
    """
    if not config.viral_report:
        return []
    analyses = viral_report.analyse_windows(windows, units)
    destination = util.ensure_dir(config.output_dir) / "viral_report.md"
    viral_report.write_markdown(
        analyses, destination, title=str(metadata.get("title") or "")
    )
    logger.ok(f"Relatorio de viralizacao: {destination}")
    for analysis in analyses:
        logger.info(
            f"  #{analysis.index} {analysis.headline} | potencial "
            f"{analysis.viral_potential}% | retencao {analysis.retention} | "
            f"comentarios {analysis.comments} | compartilhamentos {analysis.shares} | "
            f"polemica {analysis.controversy}"
        )
    return analyses


def _build_tasks(
    windows: list[score.Window],
    analysis: audio_mod.AudioAnalysis,
    transcript: transcribe.Transcript | None,
    config: ClipConfig,
    metadata: dict,
    work: Path,
    output_dir: Path,
    full_source: Path | None,
    logger: Logger,
) -> list[_RenderTask]:
    """One task per (window, template variant) pair.

    Without a template, or with one that only fills the canvas, this is exactly
    the original one-task-per-window list. With variants it multiplies: nothing
    is re-downloaded and nothing is re-analysed, only re-encoded.
    """
    variants: list[tuple[object | None, str]] = [(None, "")]
    if config.template:
        base = config_mod.resolve_template(config.template)
        combinations = template_mod.expand_variations(
            base,
            caption_presets=config.variant_presets,
            layouts=config.variant_layouts,
        )
        if len(combinations) == 1:
            # A single variant is just "the template", not a matrix of one:
            # keep the plain task shape and the unadorned filename.
            variants = [(combinations[0], "")]
        else:
            variants = [
                (variant, _variant_suffix(variant)) for variant in combinations
            ]
            axes = []
            if config.variant_presets:
                axes.append(f"{len(config.variant_presets)} presets")
            if config.variant_layouts:
                axes.append(f"{len(config.variant_layouts)} layouts")
            logger.step(
                f"Variacoes: {' x '.join(axes)} = {len(variants)} renders por corte"
            )

    tasks: list[_RenderTask] = []
    for position, window in enumerate(windows, start=1):
        finish = min(window.end + config.pad_end, analysis.duration or window.end)
        words = (
            transcript.words_between(window.start, finish) if transcript else []
        )
        silences = analysis.silence_overlaps(window.start, finish)
        for variant_template, suffix in variants:
            # Each variant gets its own ClipConfig so a template override
            # (preset, layout) cannot leak into the next variant's render.
            variant_config = (
                template_mod.apply_to_config(config, variant_template)
                if variant_template is not None
                else config
            )
            # A variant's work directory is per-variant: two renders of the
            # same window would otherwise race on the same captions.ass.
            clip_dir = work / f"clip_{position:02d}{suffix}"
            tasks.append(
                _RenderTask(
                    position=position,
                    window=window,
                    finish=finish,
                    words=words,
                    silences=silences,
                    media=full_source,
                    metadata=metadata,
                    config=variant_config,
                    clip_dir=clip_dir,
                    output_dir=output_dir,
                    template=variant_template,
                    variant=suffix,
                )
            )
    return tasks


def _variant_suffix(template) -> str:
    """Filename suffix for a variant: ``__neon-focus``.

    Derived from the template name, which already encodes the axes, so the two
    can never drift apart.
    """
    _, _, tail = template.name.partition("__")
    return f"__{tail}" if tail else ""


def render_windows(
    windows: list[score.Window],
    metadata: dict,
    analysis: audio_mod.AudioAnalysis,
    transcript: transcribe.Transcript | None,
    config: ClipConfig,
    work: Path,
    logger: Logger,
) -> list[report.ClipRecord]:
    """Download each section, render it and build the clip records."""
    output_dir = util.ensure_dir(config.output_dir)
    records: list[report.ClipRecord] = []
    full_source: Path | None = None

    if config.download_mode == "full":
        logger.step("Downloading the full video once")
        full_source = download.download_full(config.url, work / "source_video", config, logger)

    if config.dry_run:
        for position, window in enumerate(windows, start=1):
            finish = min(window.end + config.pad_end, analysis.duration or window.end)
            logger.step(
                f"Clip {position}/{len(windows)}: {util.fmt_clock(window.start)} - "
                f"{util.fmt_clock(finish)} (score {window.score:.1f})"
            )
            records.append(_record(position, window, finish, None, config))
        return records

    tasks = _build_tasks(
        windows,
        analysis,
        transcript,
        config,
        metadata,
        work,
        output_dir,
        full_source,
        logger,
    )

    # In "sections" mode every clip needs its own download. Doing that inside
    # the worker pool means N simultaneous yt-dlp requests hammering the same
    # host, so download them sequentially up front and hand each task its
    # already-downloaded media. In "full" mode the single download happens above
    # and every task already points at that same file, so no worker downloads.
    if not config.dry_run and config.download_mode == "sections" and full_source is None:
        # Variants of the same window share one download: downloading the same
        # section once per variant would triple the network cost of an axis that
        # only exists to re-encode.
        downloaded: dict[int, tuple[Path, float]] = {}
        logger.step("Baixando as secoes dos clips")
        for task in tasks:
            key = task.position
            if key not in downloaded:
                try:
                    media = download.download_section(
                        config.url,
                        task.window.start,
                        task.finish,
                        task.clip_dir / f"section_{task.position:02d}",
                        config,
                        logger,
                    )
                except ClipperError as exc:
                    if "yt-dlp failed to cut a section with ffmpeg" not in str(exc).lower():
                        raise
                    logger.warn(
                        "O FFmpeg falhou ao baixar um trecho; baixando o vídeo "
                        "completo uma vez e cortando os clips localmente."
                    )
                    full_source = download.download_full(
                        config.url, work / "source_video", config, logger
                    )
                    for fallback_task in tasks:
                        fallback_task.media = full_source
                        fallback_task.media_origin = 0.0
                    break
                origin = download.resolve_origin(
                    config.ffprobe,
                    media,
                    max(0.0, task.window.start - download.SECTION_PADDING),
                    logger,
                )
                downloaded[key] = (media, origin)
            media, origin = downloaded[key]
            # The renderer seeks into this file, so it has to know whether the
            # section kept the source timestamps or was reset to zero.
            task.media = media
            task.media_origin = origin

    if config.parallel and len(tasks) > 1:
        workers = config.workers or min(os.cpu_count() or 1, len(tasks))
        # Never spin up more workers than there is work for.
        workers = max(1, min(workers, len(tasks)))
        logger.info(f"Renderizando {len(tasks)} clips em {workers} workers")
        # Must happen before the pool exists: the workers inherit the environment,
        # and an uncapped BLAS pool per worker is what turns a render into a
        # BrokenProcessPool on a machine where memory is the scarce resource.
        util.limit_native_threads()
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_render_task, task): task for task in tasks}
                for future in as_completed(futures):
                    # The worker ran on its own copy of the task, so the local
                    # object is untouched: the result has to be taken back from
                    # the future. Reading task.record here instead would leave
                    # every record None and break the report.
                    result = future.result()
                    original = futures[future]
                    original.record = result.record
                    original.rendered = result.rendered
                    original.error = result.error
        except Exception as exc:
            logger.warn(f"Render paralelo falhou ({exc!r}); retribuindo um por vez")
            # ``_render_task`` assigns ``record``/``rendered`` unconditionally,
            # so this retry is the last writer for every task: whatever the
            # broken pool had already copied back cannot survive it. A clip the
            # retry renders is therefore never reported with an empty ``file``.
            for task in tasks:
                _render_task(task)
    else:
        for task in tasks:
            _render_task(task)

    for task in tasks:
        if task.record is None:  # pragma: no cover - defensive
            # A reporting detail must never throw away clips that were rendered.
            task.record = _record(
                task.position, task.window, task.finish, task.rendered, task.config
            )
        records.append(task.record)
        if task.rendered is not None:
            logger.ok(f"{task.rendered.path.name} ({task.rendered.duration:.1f}s, "
                      f"{task.rendered.width}x{task.rendered.height})")
    return records