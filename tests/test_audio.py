"""Unit tests for :mod:`viralclipper.audio`.

These three functions are the ENTRANCE of the score's audio signals: the dB
curve feeds ``loudness``/``density``, the adaptive threshold decides what counts
as silence, and the silence ranges feed the jump cut. A test that only asserts
"it returned an array" leaves the whole scoring input as a blind spot -- and a
sign that is silently wrong scores every clip wrong without failing anything.

So every assertion below pins an ABSOLUTE number. The expected values were
derived from the documented formula by hand (``10*log10(power)`` with a Hann
window, percentiles 10/90 for the threshold, hop arithmetic for the silences),
never copied from a previous run of the code -- a copied number only proves the
code still does what it did, including its bugs.

No ffmpeg and no real audio: the curve is built from synthetic arrays and the
WAV files are written by the test itself.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from tests._fixtures import make_analysis
from viralclipper import audio
from viralclipper.audio import AudioAnalysis
from viralclipper.util import ClipperError

RATE = 16_000
HOP_MS = 20
# frame_db turns hop_ms into samples: hop = 16000*20/1000 = 320, and the window
# is twice that (640). Both are pinned here because every expected dB depends
# on them.
HOP = 320
WIN = 640


def _hann_ratio() -> float:
    """``sum(w**2) / sum(w)`` for the window ``frame_db`` actually uses.

    For a constant signal of amplitude ``c`` every fully covered frame gets
    ``power = c**2 * sum(w**2) / sum(w)`` -- the dot product of the squared
    chunk with the squared window, normalised by ``sum(w)``. That ratio is
    0.75 for a Hann window, and it is the only place the window shape enters
    the result.
    """
    window = np.hanning(WIN)
    return float((window * window).sum() / window.sum())


class FrameDbTests(unittest.TestCase):
    """The loudness curve: one value per hop, in dBFS."""

    def test_one_value_per_hop(self):
        # 32000 samples / 320 per hop = 100 hops, exactly.
        self.assertEqual(len(audio.frame_db(np.zeros(32_000, np.float32), RATE)), 100)

    def test_digital_silence_is_exactly_minus_120_db(self):
        """Zero power is floored at 1e-12 before the log: 10*log10(1e-12) = -120.

        Not -80 (``SILENCE_FLOOR``, which is only the "no samples at all" and
        threshold-clamp value). Pinning -120 is what proves the floor is applied
        in linear power and not in dB.
        """
        db = audio.frame_db(np.zeros(32_000, np.float32), RATE)
        self.assertAlmostEqual(float(db.max()), -120.0, places=4)
        self.assertAlmostEqual(float(db.min()), -120.0, places=4)

    def test_no_samples_at_all_reports_the_floor(self):
        db = audio.frame_db(np.array([], dtype=np.float32), RATE)
        self.assertEqual(len(db), 1)
        self.assertAlmostEqual(float(db[0]), audio.SILENCE_FLOOR, places=4)

    def test_the_curve_matches_the_documented_hann_formula(self):
        """A constant amplitude must land exactly where the formula says.

        Expected value, by hand: ``10*log10(1.0**2 * 0.75)`` = **-1.2494 dB**.
        The last frame is excluded: it is the only one the zero padding reaches.
        """
        db = audio.frame_db(np.ones(32_000, np.float32), RATE)
        expected = 10.0 * math.log10(1.0 * _hann_ratio())
        self.assertAlmostEqual(expected, -1.2493873660829988, places=9)
        for index, value in enumerate(db[:-1]):
            with self.subTest(frame=index):
                self.assertAlmostEqual(float(value), expected, places=3)

    def test_halving_the_amplitude_costs_six_db(self):
        """Power scales with the square, so half the amplitude is -6.0206 dB.

        This is the property the ``loudness`` signal depends on: if the curve
        were linear instead of logarithmic, two clips with a 4x gain difference
        would score almost the same.
        """
        loud = audio.frame_db(np.ones(32_000, np.float32), RATE)
        quiet = audio.frame_db(np.full(32_000, 0.5, np.float32), RATE)
        delta = float(loud[:-1].mean()) - float(quiet[:-1].mean())
        self.assertAlmostEqual(delta, 6.020599913279624, places=3)
        # E a metade e' de fato mais baixa -- o sinal do delta importa.
        self.assertLess(float(quiet[:-1].mean()), float(loud[:-1].mean()))

    def test_the_last_frame_is_dragged_down_by_the_zero_padding(self):
        """Only the final frame mixes signal with the padding zeros.

        Pinning this keeps the tail of the curve honest: if the padding were
        dropped (or the window not padded at all), the last hop would report
        full amplitude for a signal that ended in silence.
        """
        db = audio.frame_db(np.ones(32_000, np.float32), RATE)
        self.assertLess(float(db[-1]), float(db[-2]))

    def test_a_short_signal_still_yields_one_frame(self):
        # 10 samples < one hop: ceil(10/320) = 1 frame, not zero.
        db = audio.frame_db(np.ones(10, np.float32), RATE)
        self.assertEqual(len(db), 1)

    def test_the_hop_size_follows_the_sample_rate(self):
        # hop_ms=40 at 16 kHz -> hop = 640 samples; 32000/640 = 50 frames.
        self.assertEqual(len(audio.frame_db(np.zeros(32_000, np.float32), RATE, 40)), 50)


class AdaptiveThresholdTests(unittest.TestCase):
    """The threshold derived from the recording itself: (threshold, floor, speech)."""

    def test_an_empty_curve_is_all_floor(self):
        self.assertEqual(
            audio.adaptive_threshold(np.array([], dtype=np.float32)),
            (audio.SILENCE_FLOOR, audio.SILENCE_FLOOR, audio.SILENCE_FLOOR),
        )

    def test_a_wide_range_gives_the_hand_computed_threshold(self):
        """Five values, computed by hand.

        percentile(10) of [-60,-50,-40,-30,-20] = **-56.0**;
        percentile(90) = **-24.0**; span = 32; threshold = -56 + 0.30*32 = **-46.4**.
        """
        db = np.array([-60.0, -50.0, -40.0, -30.0, -20.0], dtype=np.float32)
        threshold, noise_floor, speech = audio.adaptive_threshold(db)
        self.assertAlmostEqual(noise_floor, -56.0, places=4)
        self.assertAlmostEqual(speech, -24.0, places=4)
        self.assertAlmostEqual(threshold, -46.4, places=4)

    def test_a_flat_curve_cannot_collapse_the_span(self):
        """A perfectly flat curve has span 0, and ``max(1.0, span)`` saves it.

        Without that guard the threshold would equal the level itself and every
        hop would be classified as silence. Expected: -40 + 0.3*1.0 = -39.7,
        then clamped to ``speech - 3`` = **-43.0**.
        """
        db = np.full(50, -40.0, dtype=np.float32)
        threshold, noise_floor, speech = audio.adaptive_threshold(db)
        self.assertAlmostEqual(noise_floor, -40.0, places=4)
        self.assertAlmostEqual(speech, -40.0, places=4)
        self.assertAlmostEqual(threshold, -43.0, places=4)

    def test_a_narrow_range_is_clamped_below_the_speech_level(self):
        """With a 0.32 dB range the raw threshold would sit ABOVE the speech.

        Raw: -29.96 + 0.30*max(1.0, 0.32) = -29.66, which is louder than
        ``speech - 3`` = **-32.64**. The upper clamp is what keeps the threshold
        under the speech level, otherwise a quiet passage would be called loud.
        """
        db = np.array([-30.0, -29.9, -29.8, -29.7, -29.6], dtype=np.float32)
        threshold, _noise_floor, speech = audio.adaptive_threshold(db)
        self.assertAlmostEqual(speech, -29.64, places=4)
        self.assertAlmostEqual(threshold, -32.64, places=4)
        self.assertLess(threshold, speech)

    def test_a_very_quiet_curve_ends_up_at_the_floor(self):
        """When both clamps fight, the upper one wins.

        Raw threshold = -93.0 + 0.30*16 = -88.2. The lower clamp lifts it to
        ``SILENCE_FLOOR + 5`` = -75.0, but the upper clamp then pulls it back to
        ``speech - 3`` = -80.0 -- which is exactly ``SILENCE_FLOOR``.
        """
        db = np.array([-95.0, -90.0, -85.0, -80.0, -75.0], dtype=np.float32)
        threshold, _noise_floor, _speech = audio.adaptive_threshold(db)
        self.assertAlmostEqual(threshold, -80.0, places=4)


class DetectSilencesTests(unittest.TestCase):
    """Silence ranges, in seconds, from a hop-indexed mask."""

    def test_an_empty_curve_has_no_silence(self):
        self.assertEqual(audio.detect_silences(np.array([], dtype=np.float32), 20, -40.0, 0.3), [])

    def test_a_middle_run_is_reported_with_hand_computed_bounds(self):
        """hop_ms=100 -> 0.1 s per hop; min_silence=0.3 -> 3 hops minimum.

        The quiet run is hops 2..6 (five hops), so the range is
        (2*0.1, 7*0.1) = **(0.2, 0.7)**.
        """
        db = np.array([-10.0] * 2 + [-60.0] * 5 + [-10.0] * 3, dtype=np.float32)
        self.assertEqual(
            audio.detect_silences(db, 100, -40.0, 0.3), [(0.2, 0.7)]
        )

    def test_a_trailing_run_closes_at_the_end_of_the_file(self):
        """The last run never sees a loud hop, so it is closed by the tail branch."""
        db = np.array([-10.0] * 5 + [-60.0] * 5, dtype=np.float32)
        self.assertEqual(
            audio.detect_silences(db, 100, -40.0, 0.3), [(0.5, 1.0)]
        )

    def test_a_run_shorter_than_min_silence_is_dropped(self):
        db = np.array([-10.0] * 5 + [-60.0] * 2 + [-10.0] * 3, dtype=np.float32)
        self.assertEqual(audio.detect_silences(db, 100, -40.0, 0.3), [])

    def test_a_run_of_exactly_min_silence_is_kept(self):
        """The comparison is ``>=``: three hops with min_silence 0.3 is a silence."""
        db = np.array([-10.0] * 2 + [-60.0] * 3 + [-10.0] * 2, dtype=np.float32)
        self.assertEqual(
            audio.detect_silences(db, 100, -40.0, 0.3), [(0.2, 0.5)]
        )

    def test_the_threshold_is_exclusive(self):
        """A hop exactly AT the threshold is loud, not silent (``db < threshold``).

        With ``<`` swapped for ``<=`` every borderline hop would be cut, and the
        jump cut would shave the first and last hop of every clip.
        """
        db = np.full(20, -40.0, dtype=np.float32)
        self.assertEqual(audio.detect_silences(db, 100, -40.0, 0.3), [])

    def test_the_default_hop_needs_sixteen_quiet_hops(self):
        """The production pair (hop 20 ms, min_silence 0.32 s) is 16 hops.

        ``round(0.32 / 0.02)`` is 16 -- pinned here because the divisor is a
        float division and a change in ``hop_ms`` silently rescales every cut
        point without touching this constant.
        """
        short = np.array([-10.0] * 5 + [-60.0] * 15 + [-10.0], dtype=np.float32)
        self.assertEqual(audio.detect_silences(short, 20, -40.0, 0.32), [])

        exact = np.array([-10.0] * 5 + [-60.0] * 16 + [-10.0], dtype=np.float32)
        # 16 hops de 20 ms a partir do indice 5: (5*0.02, 21*0.02) = (0.1, 0.42).
        self.assertEqual(
            audio.detect_silences(exact, 20, -40.0, 0.32), [(0.1, 0.42)]
        )


class QuietSegmentsTests(unittest.TestCase):
    """The fallback that keeps a clip from being cut into nothing."""

    def test_falls_back_to_the_whole_file_when_nothing_is_quiet(self):
        """A flat, loud curve has no silence -- the whole range is returned.

        Without this fallback the jump cut would receive an empty list and a
        fully loud clip would be trimmed to zero length.
        """
        analysis = make_analysis(duration=10.0, db_value=-20.0)
        self.assertEqual(audio.quiet_segments(analysis), [(0.0, 10.0)])

    def test_returns_the_real_silences_when_there_are_any(self):
        db = np.array([-10.0] * 5 + [-60.0] * 5 + [-10.0] * 5, dtype=np.float32)
        analysis = AudioAnalysis(
            sample_rate=RATE,
            duration=1.5,
            hop_ms=100,
            db=db,
            voiced=db >= -40.0,
            silences=[],
            threshold_db=-40.0,
            noise_floor_db=-60.0,
            speech_level_db=-10.0,
        )
        self.assertEqual(audio.quiet_segments(analysis, min_silence=0.3), [(0.5, 1.0)])


class LoadWavMonoTests(unittest.TestCase):
    """The 16-bit scaling that turns bytes into the [-1, 1] samples above."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audio-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write_wav(self, frames: bytes, *, channels: int = 1, width: int = 2) -> Path:
        path = self.tmp / "fixture.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(width)
            handle.setframerate(RATE)
            handle.writeframes(frames)
        return path

    def test_sixteen_bit_samples_map_to_minus_one_and_one(self):
        """The divisor is 32768, not 32767: -32768 maps to exactly -1.0.

        Using 32767 would make the loudest negative sample land at -1.00003 and
        clip on the way back out.
        """
        values = [-32_768, -16_384, 0, 16_384, 32_767]
        frames = np.array(values, dtype="<i2").tobytes()
        samples, rate = audio.load_wav_mono(self._write_wav(frames))
        self.assertEqual(rate, RATE)
        self.assertEqual(samples.dtype, np.float32)
        for got, want in zip(samples.tolist(), [-1.0, -0.5, 0.0, 0.5, 32_767 / 32_768]):
            with self.subTest(got=got):
                self.assertAlmostEqual(got, want, places=6)

    def test_stereo_is_averaged_into_mono(self):
        """Left at full scale and right silent averages to +0.5, not to full."""
        frames = np.array([32_767, 0, -32_768, 0], dtype="<i2").tobytes()
        samples, _rate = audio.load_wav_mono(self._write_wav(frames, channels=2))
        self.assertEqual(len(samples), 2)
        self.assertAlmostEqual(float(samples[0]), 32_767 / 32_768 / 2.0, places=6)
        self.assertAlmostEqual(float(samples[1]), -0.5, places=6)

    def test_an_unsupported_sample_width_is_refused(self):
        """24-bit (3 bytes) has no branch: it must raise, not decode as garbage."""
        frames = b"\x00\x00\x00" * 4
        with self.assertRaises(ClipperError) as caught:
            audio.load_wav_mono(self._write_wav(frames, width=3))
        self.assertIn("24 bits", str(caught.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
