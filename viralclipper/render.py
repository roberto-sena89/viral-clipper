"""Rendering of the selected windows into ready-to-post vertical clips.

The renderer is where the "viral" look is produced:

* 9:16 canvas with the original framing preserved (center crop, blurred
  background or letterboxed fit);
* burned karaoke captions where the spoken word is highlighted;
* EBU R128 loudness normalization so every clip sounds equally loud;
* optional jump cutting that removes the silences inside the window.

ffmpeg runs with ``cwd`` set to the per-clip work directory, which keeps the
``ass`` filter path free of Windows drive-letter escaping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import caption_presets, reframe, template as template_mod, util
from .config import ClipConfig
from .transcribe import Word
from .util import ClipperError, Logger

CAPTION_FILE = "captions.ass"
MAX_LINE_SECONDS = 2.6
# The headline is the clip's hook burned as on-screen text at the top of the
# frame. 12 words keeps a full spoken sentence (two wrapped lines) instead of
# chopping it mid-thought; when a sentence is longer than that it is cut and
# marked with an ellipsis.
HEADLINE_MAX_WORDS = 12
HEADLINE_FAD_IN_MS = 120
HEADLINE_FAD_OUT_MS = 300

# WrapStyle 0 = smart wrapping: libass breaks long lines at word boundaries
# inside the style margins. Style 2 (the previous value) never wraps, which is
# why a headline wider than the frame ran off both edges instead of breaking
# into two lines.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},&H000000FF,{outline_c},{back},{bold},{italic},0,0,100,100,0,0,{border_style},{outline_w},{shadow},2,90,90,{margin_v},1
Style: Headline,{font},{headline_size},{highlight},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _highlight_on(style) -> str:
    """Karaoke tag that paints the spoken word with the preset's highlight."""
    return "{\\1c" + style.highlight_color + f"&\\fscx{style.highlight_scale}\\fscy{style.highlight_scale}}}"


def _highlight_off(style) -> str:
    """Karaoke tag that returns the text to the preset's primary color."""
    return "{\\1c" + style.primary_color + r"&\fscx100\fscy100}"


@dataclass
class RenderedClip:
    """Result of one ffmpeg render."""

    path: Path
    duration: float
    width: int
    height: int


def _escape_ass(text: str) -> str:
    cleaned = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    return " ".join(cleaned.split())


def _group_words(words: list[Word], words_per_line: int) -> list[list[Word]]:
    """Split words into caption lines bounded by count and duration."""
    lines: list[list[Word]] = []
    current: list[Word] = []
    limit = max(1, words_per_line)
    for word in words:
        if current:
            span = word.end - current[0].start
            if len(current) >= limit or span > MAX_LINE_SECONDS:
                lines.append(current)
                current = []
        current.append(word)
    if current:
        lines.append(current)
    return lines


def _headline_text(words: list[Word], config: ClipConfig, style) -> str:
    """Resolve the burned opening headline; '' when the feature is off.

    With no explicit ``headline_text`` the headline is the clip's own opening:
    the first words up to the first sentence ending, capped at
    ``HEADLINE_MAX_WORDS`` so it always fits on two wrapped lines.
    """
    if config.headline_seconds <= 0:
        return ""
    if config.headline_text is not None:
        text = config.headline_text
    else:
        picked: list[str] = []
        truncated = False
        for word in words:
            picked.append(word.text)
            if word.text.rstrip().endswith((".", "!", "?", "…")):
                break
            if len(picked) >= HEADLINE_MAX_WORDS:
                truncated = True
                break
        text = " ".join(picked)
        if truncated:
            # Never leave a half sentence looking complete.
            text = text.rstrip(" ,;:") + "…"
    text = " ".join(text.split())
    if not text:
        return ""
    if style.uppercase:
        text = text.upper()
    return text


