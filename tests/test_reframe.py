"""Unit tests for :mod:`viralclipper.reframe`.

The crop geometry is pure arithmetic and fully testable; the detector is
injected, so these tests never need OpenCV, ffmpeg or a real face.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from viralclipper import reframe
from viralclipper.reframe import FaceBox
from viralclipper.util import ClipperError


def _box(center_x: float, size: float = 0.1) -> FaceBox:
    return FaceBox(x=center_x - size / 2.0, y=0.2, width=size, height=size)


class FakeDetector:
    """Detector whose answers are dictated by the test."""

    name = "fake"

    def __init__(self, answers):
        self.answers = answers
        self.calls = 0

    def detect(self, image_path: Path) -> list[FaceBox]:
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        if isinstance(answer, Exception):
            raise answer
        return answer


class ExplodingDetector:
    name = "boom"

    def detect(self, image_path: Path) -> list[FaceBox]:
        raise RuntimeError("detector exploded")


class PlanCropXTests(unittest.TestCase):
    """1920x1080 into 1080x1920 scales to 3413x1920, leaving 2333px of slack."""

    def test_centered_when_no_face_position(self):
        self.assertEqual(reframe.plan_crop_x(1920, 1080, 1080, 1920, None), 1166)

    def test_face_at_center_matches_the_geometric_center(self):
        self.assertEqual(
            reframe.plan_crop_x(1920, 1080, 1080, 1920, 0.5),
            reframe.plan_crop_x(1920, 1080, 1080, 1920, None),
        )

    def test_face_on_the_left_shifts_the_crop_left(self):
        left = reframe.plan_crop_x(1920, 1080, 1080, 1920, 0.2)
        center = reframe.plan_crop_x(1920, 1080, 1080, 1920, 0.5)
        self.assertLess(left, center)

    def test_face_on_the_right_shifts_the_crop_right(self):
        right = reframe.plan_crop_x(1920, 1080, 1080, 1920, 0.8)
        center = reframe.plan_crop_x(1920, 1080, 1080, 1920, 0.5)
        self.assertGreater(right, center)

    def test_extreme_left_is_clamped_into_the_frame(self):
        self.assertEqual(reframe.plan_crop_x(1920, 1080, 1080, 1920, 0.0), 175)

    def test_extreme_right_is_clamped_into_the_frame(self):
        self.assertEqual(reframe.plan_crop_x(1920, 1080, 1080, 1920, 1.0), 2158)

    def test_clamped_result_never_leaves_the_frame(self):
        for position in (0.0, 0.05, 0.5, 0.95, 1.0):
            offset = reframe.plan_crop_x(1920, 1080, 1080, 1920, position)
            self.assertGreaterEqual(offset, 0)
            self.assertLessEqual(offset, 3413 - 1080)

    def test_shift_is_limited_so_one_bad_detection_cannot_frame_the_wall(self):
        slack = 3413 - 1080
        limit = slack - int(round((1.0 - reframe.MAX_SHIFT_FRACTION) / 2.0 * slack))
        self.assertLessEqual(reframe.plan_crop_x(1920, 1080, 1080, 1920, 1.0), limit + 1)

    def test_no_slack_means_no_offset(self):
        self.assertEqual(reframe.plan_crop_x(1080, 1920, 1080, 1920, 0.9), 0)

    def test_source_already_narrower_than_target(self):
        self.assertEqual(reframe.plan_crop_x(720, 1280, 1080, 1920, 0.1), 0)

    def test_degenerate_dimensions_return_zero(self):
        self.assertEqual(reframe.plan_crop_x(0, 0, 1080, 1920, 0.5), 0)
        self.assertEqual(reframe.plan_crop_x(1920, 1080, 0, 0, 0.5), 0)


class MedianCenterXTests(unittest.TestCase):
    def test_fewer_hits_than_required_is_no_opinion(self):
        frames = [[_box(0.3)], [], [_box(0.4)]]
        self.assertIsNone(reframe.median_center_x(frames, min_hits=3))

    def test_the_default_gate_is_min_hits(self):
        """Sem `min_hits`, o portao e' `MIN_HITS` -- e nao zero.

        Os testes desta classe passam `min_hits` de proposito, para medir a
        mediana com poucas amostras. Isso deixava o valor PADRAO sem cobertura
        nenhuma -- e foi assim que os dois testes de `focus_center_x`
        apodreceram: mediam a mediana de TRES amostras por um caminho onde o
        portao ja' era quatro.
        """
        tres = [[_box(0.3)], [_box(0.4)], [_box(0.5)]]
        self.assertIsNone(reframe.median_center_x(tres))
        self.assertAlmostEqual(
            reframe.median_center_x([*tres, [_box(0.4)]]), 0.4, places=6
        )

    def test_enough_hits_produce_a_median(self):
        frames = [[_box(0.2)], [_box(0.4)], [_box(0.6)], [_box(0.8)]]
        self.assertAlmostEqual(reframe.median_center_x(frames, min_hits=3), 0.5, places=6)

    def test_empty_frames_are_ignored(self):
        frames = [[_box(0.2)], [], [], [_box(0.2)], [_box(0.2)], []]
        self.assertAlmostEqual(reframe.median_center_x(frames, min_hits=3), 0.2, places=6)

    def test_largest_face_wins_when_several_are_present(self):
        frame = [_box(0.1, size=0.05), _box(0.9, size=0.30)]
        frames = [frame, frame, frame]
        self.assertAlmostEqual(reframe.median_center_x(frames, min_hits=3), 0.9, places=6)

    def test_no_frames_at_all(self):
        self.assertIsNone(reframe.median_center_x([], min_hits=1))


class BuildDetectorTests(unittest.TestCase):
    def test_unknown_backend_raises(self):
        with self.assertRaises(ClipperError):
            reframe.build_detector("does-not-exist")

    def test_no_backend_installed_returns_none(self):
        with patch.object(reframe, "available_backend", return_value=None):
            self.assertIsNone(reframe.build_detector())

    def test_auto_picks_an_available_backend(self):
        with patch.object(reframe, "available_backend", return_value=None):
            self.assertIsNone(reframe.build_detector("auto"))
            self.assertIsNone(reframe.build_detector(""))

    def test_describe_backend_reports_the_fallback(self):
        with patch.object(reframe, "available_backend", return_value=None):
            self.assertIn("crop central", reframe.describe_backend())

    def test_describe_backend_names_the_backend(self):
        with patch.object(reframe, "available_backend", return_value="opencv"):
            self.assertEqual(reframe.describe_backend(), "opencv")


class FocusCenterXTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reframe-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run(self, detector, frames=None):
        if frames is None:
            # CINCO amostras, nao tres: `median_center_x` so' opina a partir de
            # `MIN_HITS` (4) acertos, entao um teste com tres amostras mede o
            # portao -- e falha -- em vez de medir a mediana que ele promete.
            frames = [self.tmp / f"sample_{index:03d}.jpg" for index in range(1, 6)]
        for frame in frames:
            frame.write_bytes(b"fake")
        with patch.object(reframe, "sample_frames", return_value=frames):
            return reframe.focus_center_x(
                "ffmpeg", "src.mp4", 10.0, 20.0, self.tmp, detector=detector
            )

    def test_returns_the_median_center(self):
        # A ordem e' de proposito: 0.3 e 0.6 sao as pontas e o 0.4 aparece duas
        # vezes, entao a mediana (0.4) nao coincide com a media (0.44) nem com o
        # valor do meio de uma lista ja' ordenada. Uma media passaria neste
        # teste se as amostras estivessem em ordem.
        detector = FakeDetector([[_box(0.6)], [_box(0.4)], [_box(0.3)],
                                 [_box(0.4)], [_box(0.5)]])
        self.assertAlmostEqual(self._run(detector), 0.4, places=6)

    def test_a_single_hit_is_not_enough_for_a_guided_crop(self):
        """Dois acertos em cinco nao guiam o corte.

        E' o portao do `MIN_HITS` visto de fora, pelo caminho que o render usa:
        um detector que dispara em duas texturas nao pode decidir o
        enquadramento. Antes desta trava o corte seguia o ruido.
        """
        detector = FakeDetector([[_box(0.1)], [_box(0.9)], [], [], []])
        self.assertIsNone(self._run(detector))

    def test_returns_none_when_no_face_is_found(self):
        detector = FakeDetector([[], [], [], [], []])
        self.assertIsNone(self._run(detector))

    def test_returns_none_when_frames_cannot_be_extracted(self):
        detector = FakeDetector([[_box(0.5)]])
        with patch.object(reframe, "sample_frames", return_value=[]):
            self.assertIsNone(
                reframe.focus_center_x(
                    "ffmpeg", "src.mp4", 0.0, 10.0, self.tmp, detector=detector
                )
            )

    def test_detector_failure_falls_back_instead_of_raising(self):
        self.assertIsNone(self._run(ExplodingDetector()))

    def test_no_detector_available_returns_none(self):
        with patch.object(reframe, "build_detector", return_value=None):
            self.assertIsNone(
                reframe.focus_center_x("ffmpeg", "src.mp4", 0.0, 10.0, self.tmp)
            )

    def test_sampled_frames_are_cleaned_up(self):
        frames = [self.tmp / f"sample_{index:03d}.jpg" for index in range(3)]
        detector = FakeDetector([[_box(0.5)], [_box(0.5)], [_box(0.5)]])
        self._run(detector, frames=frames)
        self.assertEqual(list(self.tmp.glob("sample_*.jpg")), [])


class SampleFramesTests(unittest.TestCase):
    def test_degenerate_ranges_produce_no_frames(self):
        self.assertEqual(
            reframe.sample_frames("ffmpeg", "src.mp4", 10.0, 10.0, "work"), []
        )
        self.assertEqual(
            reframe.sample_frames("ffmpeg", "src.mp4", 20.0, 10.0, "work"), []
        )
        self.assertEqual(
            reframe.sample_frames("ffmpeg", "src.mp4", 0.0, 10.0, "work", count=0), []
        )


class OpenCvCascadeProbeTests(unittest.TestCase):
    """``cv2`` importing is not the same as the backend being usable.

    OpenCV 5 removed ``CascadeClassifier`` and stopped shipping the Haar XMLs,
    so a probe based on ``find_spec('cv2')`` would advertise a backend that
    fails on first use.
    """

    def test_missing_cv2_is_unavailable(self):
        with patch.object(reframe.util, "module_available", return_value=False):
            self.assertIsNone(reframe.opencv_cascade_path())

    def test_cv2_without_cascade_classifier_is_unavailable(self):
        import types

        fake = types.SimpleNamespace(data=types.SimpleNamespace(haarcascades="nowhere"))
        with patch.object(reframe.util, "module_available", return_value=True), patch.dict(
            "sys.modules", {"cv2": fake}
        ):
            self.assertIsNone(reframe.opencv_cascade_path())

    def test_cascade_classifier_without_the_xml_is_unavailable(self):
        import types

        fake = types.SimpleNamespace(
            CascadeClassifier=object,
            data=types.SimpleNamespace(haarcascades=str(Path(tempfile.gettempdir()))),
        )
        with patch.object(reframe.util, "module_available", return_value=True), patch.dict(
            "sys.modules", {"cv2": fake}
        ):
            self.assertIsNone(reframe.opencv_cascade_path())

    def test_available_backend_uses_the_probe_not_the_import(self):
        with patch.object(reframe, "_BACKENDS", {"opencv": (lambda: False, object)}):
            self.assertIsNone(reframe.available_backend())

    def test_describe_backend_reports_a_broken_install(self):
        with patch.object(reframe, "available_backend", return_value=None), patch.object(
            reframe.util, "module_available", return_value=True
        ):
            self.assertIn("sem cascade utilizavel", reframe.describe_backend())

    def test_broken_backend_degrades_to_the_center_crop(self):
        with patch.object(
            reframe,
            "_BACKENDS",
            {"opencv": (lambda: True, lambda: (_ for _ in ()).throw(RuntimeError("no cascade")))},
        ):
            with self.assertRaises(ClipperError):
                reframe.build_detector("opencv")


class RealBackendTests(unittest.TestCase):
    """Runs only when OpenCV 4.x is actually installed."""

    def setUp(self):
        if reframe.opencv_cascade_path() is None:
            self.skipTest("no usable OpenCV cascade installed")

    def test_detector_loads_the_bundled_cascade(self):
        detector = reframe.build_detector("opencv")
        self.assertIsNotNone(detector)
        self.assertIsNotNone(detector._load())

    def test_a_blank_image_reports_no_face(self):
        import cv2
        import numpy as np

        tmp = Path(tempfile.mkdtemp(prefix="reframe-blank-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        image = tmp / "blank.jpg"
        cv2.imwrite(str(image), np.full((240, 320, 3), 128, dtype=np.uint8))

        detector = reframe.build_detector("opencv")
        self.assertEqual(detector.detect(image), [])

    def test_unreadable_image_reports_no_face(self):
        tmp = Path(tempfile.mkdtemp(prefix="reframe-bad-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        broken = tmp / "broken.jpg"
        broken.write_bytes(b"not an image")

        detector = reframe.build_detector("opencv")
        self.assertEqual(detector.detect(broken), [])


class CleanupSamplesTests(unittest.TestCase):
    """A scratch frame that cannot be deleted must never fail the clip.

    The removal runs from a ``finally`` block, so an escaping ``OSError`` would
    replace whatever the body was doing and turn a best-effort reframe into a
    failed clip. It happened for real: a delete that the OS refused took clip 2
    down with it.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vc-reframe-cleanup-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.frame = self.tmp / "sample_001.jpg"
        self.frame.write_bytes(b"jpeg")

    def test_removes_the_sampled_frames(self):
        reframe._cleanup_samples(self.tmp)
        self.assertFalse(self.frame.exists())

    def test_an_undeletable_frame_does_not_raise(self):
        with patch.object(Path, "unlink", side_effect=OSError("trash-failed")):
            reframe._cleanup_samples(self.tmp)
        self.assertTrue(self.frame.exists())

    def test_a_missing_directory_does_not_raise(self):
        reframe._cleanup_samples(self.tmp / "does-not-exist")

    def test_focus_center_x_survives_a_cleanup_failure(self):
        """End to end: the crop is still planned when the frames cannot go.

        Cinco amostras, nao tres, porque `median_center_x` so' opina a partir de
        `MIN_HITS` (4) -- com tres o centro volta `None` e o teste mediria o
        portao, nao a sobrevivencia a falha de limpeza que ele promete.
        """
        frames = [self.tmp / f"sample_{index:03d}.jpg" for index in range(5)]
        for frame in frames:
            frame.write_bytes(b"fake")
        detector = FakeDetector([[_box(0.3)], [_box(0.3)], [_box(0.3)],
                                 [_box(0.3)], [_box(0.3)]])
        with patch.object(reframe, "sample_frames", return_value=frames), patch.object(
            Path, "unlink", side_effect=OSError("trash-failed")
        ):
            center = reframe.focus_center_x(
                "ffmpeg", "clip.mp4", 0.0, 10.0, self.tmp, detector=detector
            )
        self.assertAlmostEqual(center, 0.3, places=6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
