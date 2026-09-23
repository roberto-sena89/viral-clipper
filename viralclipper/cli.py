"""Command line interface: ``python -m viralclipper <url>``."""

from __future__ import annotations

import argparse
import dataclasses
import shutil
import sys
from dataclasses import fields
from pathlib import Path

from . import batch
from . import caption_presets
from . import config as config_mod
from . import config_file, pipeline, reframe, report, score, util
from .util import ClipperError, Logger

DESCRIPTION = (
    "Create vertical 30s+ viral clips from a YouTube video. The tool analyses "
    "speech and audio energy, ranks the strongest windows and renders ready to "
    "post shorts with burned captions."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viralclipper",
        description=DESCRIPTION,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("url", nargs="?", default=None, help="URL do video do YouTube")
    parser.add_argument("-o", "--output", default="output", help="Pasta de saida")
    parser.add_argument("--work-dir", default=None, help="Pasta de trabalho temporaria")
    parser.add_argument("-n", "--count", type=int, default=5, help="Quantidade de clips")
    parser.add_argument(
        "--batch",
        default=None,
        help="Arquivo com uma URL por linha; processa o lote com manifesto retomavel",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Banco SQLite do lote (padrao: <output>/batch.sqlite3)",
    )
    parser.add_argument(
        "--retry-failed",
        dest="retry_failed",
        action="store_true",
        help="No modo lote, recolocar na fila as URLs que falharam",
    )
    parser.add_argument(
        "--config",
        dest="config_file",
        default=None,
        help="Arquivo de configuracao YAML/TOML (lido antes dos flags CLI; flags vencem)",
    )

    selection = parser.add_argument_group("selecao")
    selection.add_argument("--min", dest="min_duration", type=float, default=30.0)
    selection.add_argument("--max", dest="max_duration", type=float, default=60.0)
    selection.add_argument("--target", dest="target_duration", type=float, default=42.0)
    selection.add_argument("--min-gap", dest="min_gap", type=float, default=6.0)
    selection.add_argument(
        "--min-score",
        dest="min_score",
        type=float,
        default=0.0,
        help="Portao de qualidade absoluto 0-100 (0 desliga). Clips abaixo disso sao descartados",
    )
    selection.add_argument("--pad-start", dest="pad_start", type=float, default=0.25)
    selection.add_argument("--pad-end", dest="pad_end", type=float, default=0.35)
    selection.add_argument(
        "--engine",
        choices=["hybrid", "audio", "transcript"],
        default="hybrid",
        help="hybrid usa transcricao + energia; audio pula a transcricao",
    )

    whisper = parser.add_argument_group("transcricao")
    whisper.add_argument("--model", dest="whisper_model", default="small")
    whisper.add_argument("--device", dest="whisper_device", default="auto")
    whisper.add_argument("--compute-type", dest="whisper_compute_type", default="int8")
    whisper.add_argument("--language", default=None, help="Ex.: pt para forcar portugues")
    whisper.add_argument("--beam-size", dest="beam_size", type=int, default=1)
    whisper.add_argument("--no-vad", dest="vad_filter", action="store_false")
    whisper.set_defaults(vad_filter=True)
    whisper.add_argument(
        "--cache-dir",
        dest="cache_dir",
        default=None,
        help="Pasta do cache de transcricao (padrao: output/cache/transcripts, "
             "fora do diretorio temporario do trabalho para ser reaproveitado "
             "entre execucoes do mesmo video)",
    )
    whisper.add_argument(
        "--no-transcript-cache",
        dest="transcript_cache",
        action="store_false",
        help="Desliga o cache de transcricao e sempre transcreve de novo",
    )
    whisper.set_defaults(transcript_cache=True)
    whisper.add_argument(
        "--transcript",
        dest="transcript_file",
        default=None,
        help="Arquivo com transcricao pronta (SRT, VTT ou painel do YouTube colado): "
             "pula o whisper e usa este texto",
    )
    whisper.add_argument(
        "--transcript-text",
        dest="transcript_text",
        default=None,
        help="Transcricao passada direto na linha (mesma finalidade de --transcript)",
    )

    audio_group = parser.add_argument_group("audio")
    audio_group.add_argument("--silence-db", dest="silence_db", type=float, default=None)
    audio_group.add_argument("--min-silence", dest="min_silence", type=float, default=0.32)
    audio_group.add_argument("--hop-ms", dest="hop_ms", type=int, default=20)

    source = parser.add_argument_group("download")
    source.add_argument(
        "--download-mode", choices=["sections", "full"], default="sections"
    )
    source.add_argument("--max-height", dest="max_height", type=int, default=1080)
    source.add_argument("--cookies-from-browser", dest="cookies_from_browser", default=None)
    source.add_argument(
        "--ytdlp-arg",
        dest="extra_ytdlp_args",
        action="append",
        default=[],
        help="Argumento extra repassado ao yt-dlp (pode repetir)",
    )

    video = parser.add_argument_group("renderizacao")
    video.add_argument("--no-vertical", dest="vertical", action="store_false")
    video.set_defaults(vertical=True)
    video.add_argument(
        "--layout",
        choices=["focus", "center", "blur", "fit"],
        default="focus",
        help="focus usa deteccao de rosto (cai para center sem detector instalado)",
    )
    video.add_argument("--width", type=int, default=1080)
    video.add_argument("--height", type=int, default=1920)
    video.add_argument("--caption-style", choices=["karaoke", "block", "none"], default="karaoke")
    video.add_argument("--no-captions", dest="caption_style", action="store_const", const="none")
    video.add_argument("--sidecar-captions", dest="burn_captions", action="store_false")
    video.set_defaults(burn_captions=True)
    video.add_argument("--words-per-line", dest="caption_words_per_line", type=int, default=None,
                       help="Palavras por linha de legenda (padrao: herda do preset)")
    video.add_argument(
        "--caption-preset",
        dest="caption_preset",
        choices=sorted(caption_presets.PRESETS),
        default="karaoke",
        help="Modelo de legenda pronto (fonte, cor, caixa e destaque). Use junto com --font-size, --caption-margin, --highlight-color etc. para sobrescrever so o que quiser.",
    )
    video.add_argument("--font", default=None, help="Fonte das legendas (padrao: herda do preset)")
    video.add_argument("--font-size", dest="font_size", type=int, default=None,
                       help="Tamanho da fonte (padrao: herda do preset)")
    video.add_argument(
        "--caption-margin",
        dest="caption_margin_v",
        type=int,
        default=None,
        help="Margem inferior da legenda em px (padrao: herda do preset; 640 mantem o texto fora da zona de botoes do TikTok)",
    )
    video.add_argument(
        "--highlight-color",
        dest="highlight_color",
        default=None,
        help="Cor de destaque do karaokê e do titulo, formato ASS &HAABBGGRR (padrao: herda do preset)",
    )
    video.add_argument(
        "--headline",
        dest="headline_text",
        default=None,
        help="Titulo fixo queimado no topo nos primeiros segundos (padrao: auto a partir da abertura do clip)",
    )
    video.add_argument(
        "--headline-seconds",
        dest="headline_seconds",
        type=float,
        default=0.0,
        help="Duracao do titulo do gancho no topo, em segundos (padrao 0 = desligado)",
    )
    video.add_argument(
        "--no-headline",
        dest="headline_seconds",
        action="store_const",
        const=0.0,
        help="Nao queimar o titulo do gancho no topo; gera somente as legendas",
    )
    video.add_argument("--headline-font-size", dest="headline_font_size", type=int, default=None)
    video.add_argument(
        "--progress-bar",
        dest="progress_bar",
        action="store_true",
        help="Desenha a barra de progresso no topo do frame (padrao: nao desenha)",
    )
    video.add_argument(
        "--no-progress-bar",
        dest="progress_bar",
        action="store_false",
        help="Nao desenhar a barra de progresso no topo (padrao)",
    )
    video.set_defaults(progress_bar=False)
    video.add_argument("--progress-bar-height", dest="progress_bar_height", type=int, default=10)
    video.add_argument(
        "--progress-bar-color",
        dest="progress_bar_color",
        default="yellow",
        help="Cor da barra de progresso (nome ou hex do ffmpeg)",
    )
    video.add_argument("--uppercase", dest="uppercase_captions", action="store_true",
                       help="Legendas em MAIUSCULAS (padrao: herda do preset)")
    video.add_argument("--no-uppercase", dest="uppercase_captions", action="store_false",
                       help="Legendas em caixa de frase (padrao: herda do preset)")
    video.set_defaults(uppercase_captions=None)
    video.add_argument("--crf", type=int, default=20)
    video.add_argument("--preset", default="veryfast")
    video.add_argument(
        "--audio-bitrate",
        dest="audio_bitrate",
        default="192k",
        help="Bitrate do AAC na saida (ex.: 128k, 192k, 256k)",
    )
    video.add_argument("--jump-cut", dest="jump_cut", action="store_true", help="Remove silencios")
    video.add_argument("--no-loudnorm", dest="loudnorm", action="store_false")
    video.set_defaults(loudnorm=True)
    video.add_argument("--lufs", dest="target_lufs", type=float, default=-14.0)
    video.add_argument(
        "--threads",
        type=int,
        default=2,
        help="Limite de threads do ffmpeg (reduza se faltar memoria; libx264 aloca por thread)",
    )
    video.add_argument("--ffmpeg", default="ffmpeg")
    video.add_argument("--ffprobe", default="ffprobe")

    templates = parser.add_argument_group("templates")
    templates.add_argument(
        "--template",
        default=None,
        help=(
            "Composicao nomeada (split-card) ou caminho de um .toml/.yaml. "
            "Sem isso o video ocupa o quadro inteiro."
        ),
    )
    templates.add_argument(
        "--variant-presets",
        dest="variant_presets",
        default=None,
        help="Presets de legenda separados por virgula: rende o mesmo corte uma vez por preset",
    )
    templates.add_argument(
        "--variant-layouts",
        dest="variant_layouts",
        default=None,
        help="Layouts separados por virgula: rende o mesmo corte uma vez por layout",
    )
    templates.add_argument(
        "--list-templates",
        dest="list_templates",
        action="store_true",
        help="Mostra os templates embutidos e sai",
    )
    templates.add_argument(
        "--describe-template",
        dest="describe_template",
        default=None,
        help="Mostra a composicao resolvida (faixas em pixels) e sai",
    )

    execution = parser.add_argument_group("execucao")
    execution.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Quantos clips renderizar em paralelo (cada worker roda um encode x264 proprio)",
    )
    execution.add_argument("--no-parallel", dest="parallel", action="store_false", help="Renderiza clips um de cada vez")
    execution.set_defaults(parallel=True)

    semantic = parser.add_argument_group("ranker semantico (opcional)")
    semantic.add_argument(
        "--ranker",
        choices=["none", "llm"],
        default="none",
        help="llm reavalia os melhores candidatos com um modelo de linguagem",
    )
    semantic.add_argument("--ranker-model", dest="ranker_model", default="gpt-4o-mini")
    semantic.add_argument(
        "--ranker-base-url",
        dest="ranker_base_url",
        default="https://api.openai.com/v1",
        help="Endpoint compativel com /chat/completions (OpenAI, DeepSeek, Groq, Ollama...)",
    )
    semantic.add_argument(
        "--ranker-api-key-env",
        dest="ranker_api_key_env",
        default="OPENAI_API_KEY",
        help="Nome da variavel de ambiente que guarda a chave",
    )
    semantic.add_argument(
        "--ranker-no-key",
        dest="ranker_requires_key",
        action="store_false",
        help="Nao exigir chave (para endpoints locais)",
    )
    semantic.set_defaults(ranker_requires_key=True)
    semantic.add_argument(
        "--ranker-top-n",
        dest="ranker_top_n",
        type=int,
        default=24,
        help="Quantos candidatos enviar ao modelo (o custo cresce com este numero)",
    )
    semantic.add_argument(
        "--ranker-weight",
        dest="ranker_weight",
        type=float,
        default=0.6,
        help="Peso do modelo no score final (0 = so heuristica, 1 = so modelo)",
    )
    semantic.add_argument("--ranker-timeout", dest="ranker_timeout", type=float, default=60.0)

    misc = parser.add_argument_group("diversos")
    misc.add_argument("--plan-only", dest="dry_run", action="store_true")
    misc.add_argument(
        "--no-viral-report",
        dest="viral_report",
        action="store_false",
        help="Nao gerar o relatorio de viralizacao (viral_report.md)",
    )
    misc.set_defaults(viral_report=True)
    misc.add_argument("--keep-temp", dest="keep_temp", action="store_true")
    misc.add_argument("-q", "--quiet", action="store_true")
    misc.add_argument("-v", "--verbose", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> config_mod.ClipConfig:
    """Map the parsed arguments onto :class:`ClipConfig`.

    Only names that the dataclass actually declares are forwarded, so adding a
    new CLI flag can never break configuration building.
    """
    known = {field.name for field in fields(config_mod.ClipConfig)}
    payload = {
        key: value
        for key, value in vars(args).items()
        if key in known and key not in {"output_dir", "work_dir"}
    }
    payload["output_dir"] = Path(args.output)
    payload["work_dir"] = Path(args.work_dir) if args.work_dir else None
    payload["cache_dir"] = Path(args.cache_dir) if args.cache_dir else None
    payload["transcript_file"] = (
        Path(args.transcript_file) if args.transcript_file else None
    )
    # The positional URL is optional now that --batch exists; the batch runner
    # replaces it per job.
    payload["url"] = args.url or ""
    # The variant axes arrive as one comma-separated string on the CLI because
    # repeating a flag per preset reads worse; the dataclass wants a list.
    payload["variant_presets"] = _split_axis(getattr(args, "variant_presets", None))
    payload["variant_layouts"] = _split_axis(getattr(args, "variant_layouts", None))
    return config_mod.ClipConfig(**payload)


def _split_axis(raw: str | None) -> list[str]:
    """Turn ``"neon, fire"`` into ``["neon", "fire"]``, dropping blanks."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _list_templates() -> int:
    """Print the built-in templates and their zones."""
    from . import template as template_mod

    for name in sorted(template_mod.BUILTIN):
        template = template_mod.BUILTIN[name]
        zones = " + ".join(
            f"{zone.kind} {zone.fraction:.0%}" for zone in template.zones
        )
        print(f"{name:<12} {zones}")
        if template.description:
            print(f"{'':<12} {template.description}")
    return 0


def _describe_template(reference: str, width: int, height: int, logger) -> int:
    """Print the pixel geometry a template resolves to at a given canvas."""
    from . import template as template_mod

    try:
        template = config_mod.resolve_template(reference)
    except ClipperError as exc:
        logger.warn(str(exc))
        return 2
    print(template_mod.describe(template, width, height))
    return 0


def run_single(
    config: config_mod.ClipConfig, logger: Logger
) -> tuple[int, report.RunReport | None, str]:
    """Analyse, select, render and report exactly one URL.

    Returns ``(exit_code, run_report, error_message)``. ``KeyboardInterrupt`` is
    deliberately *not* caught here: the single-URL path turns it into exit code
    130, while the batch path lets it abort the whole run.
    """
    work = util.ensure_dir(config.work_path())
    run_report: report.RunReport | None = None
    json_path: Path | None = None
    markdown_path: Path | None = None
    try:
        metadata, analysis, transcript, units = pipeline.analyse(config, work, logger)
        windows = pipeline.select_windows(units, analysis, config, logger)
        _print_plan(windows, logger)
        pipeline.build_viral_report(windows, units, metadata, config, logger)

        records = pipeline.render_windows(
            windows, metadata, analysis, transcript, config, work, logger
        )
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
        output_dir = util.ensure_dir(config.output_dir)
        json_path = report.write_json(run_report, output_dir / "clips.json")
        markdown_path = report.write_markdown(run_report, output_dir / "clips.md")
    except ClipperError as exc:
        logger.warn(str(exc))
        return 1, None, str(exc)
    finally:
        if not config.keep_temp:
            shutil.rmtree(work, ignore_errors=True)

    if run_report is not None and not config.quiet:
        print()
        print(report.format_table(run_report))
        print()
        logger.ok(f"Relatorio: {json_path}")
        logger.ok(f"Resumo: {markdown_path}")
        if config.dry_run:
            logger.info("Plan-only: nenhum clip foi renderizado.")

    # Two things this must not do. It must not fire for a plan-only run, where
    # nothing was rendered so ``meets_minimum`` is trivially false, and it must
    # not sit inside the ``not config.quiet`` block: an exit code that changes
    # with the verbosity flag is useless to a caller.
    if (
        run_report is not None
        and not config.dry_run
        and any(not clip.meets_minimum for clip in run_report.clips)
    ):
        logger.warn("Alguns clips ficaram abaixo da duracao minima configurada.")
        return 3, run_report, "alguns clips ficaram abaixo da duracao minima"
    return 0, run_report, ""


def run_batch_mode(
    config: config_mod.ClipConfig,
    batch_file: str,
    manifest_file: str | None,
    logger: Logger,
    *,
    retry_failed: bool = False,
) -> int:
    """Process a batch file with a resumable SQLite manifest."""
    try:
        urls = batch.load_urls(batch_file)
    except ClipperError as exc:
        logger.warn(str(exc))
        return 2

    manifest = (
        Path(manifest_file)
        if manifest_file
        else Path(config.output_dir) / "batch.sqlite3"
    )
    connection = batch.open_manifest(manifest)
    try:
        added = batch.seed(connection, urls)
        logger.info(
            f"Manifesto {manifest}: {added} URL(s) nova(s) de {len(urls)} no arquivo"
        )

        def runner(url: str) -> tuple[int, str, int, str]:
            # Every URL gets its own output and scratch directory: the reports
            # are per-video, and two workers must never share a temp folder.
            slug = batch.url_slug(url)
            job_config = dataclasses.replace(
                config, url=url, output_dir=Path(config.output_dir) / slug
            )
            if config.work_dir is not None:
                job_config.work_dir = Path(config.work_dir) / slug
            code, run_report, error = run_single(job_config, logger)
            clips = len(run_report.clips) if run_report else 0
            return code, error, clips, str(job_config.output_dir)

        summary = batch.run_batch(
            connection, runner, logger, retry_failed=retry_failed
        )
        if not config.quiet:
            print()
            print(batch.format_summary(connection, summary))
        json_path = batch.write_json(connection, Path(config.output_dir) / "batch.json")
        logger.ok(f"Manifesto exportado: {json_path}")
    except KeyboardInterrupt:
        logger.warn("Lote interrompido; rode de novo para retomar de onde parou.")
        return 130
    finally:
        connection.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    # First thing: a redirected stdout on Windows is cp1252, and one combining
    # mark in a video title would otherwise kill the run with UnicodeEncodeError.
    util.configure_stdio()
    # Build a temporary parser just to extract --config without consuming the rest.
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", dest="config_file", default=None)
    pre_args, _ = pre_parser.parse_known_args(argv)

    parser = build_parser()

    logger = Logger(quiet=False, verbose=False)
    if pre_args.config_file:
        cfg_path = Path(pre_args.config_file)
        try:
            file_defaults = config_file.load_config_file(cfg_path, logger)
            config_file.apply_defaults(parser, file_defaults, logger)
        except ClipperError as exc:
            logger.warn(str(exc))
            return 2

    args = parser.parse_args(argv)
    logger.quiet = args.quiet
    logger.verbose = args.verbose

    # Informational template flags short-circuit before any URL is required:
    # they are how you discover a composition without reading the source.
    if getattr(args, "list_templates", False):
        return _list_templates()
    if getattr(args, "describe_template", None):
        return _describe_template(args.describe_template, args.width, args.height, logger)

    try:
        config = config_from_args(args)
        config.validate()
    except ValueError as exc:
        logger.warn(str(exc))
        return 2

    if not config.url and not args.batch:
        logger.warn("Informe uma URL ou use --batch ARQUIVO.")
        return 2
    if config.url and args.batch:
        logger.warn("Use --batch sem URL: o arquivo ja traz as URLs.")
        return 2

    try:
        config.ffmpeg, config.ffprobe = util.ffmpeg_binaries(config.ffmpeg, config.ffprobe)
    except ClipperError as exc:
        logger.warn(str(exc))
        return 2

    if config.layout == "focus":
        logger.info(f"Reenquadramento: {reframe.describe_backend()}")
    if config.ranker != "none":
        logger.info(
            f"Ranker semantico: {config.ranker_model} "
            f"(top {config.ranker_top_n}, peso {config.ranker_weight:.2f})"
        )

    if args.batch:
        return run_batch_mode(
            config, args.batch, args.manifest, logger, retry_failed=args.retry_failed
        )

    try:
        code, _run_report, _error = run_single(config, logger)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        logger.warn("Interrompido pelo usuario.")
        return 130
    return code


def _print_plan(windows: list[score.Window], logger: Logger) -> None:
    logger.step(f"Top {len(windows)} janelas selecionadas")
    for position, window in enumerate(windows, start=1):
        terms = ", ".join(window.hook_terms[:3]) or "-"
        logger.info(
            f"  {position:02d}. {util.fmt_clock(window.start)} - "
            f"{util.fmt_clock(window.end)} | {window.duration:5.1f}s | "
            f"score {window.score:5.1f} | {terms}"
        )
        if window.text:
            logger.info(f'      "{window.text[:110].strip()}"')


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())