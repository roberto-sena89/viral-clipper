"""Face-guided horizontal crop for the vertical reframing.

A plain center crop is a coin flip. On a two-person podcast, a screen share, a
wide stage shot or an interview framed off-center, the middle of the frame is
exactly where the subject is *not*, and the clip is ruined before the captions
even matter.

This module samples a handful of frames from the clip, runs a face detector
over them and returns **one stable horizontal position** for the crop, derived
from the median of the detected face centers.

A single static offset, rather than per-frame tracking, is deliberate:

* it cannot jitter, which is the failure mode that makes auto-reframed clips
  look worse than a fixed crop;
* it costs one cheap pass over a few downscaled frames;
* a short clip usually holds one framing anyway, and the viewer's eye tracks
  the caption, not the pixel grid.

The detector is optional and pluggable. When no backend is installed the
module returns ``None`` and the renderer falls back to a plain center crop, so
the feature degrades instead of failing.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import util
from .util import Logger

# Sample enough points to handle brief camera moves and shot changes while
# keeping face analysis small beside the video encode.
SAMPLE_FRAMES = 13
# Wider samples retain enough detail for faces that are small in a landscape
# source; the original 360 px pass missed some distant speakers.
SAMPLE_WIDTH = 640
# The clip only gets a guided crop when at least this many sampled frames
# agree. Haar cascades can fire on textures, so a single hit is treated as noise.
MIN_HITS = 4
MIN_CONFIDENCE = 0.5
# How far the crop may be pushed from the geometric center, as a fraction of
# the total horizontal slack. Keeps one bad detection from throwing the subject
# out of frame entirely.
MAX_SHIFT_FRACTION = 0.85


@dataclass(frozen=True)
class FaceBox:
    """A detected face in normalized coordinates (0..1, origin top-left)."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0


class FaceDetector(Protocol):
    """Minimal detector interface, so tests and other backends can plug in."""

    name: str

    def detect(self, image_path: Path) -> list[FaceBox]:
        """Return the faces found in one image file."""
        ...


CASCADE_FILE = "haarcascade_frontalface_default.xml"


def opencv_cascade_path() -> Path | None:
    """Path to OpenCV's bundled frontal-face cascade, or ``None``.

    OpenCV 5 removed ``CascadeClassifier`` and stopped shipping the Haar XMLs,
    leaving only ``FaceDetectorYN``, which needs an ONNX model fetched at
    runtime. That would break the "no model download" property this backend
    exists for, so the dependency is pinned to the 4.x line and a 5.x install is
    reported explicitly rather than silently degrading to a center crop.
    """
    if not util.module_available("cv2"):
        return None
    try:
        import cv2  # noqa: PLC0415 - optional dependency
    except Exception:  # noqa: BLE001 - a broken install is "unavailable"
        return None
    if not hasattr(cv2, "CascadeClassifier"):
        return None
    data = getattr(getattr(cv2, "data", None), "haarcascades", None)
    if not data:
        return None
    candidate = Path(data) / CASCADE_FILE
    return candidate if candidate.exists() else None


