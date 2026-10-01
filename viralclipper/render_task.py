"""Render one clip, used by :func:`pipeline.render_windows`.

The module-level function is picklable: :class:`_RenderTask` carries everything
a worker needs in, and the worker returns the same task object with ``rendered``
and ``record`` filled in. Both are plain picklable values (``RenderedClip`` is a
dataclass of a ``Path``, floats and ints; ``ClipRecord`` is a dataclass of
primitives), so shipping the whole task back is what the pool already does - the
parent copies those two attributes onto its own task object.

The one invariant that matters: ``record`` is built from ``rendered`` here, in
the worker, right after the render. A record that says ``file = ""`` therefore
means this function decided the clip was never produced - it is not a reporting
detail the parent can repair.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import download, render, report, util
from .config import ClipConfig
from .util import ClipperError, Logger


@dataclass
class _RenderTask:
    position: int
    window: object  # score.Window, kept as object to avoid a circular import
    finish: float
    words: list
    silences: list[tuple[float, float]]
    media: Path | None
    metadata: dict
    config: ClipConfig
    clip_dir: Path
    output_dir: Path
    # Timestamp of the source video that corresponds to 0:00 of ``media``.
    # 0 for a full download; the section offset when the section timeline was
    # reset. See :func:`download.resolve_origin`.
    media_origin: float = 0.0
    # The template this task renders, when the run has one. None means the
    # original single-zone full-frame path.
    template: object | None = None
    # Suffix distinguishing this task from its siblings when one window is
    # rendered once per variant ("__neon-focus"). Empty for a plain run, which
    # keeps existing filenames byte-identical.
    variant: str = ""
    rendered: object | None = None
    record: object | None = None
    error: str | None = None


def _config_with_headline(config: ClipConfig, window) -> ClipConfig:
    """Return ``config`` carrying this window's own headline, when it has one.

    ``headline_text`` is a single string on the run-wide config, but one run
    produces several clips and each needs its own hook. The LLM ranker writes a
    headline per window, so it is copied in here, per task, immediately before
    the render.

    A copy rather than an assignment: this config object is shared by every
    worker in the pool, and mutating it would hand clip 3 the headline of clip
    7 depending on which worker got there first. The jump-cut retry below swaps
    a field of a copy for the same reason.

    Nothing is burned unless ``headline_seconds > 0``; that toggle stays the
    gate. A headline the model wrote with the feature switched off is still
    reported, so the work is never lost - it just does not alter the video.
    """
    headline = (getattr(window, "headline", "") or "").strip()
    if not headline:
        return config
    return replace(config, headline_text=headline)


def _render_task(task: _RenderTask) -> _RenderTask:
    """Render one clip in a worker process, writing the result into ``task``."""
    logger = Logger(quiet=task.config.quiet, verbose=task.config.verbose)
    clip_dir = task.clip_dir
    try:
        if task.media is not None:
            media = task.media
        else:
            media = download.download_section(
                task.config.url,
                task.window.start,
                task.finish,
                clip_dir / "section",
                task.config,
                logger,
            )
            task.media_origin = download.resolve_origin(
                task.config.ffprobe,
                media,
                max(0.0, task.window.start - download.SECTION_PADDING),
                logger,
            )

        filename = _clip_filename(task.position, task.window, task.metadata, task.variant)
        destination = task.output_dir / filename
        rendered = _render_with_retry(
            media=media,
            destination=destination,
            window=task.window,
            finish=task.finish,
            silences=task.silences,
            words=task.words,
            config=_config_with_headline(task.config, task.window),
            work=clip_dir,
            logger=logger,
            source_origin=task.media_origin,
            template=task.template,
        )
        task.rendered = rendered
    except Exception as exc:  # noqa: BLE001 - worker must not poison the pool
        task.error = f"{type(exc).__name__}: {exc}"
        logger.warn(f"Clip {task.position} failed: {task.error}")
    finally:
        if not task.config.keep_temp:
            shutil.rmtree(clip_dir, ignore_errors=True)

    # Built after the cleanup on purpose: the destination lives in output_dir,
    # not in clip_dir, so removing the work directory cannot invalidate it.
    task.record = _record(task.position, task.window, task.finish, task.rendered, task.config)
    return task


def _render_with_retry(
    *,
    media: Path,
    destination: Path,
    window,
    finish: float,
    silences: list[tuple[float, float]],
    words: list,
    config: ClipConfig,
    work: Path,
    logger: Logger,
    source_origin: float = 0.0,
    template=None,
):
    """Render once, retrying without jump cutting if the clip got too short."""
    attempts: list[ClipConfig] = []
    if config.jump_cut:
        attempts.append(config)
        attempts.append(ClipConfig(**{**config.__dict__, "jump_cut": False}))
    else:
        attempts.append(config)

    result = None
    for attempt, attempt_config in enumerate(attempts, start=1):
        result = render.render_clip(
            source=media,
            destination=destination,
            clip_start=window.start,
            clip_end=finish,
            config=attempt_config,
            ffmpeg=attempt_config.ffmpeg,
            ffprobe=attempt_config.ffprobe,
            words=words,
            silences=silences,
            work_dir=work / f"attempt{attempt}",
            logger=logger,
            source_origin=source_origin,
            template=template,
        )
        if result.duration >= config.min_duration - 0.05:
            return result
        if attempt < len(attempts):
            logger.warn(
                f"Jump cut produced {result.duration:.1f}s, below "
                f"{config.min_duration:.0f}s. Re-rendering without jump cut."
            )

    if result is None:  # pragma: no cover - defensive branch
        raise ClipperError("Rendering produced no result.")
    logger.warn(
        f"Clip is {result.duration:.1f}s long, below the requested "
        f"{config.min_duration:.0f}s minimum."
    )
    return result


def _clip_filename(position: int, window, metadata: dict, variant: str = "") -> str:
    video_id = str(metadata.get("id") or "video")
    stem = util.slugify(window.text[:60], fallback="clip", max_length=40)
    # The variant suffix goes before the timecode so all renderings of the same
    # window sort together, which is what makes a variant matrix reviewable.
    return (
        f"{video_id}_{position:02d}_{stem}{variant}_{int(window.start):06d}.mp4"
    )


def _record(
    position: int,
    window,
    finish: float,
    rendered,
    config: ClipConfig,
):
    return report.ClipRecord(
        index=position,
        start=round(window.start, 3),
        end=round(finish, 3),
        duration=round(rendered.duration, 2) if rendered else round(finish - window.start, 2),
        score=window.score,
        meets_minimum=bool(rendered and rendered.duration >= config.min_duration - 0.05),
        file=str(rendered.path) if rendered else "",
        hook_terms=window.hook_terms,
        text=window.text,
        components=window.components,
        width=rendered.width if rendered else 0,
        height=rendered.height if rendered else 0,
        # What was actually burned: the model's headline for this window when
        # there is one, otherwise the run-wide one typed by hand.
        headline=(getattr(window, "headline", "") or "") or (config.headline_text or ""),
        hashtags=getattr(window, "hashtags", "") or "",
    )