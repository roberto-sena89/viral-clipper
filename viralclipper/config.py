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
    count: int = 5
    engine: str = "hybrid"  # hybrid | audio | transcript
    min_gap: float = 6.0  # minimum silence kept between two accepted clips
    pad_start: float = 0.25
    pad_end: float = 0.35
    # Absolute quality gate on the 0..100 window score. Because the score is
    # computed from video-independent bounds, this is meaningful across videos:
    # set it to e.g. 45 to refuse clips that are only "the best of a bad
    # video". 0 disables the gate and always yields the top ``count`` windows.
    min_score: float = 0.0

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
    ranker_model: str = "gpt-4o-mini"
    # Any OpenAI-compatible /chat/completions endpoint: OpenAI, DeepSeek,
    # Groq, Together, OpenRouter, or a local Ollama/LM Studio.
    ranker_base_url: str = "https://api.openai.com/v1"
    ranker_api_key_env: str = "OPENAI_API_KEY"
    # Local endpoints usually need no key; set this to False for them.
    ranker_requires_key: bool = True
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
    # Burned headline at the top of the frame for the opening seconds of every
    # clip. Off by default: the shipped look is captions only. Turn it on when
    # the hook deserves on-screen text (a strong opening question, a leak, a
    # number list). ``--headline-seconds 3`` or the UI toggle enable it.
    headline_seconds: float = 0.0
    # None derives the headline from the clip's opening words.
    headline_text: str | None = None
    headline_font_size: int | None = None
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

    def work_path(self) -> Path:
        return Path(self.work_dir) if self.work_dir else Path(self.output_dir) / "_work"

    def validate(self) -> None:
        if self.min_duration <= 0:
            raise ValueError("min_duration must be greater than zero")
        if self.max_duration < self.min_duration:
            raise ValueError("max_duration must be greater than or equal to min_duration")
        if self.count < 1:
            raise ValueError("count must be at least 1")
        # Clamp instead of failing: a target outside the allowed range is a
        # harmless mistake that should not stop the run.
        self.target_duration = max(self.min_duration, min(self.max_duration, self.target_duration))
        if self.engine not in {"hybrid", "audio", "transcript"}:
            raise ValueError("engine must be hybrid, audio or transcript")
        if self.download_mode not in {"sections", "full"}:
            raise ValueError("download_mode must be sections or full")
        if self.layout not in {"center", "blur", "fit", "focus"}:
            raise ValueError("layout must be center, blur, fit or focus")
        if self.caption_style not in {"karaoke", "block", "none"}:
            raise ValueError("caption_style must be karaoke, block or none")
        if self.caption_margin_v is not None and self.caption_margin_v < 0:
            raise ValueError("caption_margin_v must not be negative")
        if self.highlight_color is not None and not self.highlight_color.startswith("&H"):
            raise ValueError("highlight_color must use the ASS format &HAABBGGRR")
        if self.headline_seconds < 0:
            raise ValueError("headline_seconds must not be negative")
        if self.headline_font_size is not None and self.headline_font_size <= 0:
            raise ValueError("headline_font_size must be greater than zero")
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