def build_captions(
    words: list[Word],
    clip_start: float,
    destination: Path,
    config: ClipConfig,
    template=None,
) -> Path | None:
    """Write the ASS file: karaoke captions plus the opening headline.

    The headline does not depend on the caption style: even a ``none`` run
    still gets the on-screen hook, because the two solve different problems
    (readability vs. first-two-seconds retention).
    """
    events: list[str] = []
    style = caption_presets.resolve(config)
    # On a split template the captions belong to the video band, not to the
    # canvas bottom, so the margin is lifted to the band edge.
    margin_v = _caption_margin_for_band(config, style, template)

    headline = _headline_text(words, config, style)
    if headline:
        events.append(
            f"Dialogue: 1,{util.ass_timestamp(0.0)},{util.ass_timestamp(config.headline_seconds)},"
            f"Headline,,0,0,0,,{{\\fad({HEADLINE_FAD_IN_MS},{HEADLINE_FAD_OUT_MS})}}"
            f"{_escape_ass(headline)}"
        )

    if config.caption_style != "none" and words:
        local: list[Word] = []
        for word in words:
            start = max(0.0, word.start - clip_start)
            end = max(start, word.end - clip_start)
            text = word.text
            if style.uppercase:
                text = text.upper()
            local.append(Word(start=start, end=end, text=text, probability=word.probability))

        highlight_on = _highlight_on(style)
        highlight_off = _highlight_off(style)
        for line in _group_words(local, style.words_per_line):
            line_start = line[0].start
            line_end = max(line[-1].end, line[0].start + 0.25)
            if config.caption_style == "block":
                body = _escape_ass(" ".join(word.text for word in line))
                events.append(
                    f"Dialogue: 0,{util.ass_timestamp(line_start)},{util.ass_timestamp(line_end)},"
                    f"Default,,0,0,0,,{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}{body}"
                )
                continue
            for position, word in enumerate(line):
                begin = word.start
                finish = line[position + 1].start if position + 1 < len(line) else line_end
                if finish - begin < 0.08:
                    finish = begin + 0.08
                pieces = []
                for index, item in enumerate(line):
                    if index == position:
                        pieces.append(highlight_on + _escape_ass(item.text) + highlight_off)
                    else:
                        pieces.append(_escape_ass(item.text))
                body = " ".join(pieces)
                events.append(
                    f"Dialogue: 0,{util.ass_timestamp(begin)},{util.ass_timestamp(finish)},"
                    f"Default,,0,0,0,,{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}{body}"
                )

    if not events:
        return None

    # BorderStyle 3 paints the box with the OUTLINE colour in libass, so a boxed
    # preset's box_color moves there; the back colour stays as the shadow.
    boxed = style.border_style == 3 and style.box_color
    header = ASS_HEADER.format(
        width=config.width,
        height=config.height,
        font=style.font,
        size=style.font_size,
        primary=style.primary_color,
        outline_c=style.box_color if boxed else style.outline_color,
        back=style.box_color if boxed else style.back_color,
        bold=-1 if style.bold else 0,
        italic=-1 if style.italic else 0,
        border_style=style.border_style,
        outline_w=f"{style.outline_width:g}",
        shadow=f"{style.shadow_depth:g}",
        margin_v=margin_v,
        headline_size=config.headline_font_size or 100,
        highlight=style.highlight_color,
    )
    destination.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return destination