class OpenCvDetector:
    """Multi-view OpenCV Haar detector for frontal and profile faces.

    OpenCV 4.x bundles these cascades, so focus reframing needs no model
    download or network access. Profile detection runs on both the original and
    mirrored image so people looking either direction can guide the crop.
    """

    name = "opencv"

    def __init__(self, min_confidence: float = MIN_CONFIDENCE) -> None:
        self._min_confidence = min_confidence
        self._cascade = None
        self._extra_cascades: list[tuple[str, object]] = []

    def _load(self):
        if self._cascade is None:
            import cv2  # noqa: PLC0415

            path = opencv_cascade_path()
            if path is None:
                raise util.ClipperError(
                    "OpenCV is installed but ships no Haar cascade "
                    "(opencv-python 5 removed CascadeClassifier). Install the "
                    "4.x line: pip install \"opencv-python-headless<5\""
                )
            cascade = cv2.CascadeClassifier(str(path))
            if cascade.empty():
                raise util.ClipperError(f"Could not load the Haar cascade at {path}")
            self._cascade = cascade

            cascade_dir = path.parent
            for filename in (
                "haarcascade_frontalface_alt2.xml",
                "haarcascade_profileface.xml",
            ):
                candidate = cascade_dir / filename
                if not candidate.is_file():
                    continue
                try:
                    alternate = cv2.CascadeClassifier(str(candidate))
                    if not alternate.empty():
                        self._extra_cascades.append((filename, alternate))
                except Exception:  # noqa: BLE001 - optional cascades must not disable focus
                    continue
        return self._cascade

    def detect(self, image_path: Path) -> list[FaceBox]:
        import cv2  # noqa: PLC0415

        self._load()
        image = cv2.imread(str(image_path))
        if image is None:
            return []
        height, width = image.shape[:2]
        if not height or not width:
            return []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        min_size = (max(18, int(width * 0.04)), max(18, int(height * 0.04)))
        neighbors = max(3, int(round(3 + 3 * self._min_confidence)))
        cascades = [("frontal", self._cascade), *self._extra_cascades]
        faces: list[FaceBox] = []

        for kind, cascade in cascades:
            views = [(gray, False)]
            if "profileface" in kind:
                views.append((cv2.flip(gray, 1), True))
            for view, mirrored in views:
                found = cascade.detectMultiScale(
                    view,
                    scaleFactor=1.05,
                    minNeighbors=neighbors,
                    minSize=min_size,
                )
                for x, y, box_width, box_height in found:
                    if mirrored:
                        x = width - x - box_width
                    faces.append(FaceBox(
                        x=x / width,
                        y=y / height,
                        width=box_width / width,
                        height=box_height / height,
                    ))
        return faces


# Each backend declares how to probe for it *and* how to build it, because
# "the module imports" is not the same as "the feature works".
_BACKENDS: dict[str, tuple[Callable[[], bool], type]] = {
    "opencv": (lambda: opencv_cascade_path() is not None, OpenCvDetector),
}


def available_backend() -> str | None:
    """Return the name of the first usable detector backend, or ``None``."""
    for name, (probe, _factory) in _BACKENDS.items():
        if probe():
            return name
    return None


def build_detector(name: str | None = None) -> FaceDetector | None:
    """Instantiate the requested detector, or the first available one."""
    if name in {None, "", "auto"}:
        name = available_backend()
    if not name:
        return None
    entry = _BACKENDS.get(name)
    if entry is None:
        raise util.ClipperError(
            f"Unknown face detector '{name}'. Available: {', '.join(_BACKENDS)}"
        )
    _probe, factory = entry
    try:
        return factory()  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 - an optional backend must never be fatal
        raise util.ClipperError(f"Could not start the '{name}' detector: {exc}") from exc


def median_center_x(detections: list[list[FaceBox]], *, min_hits: int = MIN_HITS) -> float | None:
    """Median horizontal face center across sampled frames.

    ``detections`` holds one list per sampled frame. Frames with no face are
    ignored, and fewer than ``min_hits`` non-empty frames means "no opinion"
    rather than a guess.
    """
    usable = [frame for frame in detections if frame]
    if len(usable) < min_hits:
        return None
    centers: list[float] = []
    for frame in usable:
        # With several faces in one frame, the largest is the subject: it is
        # the one closest to camera and the one a crop should follow.
        biggest = max(frame, key=lambda box: box.width * box.height)
        centers.append(biggest.center_x)
    return float(statistics.median(centers))


def plan_crop_x(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    center_x: float | None,
) -> int:
    """Horizontal crop offset that puts ``center_x`` in the middle of the frame.

    Mirrors the geometry of ``scale=W:H:force_original_aspect_ratio=increase``
    followed by ``crop=W:H:X:0``, so the returned value can be handed straight
    to ffmpeg. ``center_x`` is normalized; ``None`` yields the centered crop.
    """
    if source_width <= 0 or source_height <= 0 or target_width <= 0 or target_height <= 0:
        return 0

    ratio = max(target_width / source_width, target_height / source_height)
    scaled_width = round(source_width * ratio)
    slack = scaled_width - target_width
    if slack <= 0:
        # The source is already narrower than the target: nothing to shift.
        return 0

    if center_x is None:
        return slack // 2

    desired = center_x * scaled_width - target_width / 2.0
    # Clamp into the frame, then limit how far the crop may travel from the
    # center so one stray detection cannot frame the wall.
    low = (1.0 - MAX_SHIFT_FRACTION) / 2.0 * slack
    high = slack - low
    return int(round(min(max(desired, low), high)))


