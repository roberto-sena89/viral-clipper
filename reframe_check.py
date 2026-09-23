"""End-to-end check for the face-guided reframing.

``tests/test_reframe.py`` covers the geometry and the fallbacks with a fake
detector, which is fast and deterministic but proves nothing about the parts
that actually break in production: the ffmpeg frame-extraction arguments, the
Haar cascade path inside the wheel, and whether the sampled JPEGs are readable
at all. This script exercises those against a real video built on the spot.

It needs ffmpeg and one optional detector backend (``pip install
opencv-python-headless``). Without a backend it verifies the fallback instead
of failing, because falling back is the designed behaviour.

Run it from the project root:

    python reframe_check.py
    python reframe_check.py --keep
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from viralclipper import reframe, util
from viralclipper.util import Logger

SOURCE_WIDTH = 1280
SOURCE_HEIGHT = 720
CLIP_START = 2.0
CLIP_END = 10.0


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep the temporary directory")
    return parser.parse_args()


def main() -> int:
    options = build_args()
    log = Logger()
    ffmpeg, _ffprobe = util.ffmpeg_binaries()
    tmp = Path(tempfile.mkdtemp(prefix="vc_reframe_"))

    try:
        backend = reframe.describe_backend()
        print("backend:", backend)
        detector = reframe.build_detector()
        print("detector:", detector)

        source = tmp / "clip.mp4"
        util.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"testsrc=size={SOURCE_WIDTH}x{SOURCE_HEIGHT}:rate=15:"
                f"duration={CLIP_END + 2:.0f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
                "-pix_fmt", "yuv420p", str(source),
            ],
            logger=log,
        )
        print("fixture:", source.stat().st_size, "bytes")

        # --- frame extraction -------------------------------------------------
        frame_dir = tmp / "frames"
        frames = reframe.sample_frames(
            ffmpeg, source, CLIP_START, CLIP_END, frame_dir, logger=log
        )
        print("sampled frames:", len(frames))
        assert frames, "sample_frames produced no frames"
        assert len(frames) == reframe.SAMPLE_FRAMES, (
            f"expected {reframe.SAMPLE_FRAMES} frames, got {len(frames)}"
        )
        for frame in frames:
            assert frame.stat().st_size > 0, f"{frame.name} is empty"
        print("first frame:", frames[0].name, frames[0].stat().st_size, "bytes")

        # --- detector ---------------------------------------------------------
        if detector is None:
            print("no backend installed: verifying the fallback instead")
            print("hint: pip install opencv-python-headless")
        else:
            boxes = detector.detect(frames[0])
            print("detect on frame 0:", len(boxes), "face(s)")
            for box in boxes:
                assert 0.0 <= box.center_x <= 1.0, f"center out of range: {box}"
            # A synthetic test pattern holds no face; the detector must say so
            # rather than invent one, because a false positive moves the crop.
            print("synthetic pattern reported faces:", len(boxes))

        # --- the public entry point -------------------------------------------
        center = reframe.focus_center_x(
            ffmpeg, source, CLIP_START, CLIP_END, tmp / "focus", logger=log
        )
        print("focus_center_x:", center)
        assert center is None or 0.0 <= center <= 1.0, center

        # --- crop planning ----------------------------------------------------
        centered = reframe.plan_crop_x(SOURCE_WIDTH, SOURCE_HEIGHT, 1080, 1920, None)
        left = reframe.plan_crop_x(SOURCE_WIDTH, SOURCE_HEIGHT, 1080, 1920, 0.1)
        right = reframe.plan_crop_x(SOURCE_WIDTH, SOURCE_HEIGHT, 1080, 1920, 0.9)
        print(f"crop_x: centered={centered} left={left} right={right}")
        assert left < centered < right, "the crop must follow the face horizontally"
        assert 0 <= left and right <= round(SOURCE_WIDTH * (1920 / SOURCE_HEIGHT)) - 1080

        # --- cleanup of the sampled frames ------------------------------------
        leftover = list(Path(tmp / "focus").glob("sample_*.jpg"))
        assert not leftover, f"sampled frames were left behind: {leftover}"

        print("REFRAME OK")
        return 0
    finally:
        if options.keep:
            print("temp dir kept:", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
