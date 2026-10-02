"""Run configuration for the viral-clipper pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClipConfig:
    """Every knob the pipeline needs, with sane defaults for viral shorts.

    Durations are always in seconds. ``min_duration`` is a hard guarantee: a
    rendered clip is rejected if the final file is shorter than this value.
    """

    url: str
    output_dir: Path = Path("output")
    work_dir: Path | None = None

    # --- selection -------------------------------------------------------
    min_duration: float = 30.0
    max_duration: float = 60.0
    target_duration: float = 42.0
    # 0 = automatico: o proprio video decide quantos cortes rende, pelo
    # portao relativo ``auto_margin``. Qualquer valor > 0 e um teto fixo.
    count: int = 0
    engine: str = "hybrid"  # hybrid | audio | transcript
    min_gap: float = 6.0  # minimum silence kept between two accepted clips
    pad_start: float = 0.25
    pad_end: float = 0.35
    # Absolute quality gate on the 0..100 window score. Because the score is
    # computed from video-independent bounds, this is meaningful across videos:
    # set it to e.g. 45 to refuse clips that are only "the best of a bad
    # video". 0 disables the gate and always yields the top ``count`` windows.
    min_score: float = 0.0

    # --- modo automatico -------------------------------------------------
    # Quantos pontos abaixo do melhor corte do video ainda contam como "vale
    # cortar". O piso e relativo ao proprio video, entao nao precisa de
    # calibracao: um podcast excelente rende dezenas de cortes, um video
    # fraco rende poucos. So vale quando ``count`` e 0.
    auto_margin: float = 15.0
    # Teto de seguranca do modo automatico, para um video patologico nao
    # gerar milhares de cortes. 200 e o maximo fisico de um video de 2 h em
    # trechos de 30 s com 6 s de intervalo.
    auto_ceiling: int = 200
    # Tolerancia acima de ``max_duration`` para o corte fechar o raciocinio.
    # O candidato passa a existir ate ``max_duration + max_duration_grace``,
    # mas o sinal ``length`` continua penalizando quem se afasta do alvo:
    # o corte longo tem de merecer.
    max_duration_grace: float = 30.0

    # --- transcription ---------------------------------------------------
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    language: str | None = None
    beam_size: int = 1
    vad_filter: bool = True
    # Cache de transcricao. Quando nao configurado, o cache persiste em
    # output/cache/transcripts, fora do diretorio temporario do trabalho, para
    # poder ser reaproveitado entre execucoes do mesmo video.
    cache_dir: Path | None = None
    transcript_cache: bool = True
    # --- supplied transcript ------------------------------------------------
    # When either is set, whisper is skipped entirely and the pipeline scores
    # the transcript the user provided (SRT, WebVTT, a pasted YouTube caption
    # panel, or plain prose). transcript_text wins when both are present.
    transcript_text: str | None = None
    transcript_file: Path | None = None
    # Write a viralization analysis (viral_report.md) next to the clips.
    viral_report: bool = True

    # --- semantic re-ranking (optional) -----------------------------------
    # The heuristic ranks every candidate on cheap signals; the ranker then
    # re-judges the best few with a language model. Off by default so the
    # pipeline never needs network access or an API key unless asked.
    ranker: str = "none"  # none | llm
    # A named provider from ``viralclipper.providers``. When set, it fills in
    # base_url, model, api_key_env and requires_key, so the panel offers a
    # dropdown instead of two text fields a person has to remember. Empty keeps
    # the manual fields below authoritative, which is what a TOML file written
    # by hand already relies on.
    ranker_provider: str = ""
    ranker_model: str = "gpt-4o-mini"
    # Any OpenAI-compatible /chat/completions endpoint: OpenAI, DeepSeek,
    # Groq, Together, OpenRouter, or a local Ollama/LM Studio.
    ranker_base_url: str = "https://api.openai.com/v1"
    ranker_api_key_env: str = "OPENAI_API_KEY"
    # Local endpoints usually need no key; set this to False for them.
    ranker_requires_key: bool = True
    # --- curator prompt -----------------------------------------------------
    # Path to the free-form prompt that tells the model what a viral clip is.
    # The file holds prose only; the JSON contract the parser needs is appended
    # by the code, so any prompt can be dropped in verbatim without the author
    # having to also get a response schema right. Empty falls back to the
    # built-in prompt in ``ranker.py``.
    curator_prompt_file: Path | None = None
    # How many of the heuristic's best candidates get a model call. This is the
    # cost dial: one call per candidate per video.
    ranker_top_n: int = 24
    # 0 = pure heuristic, 1 = pure model. The middle keeps the cheap signals
    # (loudness, boundary, length) as a tie-breaker.
    ranker_weight: float = 0.6
    ranker_timeout: float = 60.0

    # --- audio analysis --------------------------------------------------
    silence_db: float | None = None  # None means adaptive threshold
    min_silence: float = 0.32
    hop_ms: int = 20

    # --- download --------------------------------------------------------
    download_mode: str = "sections"  # sections | full
    max_height: int = 1080
    cookies_from_browser: str | None = None
    extra_ytdlp_args: list[str] = field(default_factory=list)
    # Language every yt-dlp metadata call asks the site for. YouTube localizes
    # everything it returns: the same video is listed as "LULA HAS LOST CONTROL
    # OF THE GOVERNMENT" by default and as "LULA PERDEU CONTROLE do GOVERNO"
    # with "pt", and an English original ("I Built A City...") comes back
    # already in Portuguese. Titles are what the report, the file listing and
    # the web panel show, so the choice is made here once and applied to every
    # call. Use "" to leave the site's own default alone. Codes are the ones
    # yt-dlp accepts ("pt", "en", "es"); "pt-BR" is rejected by yt-dlp itself.
    metadata_language: str = "pt"

    # --- rendering -------------------------------------------------------
    vertical: bool = True
    # focus = crop guided by face detection when a detector is installed, and a
    # plain center crop otherwise. center | blur | fit | focus
    layout: str = "focus"
    width: int = 1080
    height: int = 1920
    burn_captions: bool = True
    caption_style: str = "karaoke"  # karaoke | block | none
    # --- captions ------------------------------------------------------------
    # A preset bundles the whole caption look; any field left as None inherits
    # it from the preset. Override only what you want to change.
    caption_preset: str = "karaoke"
    caption_words_per_line: int | None = None
    font: str | None = None
    font_size: int | None = None
    caption_margin_v: int | None = None
    uppercase_captions: bool | None = None
    highlight_color: str | None = None
    # Light/dark box theme for the burned captions. None keeps the preset's
    # own colors; "light"/"dark" force the classic light/dark box+text pair
    # (font, size and highlight still come from the preset).
    caption_box_theme: str | None = None
    # Burned headline at the top of the frame for the opening seconds of every
    # clip. Off by default: the shipped look is captions only. Turn it on when
    # the hook deserves on-screen text (a strong opening question, a leak, a
    # number list). ``--headline-seconds 3`` or the UI toggle enable it.
    headline_seconds: float = 0.0
    # None derives the headline from the clip's opening words.
    headline_text: str | None = None
    headline_font_size: int | None = None
    # Symmetric side margins of the burned headline in px (libass MarginL/R).
    # Keeps the hook clear of the notch and the right-side action rail.
    headline_margin_side: int | None = None
    # Manual reframe override for the center/focus crop. zoom >= 1 magnifies
    # around the pan point; pan 0..1 slides the crop window (0.5 = centered).
    # None means "leave the layout alone".
    reframe_zoom: float | None = None
    reframe_pan_x: float | None = None
    reframe_pan_y: float | None = None
    # libass top-row alignment of the burned headline: left, center, right.
    headline_align: str = "center"
    # Thin bar at the top of the frame showing watched progress. Off by default:
    # the shipped look is the video plus captions only. Enable it with
    # ``--progress-bar`` or the UI toggle when the platform's own progress hint
    # is not enough.
    progress_bar: bool = False
    progress_bar_height: int = 10
    # Any ffmpeg color (name or 0xRRGGBB).
    progress_bar_color: str = "yellow"
    loudnorm: bool = True
    target_lufs: float = -14.0
    jump_cut: bool = False
    crf: int = 20
    preset: str = "veryfast"
    audio_bitrate: str = "192k"
    # --- template -------------------------------------------------------------
    # A named composition (zones stacking video, stills and a caption band).
    # None keeps the original single-zone full-frame render. A built-in name
    # ("split-card") or a path to a .toml/.yaml template file both work.
    template: str | None = None
    # Cross the template with these axes: every preset x every layout renders
    # the same window again. This is the output multiplier - one analysis pass,
    # many deliverables - so the only cost is the extra encodes.
    variant_presets: list[str] = field(default_factory=list)
    variant_layouts: list[str] = field(default_factory=list)
    # libx264 allocates its frame buffers per thread, so the ffmpeg default
    # (one thread per core) is what makes a 1080x1920 render run out of memory
    # on an 8 GB machine. 2 keeps a single encode comfortable.
    threads: int = 2

    # --- misc ------------------------------------------------------------
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    quiet: bool = False
    verbose: bool = False
    keep_temp: bool = False
    dry_run: bool = False

    # --- execution ------------------------------------------------------
    # Render the selected clips in worker processes. Each clip is independent
    # once its section has been downloaded, so this is a straight win on a
    # multi core machine. ``workers`` caps the pool size and is deliberately
    # small by default: every worker runs its own libx264 encode, and a
    # 1080x1920 encode is not cheap in RAM, so "one per core" is a reliable way
    # to run the machine out of memory. Raise it on a box with plenty of RAM.
    parallel: bool = True
    workers: int = 2

    @property
    def hard_max_duration(self) -> float:
        """Teto rigido de duracao de um candidato.

        ``max_duration`` e o alvo editorial; a tolerancia existe para o
        corte conseguir fechar o raciocinio. Nada alem disto chega a virar
        candidato, entao este e o numero que limita o custo do scoring.
        """
        return self.max_duration + max(0.0, self.max_duration_grace)

    def work_path(self) -> Path:
        return Path(self.work_dir) if self.work_dir else Path(self.output_dir) / "_work"

    def validate(self) -> None:
        if self.min_duration <= 0:
            raise ValueError("min_duration must be greater than zero")
        if self.max_duration < self.min_duration:
            raise ValueError("max_duration must be greater than or equal to min_duration")
        if self.count < 0:
            raise ValueError("count must not be negative (0 means automatic)")
        if self.auto_margin < 0:
            raise ValueError("auto_margin must not be negative")
        if self.auto_ceiling < 1:
            raise ValueError("auto_ceiling must be at least 1")
        if self.max_duration_grace < 0:
            raise ValueError("max_duration_grace must not be negative")
        # Clamp instead of failing: a target outside the allowed range is a
        # harmless mistake that should not stop the run.
        self.target_duration = max(self.min_duration, min(self.max_duration, self.target_duration))
        if self.engine not in {"hybrid", "audio", "transcript"}:
            raise ValueError("engine must be hybrid, audio or transcript")
        if self.download_mode not in {"sections", "full"}:
            raise ValueError("download_mode must be sections or full")
        # The code is spliced into ``youtube:lang=<code>``, so a separator would
        # let a config file (or the web panel) smuggle in another extractor
        # argument.
        language = "" if self.metadata_language is None else str(self.metadata_language).strip()
        if any(separator in language for separator in (";", ":", "=", " ")):
            raise ValueError(
                "metadata_language must be a bare language code such as pt or en "
                "(empty asks for no language at all)"
            )
        self.metadata_language = language
        if self.layout not in {"center", "blur", "fit", "focus"}:
            raise ValueError("layout must be center, blur, fit or focus")
        if self.caption_style not in {"karaoke", "block", "none"}:
            raise ValueError("caption_style must be karaoke, block or none")
        if self.caption_margin_v is not None and self.caption_margin_v < 0:
            raise ValueError("caption_margin_v must not be negative")
        if self.highlight_color is not None and not self.highlight_color.startswith("&H"):
            raise ValueError("highlight_color must use the ASS format &HAABBGGRR")
        if self.caption_box_theme is not None and self.caption_box_theme not in {"light", "dark"}:
            raise ValueError("caption_box_theme must be light or dark")
        if self.headline_seconds < 0:
            raise ValueError("headline_seconds must not be negative")
        if self.headline_font_size is not None and self.headline_font_size <= 0:
            raise ValueError("headline_font_size must be greater than zero")
        if self.headline_margin_side is not None and self.headline_margin_side < 0:
            raise ValueError("headline_margin_side must not be negative")
        if self.headline_align not in {"left", "center", "right"}:
            raise ValueError("headline_align must be left, center or right")
        if self.reframe_zoom is not None and self.reframe_zoom < 1:
            raise ValueError("reframe_zoom must be 1 or greater")
        for axis in ("reframe_pan_x", "reframe_pan_y"):
            value = getattr(self, axis)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{axis} must be between 0 and 1")
        if self.caption_preset:
            # Raises ClipperError with the list of known presets when wrong.
            from . import caption_presets

            caption_presets.get_preset(self.caption_preset)
        if self.progress_bar_height < 0:
            raise ValueError("progress_bar_height must not be negative")
        if not 0.0 <= self.min_score <= 100.0:
            raise ValueError("min_score must be between 0 and 100")
        if self.ranker not in {"none", "llm"}:
            raise ValueError("ranker must be none or llm")
        if not 0.0 <= self.ranker_weight <= 1.0:
            raise ValueError("ranker_weight must be between 0 and 1")
        if self.ranker_top_n < 0:
            raise ValueError("ranker_top_n must not be negative")
        if self.ranker_timeout <= 0:
            raise ValueError("ranker_timeout must be greater than zero")
        if self.ranker_provider:
            # Validated here so a typo fails before the download, not after it.
            # Imported lazily: ``providers`` imports this module, and a
            # module-level import would be circular.
            from . import providers

            providers.get_provider(self.ranker_provider)
        if self.curator_prompt_file is not None:
            prompt_path = Path(self.curator_prompt_file)
            if not prompt_path.is_file():
                raise ValueError(
                    f"curator_prompt_file does not exist: {prompt_path}"
                )
        if self.workers < 0:
            raise ValueError("workers must not be negative")
        # The template is validated eagerly so a typo in a zone fraction fails
        # before a download, not after transcription.
        if self.template:
            from . import template as template_mod

            resolve_template(self.template, template_mod)
        for axis, field_name in (
            (self.variant_presets, "variant_presets"),
            (self.variant_layouts, "variant_layouts"),
        ):
            if not isinstance(axis, list):
                raise ValueError(f"{field_name} must be a list of strings")
        for preset in self.variant_presets:
            if not isinstance(preset, str):
                raise ValueError("variant_presets must contain only strings")
        for layout in self.variant_layouts:
            if layout not in {"center", "blur", "fit", "focus"}:
                raise ValueError(
                    f"variant_layouts contains '{layout}'; "
                    "must be center, blur, fit or focus"
                )


def resolve_template(name: str, template_mod=None):
    """Resolve a template reference: a built-in name or a path to a file.

    Accepting both from the same knob means the CLI needs one flag, not two,
    and a built-in can be promoted to a custom file by copying it out and
    passing the path instead of the name.
    """
    if template_mod is None:
        from . import template as template_mod

    candidate = Path(name)
    # A path wins over a name only when it actually exists, so a file called
    # "split-card" in the cwd cannot shadow the built-in by accident.
    if candidate.suffix.lower() in {".toml", ".yaml", ".yml"} or candidate.exists():
        return template_mod.load_template(candidate)
    return template_mod.get_template(name)