def sample_frames(
    ffmpeg: str,
    source: str | Path,
    start: float,
    end: float,
    work_dir: str | Path,
    *,
    count: int = SAMPLE_FRAMES,
    logger: Logger | None = None,
) -> list[Path]:
    """Extract ``count`` evenly spaced downscaled frames from ``[start, end]``."""
    duration = max(0.0, end - start)
    if duration <= 0 or count <= 0:
        return []

    work = util.ensure_dir(work_dir)
    for stale in work.glob("sample_*.jpg"):
        stale.unlink(missing_ok=True)

    # Spread the samples over the middle 80% of the clip: the first and last
    # fractions often hold the transition in and out of the shot.
    span = duration * 0.8
    offset = start + duration * 0.1
    fps = max(0.01, count / span)

    pattern = work / "sample_%03d.jpg"
    util.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            util.fmt_clock_ms(offset),
            "-i",
            str(Path(source).resolve()),
            "-t",
            f"{span:.3f}",
            "-vf",
            f"fps={fps:.6f},scale={SAMPLE_WIDTH}:-2",
            "-frames:v",
            str(count),
            "-q:v",
            "4",
            str(pattern),
        ],
        logger=logger,
        check=False,
    )
    return sorted(work.glob("sample_*.jpg"))


def _cleanup_samples(work_dir: str | Path, logger: Logger | None = None) -> None:
    """Remove the sampled frames, never letting a failure escape.

    These files are scratch. A frame that cannot be deleted is not a reason to
    lose a clip, and an exception raised from a ``finally`` block would replace
    whatever the body was doing - so every error is reported and swallowed.
    """
    for frame in Path(work_dir).glob("sample_*.jpg"):
        try:
            frame.unlink(missing_ok=True)
        except OSError as exc:  # noqa: PERF203 - one retry path per file is fine
            if logger:
                logger.debug(f"Could not remove {frame.name}: {exc}")


def focus_center_x(
    ffmpeg: str,
    source: str | Path,
    start: float,
    end: float,
    work_dir: str | Path,
    *,
    detector: FaceDetector | None = None,
    logger: Logger | None = None,
) -> float | None:
    """Normalized horizontal center to crop around, or ``None`` for centered.

    Never raises on detector trouble: a missing backend, a broken cascade or a
    failed frame extraction all mean "fall back to the geometric center".
    """
    if detector is None:
        detector = build_detector()
    if detector is None:
        if logger:
            logger.warn(
                "No face detector is available; using a center crop. "
                "Install opencv-python-headless<5 to enable face-guided focus."
            )
        return None

    try:
        frames = sample_frames(ffmpeg, source, start, end, work_dir, logger=logger)
        if not frames:
            if logger:
                logger.warn("Could not extract frames for face detection; using a center crop")
            return None
        detections = [detector.detect(frame) for frame in frames]
        center = median_center_x(detections)
    except Exception as exc:  # noqa: BLE001 - reframing is best effort
        if logger:
            logger.warn(f"Face detection failed ({type(exc).__name__}: {exc}); using center crop")
        return None
    finally:
        _cleanup_samples(work_dir, logger)

    if logger:
        # Reporting the hit count makes the confidence of the chosen crop
        # visible in the render log instead of hiding a weak detection behind a
        # plausible-looking center coordinate.
        hits = sum(1 for faces in detections if faces)
        if center is None:
            logger.warn(
                f"No consistent face found in {hits}/{len(frames)} sampled frames; "
                "using the center crop"
            )
        else:
            logger.info(
                f"Face-guided crop centered at {center * 100:.0f}% of the frame width "
                f"(faces in {hits}/{len(frames)} sampled frames)"
            )
    return center


def describe_backend() -> str:
    """Human readable backend status, used by the CLI banner."""
    name = available_backend()
    if name is not None:
        return name
    if util.module_available("cv2"):
        return "opencv instalado, mas sem cascade utilizavel (crop central)"
    return "nenhum (crop central)"


__all__ = [
    "FaceBox",
    "FaceDetector",
    "OpenCvDetector",
    "available_backend",
    "build_detector",
    "describe_backend",
    "focus_center_x",
    "median_center_x",
    "opencv_cascade_path",
    "plan_crop_x",
    "sample_frames",
]
