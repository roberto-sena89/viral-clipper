"""Unit tests for :mod:`viralclipper.transcribe`.

A real whisper checkpoint is never loaded here: the tests inject a fake
``faster_whisper`` module so the ladder, the CPU retry and the error messages can
be driven deterministically. The real backend is covered by ``smoke_test.py``.
"""

from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tests._fixtures import make_config
from viralclipper import transcribe
from viralclipper.util import ClipperError

MEMORY_ERROR = "mkl_malloc: failed to allocate memory"


class _RecordingLogger:
    """Minimal stand-in that keeps every message with its level."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def _record(self, level: str, message: str) -> None:
        self.messages.append((level, message))

    def step(self, message: str) -> None:
        self._record("step", message)

    def ok(self, message: str) -> None:
        self._record("ok", message)

    def warn(self, message: str) -> None:
        self._record("warn", message)

    def info(self, message: str) -> None:
        self._record("info", message)

    def debug(self, message: str) -> None:
        self._record("debug", message)

    def warnings(self) -> list[str]:
        return [message for level, message in self.messages if level == "warn"]


class _FakeWhisperModel:
    """Records every construction attempt and returns a stub instance."""

    def __init__(self, *, fail: dict[str, Exception] | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = dict(fail or {})
        self.loaded: list[str] = []

    def __call__(self, name, *, device, compute_type, **kwargs):
        self.calls.append((name, device, compute_type))
        error = self.fail.get(name)
        if error is not None:
            raise error
        self.loaded.append(name)
        return _StubModel(name)


class _StubModel:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeWord:
    def __init__(self, start: float, end: float, word: str, probability: float = 0.9) -> None:
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability


class _FakeSegment:
    def __init__(self, words) -> None:
        self.words = words


class _FakeInfo:
    language = "pt"
    language_probability = 0.98


class _TranscribingModel:
    """Stub whose ``transcribe`` yields the words it was built with."""

    def __init__(self, words) -> None:
        self.words = words
        self.kwargs: dict = {}

    def transcribe(self, path, **kwargs):
        self.kwargs = kwargs
        return [_FakeSegment(self.words)], _FakeInfo()


@contextmanager
def fake_whisper(factory):
    """Install a fake ``faster_whisper`` module for the duration of the block."""
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = factory
    with patch.dict(sys.modules, {"faster_whisper": module}):
        yield factory


class SmallerModelsTests(unittest.TestCase):
    def test_small_lists_every_smaller_checkpoint_largest_first(self):
        self.assertEqual(
            transcribe.smaller_models("small"), ["base", "tiny"]
        )

    def test_large_lists_the_whole_ladder_below_it(self):
        self.assertEqual(
            transcribe.smaller_models("large-v3"),
            ["large-v2", "large", "medium", "small", "base", "tiny"],
        )

    def test_tiny_has_nothing_smaller(self):
        self.assertEqual(transcribe.smaller_models("tiny"), [])

    def test_an_unknown_name_has_no_fallbacks(self):
        """A typo must fail fast rather than silently loading another checkpoint."""
        self.assertEqual(transcribe.smaller_models("smal"), [])

    def test_the_name_is_matched_case_insensitively(self):
        self.assertEqual(transcribe.smaller_models("  SMALL "), ["base", "tiny"])


class IsMemoryErrorTests(unittest.TestCase):
    def test_recognises_the_ctranslate2_allocation_failure(self):
        self.assertTrue(transcribe.is_memory_error(RuntimeError(MEMORY_ERROR)))

    def test_recognises_plain_out_of_memory_wording(self):
        self.assertTrue(transcribe.is_memory_error(MemoryError("out of memory")))

    def test_does_not_claim_a_download_failure_is_a_memory_problem(self):
        self.assertFalse(
            transcribe.is_memory_error(RuntimeError("HTTP Error 404: Not Found"))
        )


class LoadModelTests(unittest.TestCase):
    def test_returns_the_configured_checkpoint_when_it_loads(self):
        factory = _FakeWhisperModel()
        config = make_config(whisper_model="small", whisper_device="cpu")
        with fake_whisper(factory):
            loaded = transcribe.load_model(config, _RecordingLogger())
        self.assertEqual(loaded.name, "small")
        self.assertEqual(loaded.device, "cpu")
        self.assertEqual(factory.loaded, ["small"])

    def test_steps_down_the_ladder_when_the_checkpoint_does_not_fit(self):
        """The 8 GB laptop case: 'small' raises, 'base' loads, the run survives."""
        factory = _FakeWhisperModel(fail={"small": RuntimeError(MEMORY_ERROR)})
        config = make_config(whisper_model="small", whisper_device="cpu")
        logger = _RecordingLogger()
        with fake_whisper(factory):
            loaded = transcribe.load_model(config, logger)
        self.assertEqual(loaded.name, "base")
        self.assertEqual([call[0] for call in factory.calls], ["small", "base"])
        self.assertTrue(
            any("small" in message and "base" in message for message in logger.warnings())
        )

    def test_keeps_stepping_down_until_something_loads(self):
        factory = _FakeWhisperModel(
            fail={
                "medium": RuntimeError(MEMORY_ERROR),
                "small": RuntimeError(MEMORY_ERROR),
                "base": RuntimeError(MEMORY_ERROR),
            }
        )
        config = make_config(whisper_model="medium", whisper_device="cpu")
        with fake_whisper(factory):
            loaded = transcribe.load_model(config, _RecordingLogger())
        self.assertEqual(loaded.name, "tiny")
        self.assertEqual(
            [call[0] for call in factory.calls], ["medium", "small", "base", "tiny"]
        )

    def test_reports_the_checkpoint_it_settled_on(self):
        factory = _FakeWhisperModel(fail={"large-v3": RuntimeError("boom")})
        config = make_config(whisper_model="large-v3", whisper_device="cpu")
        with fake_whisper(factory):
            loaded = transcribe.load_model(config, _RecordingLogger())
        self.assertNotEqual(loaded.name, "large-v3")
        self.assertEqual(loaded.name, "large-v2")

    def test_raises_once_every_candidate_failed(self):
        factory = _FakeWhisperModel(
            fail={
                "base": RuntimeError(MEMORY_ERROR),
                "tiny": RuntimeError(MEMORY_ERROR),
            }
        )
        config = make_config(whisper_model="base", whisper_device="cpu")
        with fake_whisper(factory):
            with self.assertRaises(ClipperError) as caught:
                transcribe.load_model(config, _RecordingLogger())
        self.assertEqual([call[0] for call in factory.calls], ["base", "tiny"])
        self.assertIn("out of memory", str(caught.exception))
        self.assertIn("--engine audio", str(caught.exception))

    def test_an_unknown_model_name_fails_without_trying_other_checkpoints(self):
        factory = _FakeWhisperModel(fail={"smal": RuntimeError("bad name")})
        config = make_config(whisper_model="smal", whisper_device="cpu")
        with fake_whisper(factory):
            with self.assertRaises(ClipperError) as caught:
                transcribe.load_model(config, _RecordingLogger())
        self.assertEqual([call[0] for call in factory.calls], ["smal"])
        self.assertIn("--model name", str(caught.exception))

    def test_a_gpu_attempt_is_retried_on_cpu_int8(self):
        class _CudaAware:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            def __call__(self, name, *, device, compute_type, **kwargs):
                self.calls.append((name, device, compute_type))
                if device != "cpu":
                    raise RuntimeError("no CUDA device")
                return _StubModel(name)

        factory = _CudaAware()
        config = make_config(
            whisper_model="small", whisper_device="cuda", whisper_compute_type="float16"
        )
        with fake_whisper(factory):
            loaded = transcribe.load_model(config, _RecordingLogger())
        self.assertEqual(factory.calls, [("small", "cuda", "float16"), ("small", "cpu", "int8")])
        self.assertEqual(loaded.device, "cpu")
        self.assertEqual(loaded.compute_type, "int8")

    def test_missing_faster_whisper_names_the_install_command(self):
        config = make_config(whisper_model="small")
        with patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaises(ClipperError) as caught:
                transcribe.load_model(config, _RecordingLogger())
        self.assertIn("pip install faster-whisper", str(caught.exception))


class TranscribeTests(unittest.TestCase):
    def test_uses_the_model_it_is_handed_and_reports_its_name(self):
        """The caller passes a resolved model, so the transcript must not lie."""
        model = _TranscribingModel([_FakeWord(0.0, 0.5, " ola")])
        loaded = transcribe.LoadedModel(
            model=model, name="base", device="cpu", compute_type="int8"
        )
        config = make_config(whisper_model="small")
        transcript = transcribe.transcribe("audio.wav", config, None, model=loaded)
        self.assertEqual(transcript.model_name, "base")
        self.assertEqual(transcript.text, "ola")

    def test_loads_the_configured_checkpoint_when_none_is_passed(self):
        calls: list[tuple[str, str, str]] = []
        model_holder = _TranscribingModel([_FakeWord(0.0, 1.0, " oi")])

        def build(name, *, device, compute_type, **kwargs):
            calls.append((name, device, compute_type))
            return model_holder

        config = make_config(whisper_model="tiny", whisper_device="cpu")
        with fake_whisper(build):
            transcript = transcribe.transcribe("audio.wav", config, None)
        self.assertEqual(calls, [("tiny", "cpu", "int8")])
        self.assertEqual(transcript.model_name, "tiny")
        self.assertEqual(transcript.language, "pt")
        self.assertAlmostEqual(transcript.language_probability, 0.98)

    def test_words_are_sorted_by_start_time(self):
        words = [
            _FakeWord(2.0, 2.5, " mundo"),
            _FakeWord(0.0, 0.5, " ola"),
            _FakeWord(1.0, 1.5, " bonito"),
        ]
        loaded = transcribe.LoadedModel(
            model=_TranscribingModel(words), name="base", device="cpu", compute_type="int8"
        )
        transcript = transcribe.transcribe("a.wav", make_config(), None, model=loaded)
        self.assertEqual([word.text for word in transcript.words], ["ola", "bonito", "mundo"])
        self.assertEqual(transcript.words_between(0.0, 1.6), transcript.words[:2])

    def test_blank_words_are_dropped(self):
        words = [_FakeWord(0.0, 0.5, " ola"), _FakeWord(0.5, 0.7, "   ")]
        loaded = transcribe.LoadedModel(
            model=_TranscribingModel(words), name="base", device="cpu", compute_type="int8"
        )
        transcript = transcribe.transcribe("a.wav", make_config(), None, model=loaded)
        self.assertEqual(len(transcript.words), 1)

    def test_a_transcription_crash_becomes_a_clipper_error(self):
        class _Broken:
            def transcribe(self, path, **kwargs):
                raise RuntimeError("decoder exploded")

        loaded = transcribe.LoadedModel(
            model=_Broken(), name="base", device="cpu", compute_type="int8"
        )
        with self.assertRaises(ClipperError) as caught:
            transcribe.transcribe("a.wav", make_config(), None, model=loaded)
        self.assertIn("Transcription failed", str(caught.exception))
        self.assertIn("decoder exploded", str(caught.exception))

    def test_vad_and_beam_settings_reach_the_backend(self):
        model = _TranscribingModel([_FakeWord(0.0, 0.5, " ola")])
        loaded = transcribe.LoadedModel(
            model=model, name="base", device="cpu", compute_type="int8"
        )
        config = make_config(beam_size=7, vad_filter=False, language="en")
        transcribe.transcribe(Path("a.wav"), config, None, model=loaded)
        self.assertEqual(model.kwargs["beam_size"], 7)
        self.assertFalse(model.kwargs["vad_filter"])
        self.assertEqual(model.kwargs["language"], "en")
        self.assertTrue(model.kwargs["word_timestamps"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
