"""Unit tests for :mod:`viralclipper.transcript_cache`.

Nothing here touches whisper, ffmpeg or the network: the cache operates on a
synthetic WAV file and hand built ``Transcript`` objects.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from tests._fixtures import make_config, make_transcript, make_word
from viralclipper import transcript_cache
from viralclipper.transcribe import Transcript, Word


def write_wav(path: Path, seconds: float = 1.0, sample_rate: int = 16_000) -> Path:
    """Write a small silent PCM WAV so the cache has real bytes to hash."""
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)
    return path


class CacheKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_cache_"))
        self.wav = write_wav(self.tmp / "analysis.wav")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_inputs_produce_same_key(self):
        config = make_config()
        first = transcript_cache.cache_key("vid1", self.wav, config)
        second = transcript_cache.cache_key("vid1", self.wav, config)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_different_source_id_changes_key(self):
        config = make_config()
        base = transcript_cache.cache_key("vid1", self.wav, config)
        self.assertNotEqual(base, transcript_cache.cache_key("vid2", self.wav, config))

    def test_different_model_changes_key(self):
        base = transcript_cache.cache_key("vid1", self.wav, make_config())
        other = transcript_cache.cache_key("vid1", self.wav, make_config(whisper_model="medium"))
        self.assertNotEqual(base, other)

    def test_an_explicit_model_is_what_gets_hashed(self):
        """The loader may step down a checkpoint, and the key has to follow it."""
        config = make_config(whisper_model="small")
        explicit = transcript_cache.cache_key("vid1", self.wav, config, model="base")
        as_base = transcript_cache.cache_key(
            "vid1", self.wav, make_config(whisper_model="base")
        )
        self.assertEqual(explicit, as_base)

    def test_a_fallback_model_does_not_collide_with_the_requested_one(self):
        """A 'base' entry must never be served to a run that asked for 'small'."""
        config = make_config(whisper_model="small")
        requested = transcript_cache.cache_key("vid1", self.wav, config)
        fell_back = transcript_cache.cache_key("vid1", self.wav, config, model="base")
        self.assertNotEqual(requested, fell_back)

    def test_omitting_the_model_keeps_the_configured_one(self):
        config = make_config(whisper_model="small")
        self.assertEqual(
            transcript_cache.cache_key("vid1", self.wav, config),
            transcript_cache.cache_key("vid1", self.wav, config, model="small"),
        )

    def test_different_language_changes_key(self):
        base = transcript_cache.cache_key("vid1", self.wav, make_config())
        other = transcript_cache.cache_key("vid1", self.wav, make_config(language="pt"))
        self.assertNotEqual(base, other)

    def test_different_beam_size_changes_key(self):
        base = transcript_cache.cache_key("vid1", self.wav, make_config())
        other = transcript_cache.cache_key("vid1", self.wav, make_config(beam_size=5))
        self.assertNotEqual(base, other)

    def test_different_vad_flag_changes_key(self):
        base = transcript_cache.cache_key("vid1", self.wav, make_config())
        other = transcript_cache.cache_key("vid1", self.wav, make_config(vad_filter=False))
        self.assertNotEqual(base, other)

    def test_different_compute_type_changes_key(self):
        base = transcript_cache.cache_key("vid1", self.wav, make_config())
        other = transcript_cache.cache_key(
            "vid1", self.wav, make_config(whisper_compute_type="float16")
        )
        self.assertNotEqual(base, other)

    def test_different_audio_content_changes_key(self):
        config = make_config()
        base = transcript_cache.cache_key("vid1", self.wav, config)
        other_wav = write_wav(self.tmp / "other.wav", seconds=2.0)
        self.assertNotEqual(base, transcript_cache.cache_key("vid1", other_wav, config))

    def test_engine_and_render_options_do_not_change_key(self):
        # Only transcription settings may invalidate the cache.
        base = transcript_cache.cache_key("vid1", self.wav, make_config())
        noisy = transcript_cache.cache_key(
            "vid1", self.wav, make_config(engine="transcript", min_duration=10.0, font="Impact")
        )
        self.assertEqual(base, noisy)

    def test_missing_wav_raises_clipper_error(self):
        from viralclipper.util import ClipperError

        with self.assertRaises(ClipperError):
            transcript_cache.cache_key("vid1", self.tmp / "nope.wav", make_config())


class ResolveCacheTests(unittest.TestCase):
    def test_enabled_by_default_outside_the_work_dir(self):
        # O default PRECISA ficar fora do diretório de trabalho: `cli.py` apaga
        # `work` inteiro no `finally`, então um cache lá dentro é escrito e
        # destruído na mesma execução — e toda rodada do mesmo vídeo transcreve
        # de novo. Este teste chamava-se "under_the_work_dir" e afirmava
        # exatamente o comportamento errado.
        config = make_config()
        info = transcript_cache.resolve_cache(config)
        self.assertTrue(info.enabled)
        self.assertEqual(
            info.directory, Path(config.output_dir) / transcript_cache.DEFAULT_CACHE_DIR
        )
        self.assertNotIn(
            "work",
            info.directory.parts,
            "o cache não pode morar dentro do diretório de trabalho",
        )

    def test_explicit_cache_dir_wins(self):
        info = transcript_cache.resolve_cache(make_config(cache_dir=Path("meu/cache")))
        self.assertTrue(info.enabled)
        self.assertEqual(info.directory, Path("meu/cache"))

    def test_disabled_by_flag(self):
        info = transcript_cache.resolve_cache(make_config(transcript_cache=False))
        self.assertFalse(info.enabled)
        self.assertEqual(info.reason, "--no-transcript-cache")


class SaveLoadRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_cache_"))
        self.wav = write_wav(self.tmp / "analysis.wav")
        self.config = make_config()
        self.key = transcript_cache.cache_key("vid1", self.wav, self.config)
        self.transcript = make_transcript(
            [
                make_word(0.0, 0.4, "Ola"),
                make_word(0.4, 0.8, "mundo"),
                make_word(0.8, 1.0, "!"),
            ]
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save(self):
        return transcript_cache.save(
            self.tmp / "cache", self.key, self.transcript,
            source_id="vid1", wav_path=self.wav, config=self.config,
        )

    def test_save_then_load_returns_identical_words(self):
        self.assertIsNotNone(self._save())
        loaded = transcript_cache.load(self.tmp / "cache", self.key)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.language, self.transcript.language)
        self.assertEqual(loaded.model_name, self.transcript.model_name)
        self.assertEqual(len(loaded.words), 3)
        for original, restored in zip(self.transcript.words, loaded.words):
            self.assertAlmostEqual(original.start, restored.start, places=6)
            self.assertAlmostEqual(original.end, restored.end, places=6)
            self.assertEqual(original.text, restored.text)

    def test_load_returns_none_on_miss(self):
        self.assertIsNone(transcript_cache.load(self.tmp / "cache", "0" * 64))

    def test_save_creates_the_cache_directory(self):
        target = self.tmp / "deep" / "nested"
        transcript_cache.save(
            target, self.key, self.transcript,
            source_id="vid1", wav_path=self.wav, config=self.config,
        )
        self.assertTrue((target / f"{self.key}.json").exists())

    def test_saved_entry_records_the_transcription_settings(self):
        self._save()
        payload = json.loads((self.tmp / "cache" / f"{self.key}.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["model"], self.config.whisper_model)
        self.assertEqual(payload["language"], "auto")
        self.assertEqual(payload["beam_size"], self.config.beam_size)
        self.assertEqual(payload["word_count"], 3)
        self.assertEqual(payload["cache_version"], transcript_cache.CACHE_VERSION)

    def test_no_leftover_temporary_file(self):
        self._save()
        self.assertEqual(list((self.tmp / "cache").glob("*.tmp")), [])

    def test_unicode_words_survive_the_round_trip(self):
        transcript = make_transcript([make_word(0.0, 0.5, "coração")])
        transcript_cache.save(
            self.tmp / "cache", self.key, transcript,
            source_id="vid1", wav_path=self.wav, config=self.config,
        )
        loaded = transcript_cache.load(self.tmp / "cache", self.key)
        self.assertEqual(loaded.words[0].text, "coração")

    def test_empty_transcript_is_cacheable(self):
        transcript = Transcript(language="pt", language_probability=0.5, model_name="small", words=[])
        transcript_cache.save(
            self.tmp / "cache", self.key, transcript,
            source_id="vid1", wav_path=self.wav, config=self.config,
        )
        loaded = transcript_cache.load(self.tmp / "cache", self.key)
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.empty)


class CorruptEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_cache_"))
        self.cache = self.tmp / "cache"
        self.cache.mkdir(parents=True)
        self.key = "a" * 64

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entry(self, text: str) -> Path:
        path = self.cache / f"{self.key}.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_invalid_json_is_a_miss_and_is_removed(self):
        path = self._entry("{not json")
        self.assertIsNone(transcript_cache.load(self.cache, self.key))
        self.assertFalse(path.exists())

    def test_missing_header_field_is_a_miss(self):
        path = self._entry(json.dumps({"cache_version": 1, "key": self.key}))
        self.assertIsNone(transcript_cache.load(self.cache, self.key))
        self.assertFalse(path.exists())

    def test_wrong_version_is_a_miss(self):
        payload = {
            "cache_version": 999, "key": self.key, "source_id": "x", "model": "small",
            "language": "auto", "beam_size": 1, "vad_filter": True, "device": "auto",
            "compute_type": "int8", "audio_sha1": "x", "audio_bytes": 1,
            "created_at": 0.0, "word_count": 0, "language_probability": 0.0,
            "model_name": "small", "words": [],
        }
        path = self._entry(json.dumps(payload))
        self.assertIsNone(transcript_cache.load(self.cache, self.key))
        self.assertFalse(path.exists())

    def test_key_mismatch_is_a_miss(self):
        payload = {
            "cache_version": transcript_cache.CACHE_VERSION, "key": "b" * 64,
            "source_id": "x", "model": "small", "language": "auto", "beam_size": 1,
            "vad_filter": True, "device": "auto", "compute_type": "int8",
            "audio_sha1": "x", "audio_bytes": 1, "created_at": 0.0, "word_count": 0,
            "language_probability": 0.0, "model_name": "small", "words": [],
        }
        path = self._entry(json.dumps(payload))
        self.assertIsNone(transcript_cache.load(self.cache, self.key))
        self.assertFalse(path.exists())

    def test_word_with_bad_timing_is_a_miss(self):
        payload = {
            "cache_version": transcript_cache.CACHE_VERSION, "key": self.key,
            "source_id": "x", "model": "small", "language": "auto", "beam_size": 1,
            "vad_filter": True, "device": "auto", "compute_type": "int8",
            "audio_sha1": "x", "audio_bytes": 1, "created_at": 0.0, "word_count": 1,
            "language_probability": 0.0, "model_name": "small",
            "words": [{"start": "nao", "end": 1.0, "text": "a"}],
        }
        path = self._entry(json.dumps(payload))
        self.assertIsNone(transcript_cache.load(self.cache, self.key))
        self.assertFalse(path.exists())


class HousekeepingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_cache_"))
        self.cache = self.tmp / "cache"
        self.cache.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_describe_is_empty_for_a_missing_directory(self):
        self.assertEqual(transcript_cache.describe(self.tmp / "nope"), [])

    def test_describe_skips_unreadable_entries(self):
        (self.cache / "broken.json").write_text("{oops", encoding="utf-8")
        (self.cache / "good.json").write_text(
            json.dumps({"source_id": "v", "model": "small", "language": "pt",
                        "word_count": 4, "created_at": 1.0}),
            encoding="utf-8",
        )
        entries = transcript_cache.describe(self.cache)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source_id"], "v")
        self.assertEqual(entries[0]["word_count"], 4)

    def test_describe_sorts_newest_first(self):
        for name, created in (("a.json", 1.0), ("b.json", 3.0), ("c.json", 2.0)):
            (self.cache / name).write_text(
                json.dumps({"source_id": name, "created_at": created}), encoding="utf-8"
            )
        self.assertEqual(
            [entry["source_id"] for entry in transcript_cache.describe(self.cache)],
            ["b.json", "c.json", "a.json"],
        )

    def test_clear_removes_entries_and_reports_the_count(self):
        for name in ("a.json", "b.json"):
            (self.cache / name).write_text("{}", encoding="utf-8")
        self.assertEqual(transcript_cache.clear(self.cache), 2)
        self.assertEqual(list(self.cache.glob("*.json")), [])

    def test_clear_on_missing_directory_returns_zero(self):
        self.assertEqual(transcript_cache.clear(self.tmp / "nope"), 0)

    def test_clear_leaves_non_json_files_alone(self):
        (self.cache / "keep.txt").write_text("x", encoding="utf-8")
        (self.cache / "a.json").write_text("{}", encoding="utf-8")
        self.assertEqual(transcript_cache.clear(self.cache), 1)
        self.assertTrue((self.cache / "keep.txt").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