def extract_frame(
    *,
    ffmpeg: str,
    source: str | Path,
    at: float,
    destination: Path,
    logger: Logger | None = None,
) -> Path | None:
    """Grab one still from ``source`` for a template's ``frame`` zone.

    This is the "poster" idea: a template can show a large still derived from
    the clip itself instead of an external asset, which means a channel can
    ship a branded split layout with no image files at all. ``at`` is in source
    timeline seconds; the caller has already resolved any section origin.

    A missing frame is never fatal: the zone degrades to the clip video, which
    still produces a valid clip. Failing the whole render because one poster
    could not be grabbed would be a bad trade.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if at > 0:
        args += ["-ss", util.fmt_clock_ms(at)]
    # One frame is enough; -frames:v 1 lets ffmpeg exit on its own.
    args += ["-i", str(Path(source).resolve()), "-frames:v", "1", "-q:v", "2", str(destination)]
    proc = util.run(args, logger=logger, check=False)
    if proc.returncode != 0 or not destination.exists():
        if logger:
            logger.warn("Nao consegui extrair o frame do template; usando o video.")
        return None
    return destination


def _caption_margin_for_band(config: ClipConfig, style, template) -> int:
    """Caption bottom margin that keeps the text inside the video band.

    ``margin_v`` is measured from the bottom of the canvas, but on a split
    template the captions belong to the *video* band, which ends well above the
    bottom. Without this the text lands on top of the image zone. The preset's
    own margin is kept as a floor so a template with a full-height video band
    behaves exactly as before.
    """
    if template is None:
        return style.margin_v
    video_zone = template.video_zone
    if video_zone is None or video_zone.fraction >= 1.0:
        return style.margin_v
    below = config.height - int(round(config.height * video_zone.fraction))
    # The preset's margin is a bottom gutter inside a full-height frame; carry
    # the same gutter up to the band edge.
    return below + style.margin_v


def _layout_filter(
    config: ClipConfig,
    source_width: int,
    source_height: int,
    crop_x: int | None = None,
) -> str:
    """Scale/crop the source frame into the target canvas.

    ``crop_x`` only affects the ``center``/``focus`` geometry: it is the
    horizontal offset the crop starts at. ``None`` keeps ffmpeg's centered
    default, which is what ``center`` always uses and what ``focus`` falls back
    to when no face was found.
    """
    if not config.vertical:
        if config.max_height > 0 and source_height > config.max_height:
            return f"scale=-2:{config.max_height},setsar=1"
        return "setsar=1"

    width, height = config.width, config.height
    if config.layout == "fit":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    if config.layout == "blur":
        return (
            f"split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=luma_radius=32:luma_power=2,setsar=1[bgb];"
            f"[fg]scale={width}:-2:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    # "center" and "focus" share the geometry; only the offset differs.
    if crop_x is not None:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:{crop_x}:(ih-oh)/2,setsar=1"
        )
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def _loudnorm_filter(config: ClipConfig) -> str:
    return f"loudnorm=I={config.target_lufs:g}:TP=-1.5:LRA=11:print_format=summary"


def _progress_bar_filter(config: ClipConfig, duration: float) -> str:
    """Filter fragment that draws the watched-progress bar at the frame top.

    drawbox re-evaluates ``w`` on every frame, so ``iw*t/duration`` is the fill
    ratio. ``t`` is clip-local because the chain starts with
    ``setpts=PTS-STARTPTS``, which also makes it correct after a jump cut: the
    concatenated segments restart the clock and ``duration`` is the kept total.
    The expression carries no ``:`` or ``,`` so it needs no quoting inside the
    filter graph.
    """
    if not config.progress_bar or config.progress_bar_height <= 0 or duration <= 0:
        return ""
    return (
        f"drawbox=x=0:y=0:w=iw*t/{duration:.3f}:h={config.progress_bar_height}"
        f":color={config.progress_bar_color}@0.9:t=fill"
    )


def _trim_graph(keep_ranges: list[tuple[float, float]]) -> str:
    """Filter graph that keeps only the given (clip local) ranges."""
    parts: list[str] = []
    pairs: list[str] = []
    for index, (start, end) in enumerate(keep_ranges):
        parts.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]")
        parts.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]")
        pairs.append(f"[v{index}][a{index}]")
    parts.append(f"{''.join(pairs)}concat=n={len(keep_ranges)}:v=1:a=1[vraw][araw]")
    return ";".join(parts)


def has_audio_stream(ffprobe: str, media: str | Path, logger: Logger | None = None) -> bool:
    """Return True when the file exposes at least one audio stream."""
    proc = util.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(media),
        ],
        logger=logger,
        check=False,
    )
    return bool((proc.stdout or "").strip())


def plan_keep_ranges(
    start: float,
    end: float,
    silences: list[tuple[float, float]],
    *,
    min_kept: float,
    max_kept: float,
    tail_pad: float = 0.12,
) -> list[tuple[float, float]]:
    """Compute which parts of ``[start, end]`` survive a jump cut.

    Ranges are relative to ``start``. When removing silences would make the
    clip shorter than ``min_kept``, silences are dropped from the plan until
    the result fits, so jump cutting can never break the minimum duration.
    """
    duration = end - start
    if duration <= 0:
        return [(0.0, 0.0)]

    def build(limit: int) -> list[tuple[float, float]]:
        ranges: list[tuple[float, float]] = []
        cursor = 0.0
        for quiet_start, quiet_end in silences[:limit]:
            cut_start = max(0.0, quiet_start - start + tail_pad)
            cut_end = min(duration, quiet_end - start - tail_pad)
            if cut_end - cut_start < 0.12 or cut_start <= cursor:
                continue
            ranges.append((cursor, cut_start))
            cursor = cut_end
        ranges.append((cursor, duration))
        return [(round(a, 3), round(b, 3)) for a, b in ranges if b - a > 0.10]

    ranges = build(len(silences))
    kept = sum(b - a for a, b in ranges)
    limit = len(silences)
    while kept < min_kept and limit > 0:
        limit -= 1
        ranges = build(limit)
        kept = sum(b - a for a, b in ranges)
    if kept > max_kept:
        # Never let a jump cut make the clip longer than the window asks for.
        return [(0.0, round(duration, 3))]
    return ranges



def _apply(filters: str, extra: str) -> str:
    """Append ``extra`` to the last segment of a ``;`` separated filter graph.

    This is needed for the blur layout, which is a multi segment graph, so the
    caption stage ends up attached to the overlay output.
    """
    if not extra:
        return filters
    # An empty ``filters`` is the composer's case: the graph is fully built and
    # only the overlay stage is appended to its labelled output.
    if not filters:
        return extra
    head, separator, tail = filters.rpartition(";")
    if separator:
        return f"{head}{separator}{tail},{extra}"
    return f"{filters},{extra}"


def len_for_compose(template) -> int:
    """Count the zones a template actually draws.

    ``captions`` never draws a band (libass positions the text absolutely), so
    a template of one video zone plus captions needs no composer at all and
    keeps the original single-stream render path.
    """
    return sum(1 for zone in template.zones if zone.kind != "captions")


def _template_stills(
    template,
    config: ClipConfig,
    ffmpeg: str,
    source: str | Path,
    work: Path,
    clip_start: float,
    logger: Logger | None,
) -> list[str]:
    """Resolve every still a template needs, in zone order.

    ``image`` zones use their own file; ``frame`` zones get one grabbed from the
    clip. A zone whose still cannot be produced falls back to the clip video
    rather than failing the render, so a missing logo file degrades the look
    instead of costing the whole clip.
    """
    inputs: list[str] = []
    for index, zone in enumerate(template.zones):
        if zone.kind == "image":
            candidate = Path(zone.source)
            if not candidate.is_absolute():
                candidate = Path(config.output_dir) / candidate
            if candidate.exists():
                inputs.append(str(candidate.resolve()))
                continue
            if logger:
                logger.warn(f"Imagem da zona {index + 1} nao encontrada: {zone.source}")
            # Point at the clip so the overlay stays valid (an input index must
            # exist even when its content is unusable).
            inputs.append(str(Path(source).resolve()))
        elif zone.kind == "frame":
            still = work / f"zone{index}.jpg"
            grabbed = extract_frame(
                ffmpeg=ffmpeg,
                source=source,
                at=clip_start + max(0.0, zone.frame_at),
                destination=still,
                logger=logger,
            )
            inputs.append(str(grabbed.resolve() if grabbed else Path(source).resolve()))
    return inputs


def _focus_crop_x(
    config: ClipConfig,
    ffmpeg: str,
    source: str | Path,
    seek_start: float,
    seek_end: float,
    source_width: int,
    source_height: int,
    work: Path,
    logger: Logger | None,
) -> int | None:
    """Horizontal crop offset for the ``focus`` layout, ``None`` for centered."""
    if not config.vertical or config.layout != "focus":
        return None
    center = reframe.focus_center_x(
        ffmpeg, source, seek_start, seek_end, work / "reframe", logger=logger
    )
    if center is None:
        return None
    return reframe.plan_crop_x(
        source_width, source_height, config.width, config.height, center
    )


def render_clip(
    *,
    source: str | Path,
    destination: str | Path,
    clip_start: float,
    clip_end: float,
    config: ClipConfig,
    ffmpeg: str,
    ffprobe: str,
    words: list[Word] | None = None,
    silences: list[tuple[float, float]] | None = None,
    work_dir: str | Path,
    logger: Logger | None = None,
    source_origin: float = 0.0,
    template=None,
) -> RenderedClip:
    """Cut, reframe, caption and normalize one window into a final clip.

    ``clip_start``/``clip_end`` and ``silences`` are always expressed in the
    *source video's* timeline. ``source_origin`` is the timestamp that
    corresponds to 0:00 of the file being cut: 0 for a full download, and the
    section offset when the media is a section download whose timeline was
    reset. Everything that seeks inside the file subtracts it.

    ``template`` is optional. ``None`` (and any template that only fills the
    canvas with the video) renders through the original single-stream path, so
    existing behaviour is unchanged.
    """
    work = util.ensure_dir(work_dir)
    destination = Path(destination).resolve()

    window_start = max(0.0, clip_start - config.pad_start)
    window_end = clip_end + config.pad_end
    duration = max(0.1, window_end - window_start)

    caption_path = build_captions(
        words or [], clip_start, work / CAPTION_FILE, config, template
    )
    source_width, source_height = util.probe_video_size(ffprobe, source, logger)
    audio_available = has_audio_stream(ffprobe, source, logger)

    seek_start = max(0.0, window_start - source_origin)
    seek_end = max(seek_start, window_end - source_origin)

    crop_x = _focus_crop_x(
        config, ffmpeg, source, seek_start, seek_end,
        source_width, source_height, work, logger,
    )
    layout = _layout_filter(config, source_width, source_height, crop_x)
    caption_stage = f"ass={CAPTION_FILE}" if caption_path and config.burn_captions else ""

    graph_parts: list[str] = []
    video_input = "[0:v]"
    audio_input = "[0:a]"
    if audio_available and config.jump_cut and silences:
        keep_ranges = plan_keep_ranges(
            window_start,
            window_end,
            silences,
            min_kept=config.min_duration,
            max_kept=duration,
        )
        if len(keep_ranges) > 1:
            graph_parts.append(_trim_graph(keep_ranges))
            video_input = "[vraw]"
            audio_input = "[araw]"
            duration = sum(b - a for a, b in keep_ranges)

    # Built after the jump cut block so the progress bar gets the kept duration,
    # not the window duration.
    overlay_chain = ",".join(
        stage
        for stage in (caption_stage, _progress_bar_filter(config, duration))
        if stage
    )

    # A template that draws more than a full-canvas video zone needs its own
    # graph: the plain layout filter only ever produces one full-canvas stream.
    zones_need_composing = (
        template is not None and len_for_compose(template) > 1
    )
    extra_inputs: list[str] = []
    if zones_need_composing:
        extra_inputs = _template_stills(
            template, config, ffmpeg, source, work / "zones", seek_start, logger
        )
        compose_graph, compose_label = template_mod.compose(
            template,
            config.width,
            config.height,
            # After a jump cut the clip is no longer 0:v; handing the composer
            # the live label keeps the cut in the graph instead of silently
            # dropping it.
            video_input=video_input.strip("[]"),
        )
        graph_parts.append(compose_graph)
        # The composer relabels the clip input itself; the caption and progress
        # stages then run over the composed canvas so the burnt text sits on top
        # of every zone. With neither stage enabled the label is all that is
        # needed - appending a bare comma would make ffmpeg parse an empty
        # filter name and fail the whole render.
        if overlay_chain:
            graph_parts.append(f"{compose_label}{overlay_chain}[vout]")
        else:
            graph_parts.append(f"{compose_label}null[vout]")
    else:
        video_chain = f"{video_input}setpts=PTS-STARTPTS,{_apply(layout, overlay_chain)}[vout]"
        graph_parts.append(video_chain)

    maps = ["-map", "[vout]"]
    if audio_available:
        audio_chain = f"{audio_input}asetpts=PTS-STARTPTS"
        if config.loudnorm:
            audio_chain += f",{_loudnorm_filter(config)}"
        audio_chain += "[aout]"
        graph_parts.append(audio_chain)
        maps += ["-map", "[aout]"]

    temp_output = work / f"render{Path(destination).suffix or '.mp4'}"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        util.fmt_clock_ms(seek_start),
        "-i",
        str(Path(source).resolve()),
    ]
    # Still zones are appended after the clip, in zone order, because the
    # composer addresses them positionally as [1:v], [2:v], ...
    for still in extra_inputs:
        command += ["-i", still]
    command += [
        "-sn",
        "-filter_complex",
        ";".join(graph_parts),
        *maps,
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-pix_fmt",
        "yuv420p",
    ]
    if config.threads > 0:
        command += ["-threads", str(config.threads)]
    if audio_available:
        command += ["-c:a", "aac", "-b:a", config.audio_bitrate]
    else:
        command += ["-an"]
    command += ["-movflags", "+faststart", temp_output.name]

    util.run_streaming(command, cwd=work, logger=logger)

    if not temp_output.exists():
        raise ClipperError(f"ffmpeg did not create {temp_output}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    temp_output.replace(destination)

    final_duration = util.probe_duration(ffprobe, destination, logger) or duration
    out_width, out_height = util.probe_video_size(ffprobe, destination, logger)

    if caption_path and not config.burn_captions:
        # Captions were requested but not burned in: ship a sidecar instead.
        sidecar = destination.with_suffix(".srt")
        util.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", CAPTION_FILE, str(sidecar)],
            cwd=work,
            logger=logger,
            check=False,
        )

    return RenderedClip(
        path=destination,
        duration=round(final_duration, 3),
        width=out_width or config.width,
        height=out_height or config.height,
    )
