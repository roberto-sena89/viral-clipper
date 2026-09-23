"""Unit tests for :mod:`viralclipper.pipeline`.

``render_windows`` downloads media and renders with ffmpeg, so the tests patch
the download and render helpers and replace the process pool with an inline
executor: the worker then runs in the calling process, which keeps those patches
effective and the suite offline and fast. The real ``ProcessPoolExecutor`` is
exercised by ``smoke_test.py``.
"""

from __future__ import annotations

import pickle
import shutil
import tempfile
import unittest
import wave
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._fixtures import make_analysis, make_config, make_transcript, make_word
from viralclipper import pipeline, report, transcribe, transcript_cache
from viralclipper.config import ClipConfig
from viralclipper.render_task import _clip_filename
from viralclipper.score import Window
from viralclipper.util import ClipperError, Logger

WORK = Path("work")


class _InlineExecutor:
    """Stand-in for ``ProcessPoolExecutor`` that renders in this process.

    ``render_windows`` iterates ``as_completed``, which needs real
    :class:`~concurrent.futures.Future` objects, and the worker has to see the
    patched download/render helpers, so each submit runs immediately and returns
    an already finished future.
    """

    def __init__(self, max_workers=None):
        self.max_workers = max_workers
        self.submitted = 0

    def __enter__(self) -> "_InlineExecutor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def submit(self, fn, *args, **kwargs) -> Future:
        self.submitted += 1
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - mirrors a crashing worker
            future.set_exception(exc)
        return future


class _FailingExecutor(_InlineExecutor):
    """Pool whose every task result raises, so the fallback path runs."""

    def submit(self, fn, *args, **kwargs) -> Future:
        self.submitted += 1
        future: Future = Future()
        future.set_exception(RuntimeError("pool boom"))
        return future


class _OutOfProcessExecutor(_InlineExecutor):
    """Runs the task on a detached copy, the way a real worker process does.

    ``_InlineExecutor`` mutates the parent's own object, which hides every bug
    that lives at the process boundary. Round-tripping the task through pickle
    reproduces the real semantics: the worker gets a copy, so the parent has to
    read the result back from the future instead of from its own object.
    """

    def submit(self, fn, *args, **kwargs) -> Future:
        self.submitted += 1
        future: Future = Future()
        try:
            detached = pickle.loads(pickle.dumps(args[0]))
            future.set_result(fn(detached, *args[1:], **kwargs))
        except BaseException as exc:  # noqa: BLE001 - mirrors a crashing worker
            future.set_exception(exc)
        return future


def _window(start: float, end: float, *, text: str = "hello", score: float = 50.0) -> Window:
    return Window(
        start=start,
        end=end,
        unit_start=0,
        unit_end=1,
        text=text,
        score=score,
        components={"hook": 0.4},
        hook_terms=["segredo"],
    )


def _rendered(duration: float = 10.0, name: str = "clip.mp4") -> MagicMock:
    rendered = MagicMock()
    rendered.path = Path(name)
    rendered.duration = duration
    rendered.width = 1080
    rendered.height = 1920
    return rendered


class PipelineTestCase(unittest.TestCase):
    """Shared setup: three candidate windows and a scratch output directory."""

    def setUp(self) -> None:
        self.output_dir = Path(tempfile.mkdtemp(prefix="viralclipper-test-"))
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)
        self.analysis = make_analysis(duration=120.0)
        self.transcript = make_transcript([make_word(0.0, 2.0, "hello")])
        self.windows = [_window(0.0, 10.0), _window(20.0, 30.0), _window(40.0, 50.0)]

    def config(self, **overrides) -> ClipConfig:
        payload = {
            "output_dir": self.output_dir,
            "min_duration": 5.0,
            "max_duration": 30.0,
            "target_duration": 15.0,
            "count": 3,
            "pad_start": 0.0,
            "pad_end": 0.0,
        }
        payload.update(overrides)
        return make_config(**payload)

    def render(self, windows, config, *, executor=None, render_side_effect=None):
        """Run ``render_windows`` with downloads and rendering patched out.

        Returns the records, the download and render mocks and the pool instance
        that ``render_windows`` created (``None`` when it never built a pool), so
        a test can assert what happened inside a worker. ``executor`` is the fake
        pool *class*; ``render_side_effect`` injects renderer failures.
        """
        pool = None
        with ExitStack() as stack:
            download_section = stack.enter_context(
                patch.object(pipeline.download, "download_section", return_value=Path("sec.mp4"))
            )
            download_full = stack.enter_context(
                patch.object(pipeline.download, "download_full", return_value=Path("full.mp4"))
            )
            render_clip = stack.enter_context(
                patch.object(
                    pipeline.render,
                    "render_clip",
                    return_value=_rendered(),
                    side_effect=render_side_effect,
                )
            )
            if executor is not None:
                def build_pool(**kwargs):
                    nonlocal pool
                    pool = executor(**kwargs)
                    return pool

                stack.enter_context(patch.object(pipeline, "ProcessPoolExecutor", build_pool))
            records = pipeline.render_windows(
                windows, {"id": "abc"}, self.analysis, self.transcript, config, WORK,
                Logger(quiet=True),
            )
        return records, download_section, download_full, render_clip, pool


class SequentialRenderTests(PipelineTestCase):
    def test_renders_every_window_in_order(self):
        records, download_section, _, render_clip, _ = self.render(
            self.windows, self.config(parallel=False)
        )
        self.assertEqual([record.index for record in records], [1, 2, 3])
        self.assertEqual([record.start for record in records], [0.0, 20.0, 40.0])
        self.assertTrue(all(record.meets_minimum for record in records))
        self.assertTrue(all(record.duration == 10.0 for record in records))
        self.assertEqual(render_clip.call_count, 3)
        self.assertEqual(download_section.call_count, 3)

    def test_sections_are_downloaded_once_per_clip_in_the_parent(self):
        """Workers reuse the pre-downloaded section instead of fetching it again."""
        records, download_section, _, render_clip, _ = self.render(
            self.windows, self.config(parallel=False)
        )
        self.assertEqual(len(records), 3)
        self.assertEqual(download_section.call_count, 3)
        destinations = [call.args[3].parent.name for call in download_section.call_args_list]
        self.assertEqual(destinations, ["clip_01", "clip_02", "clip_03"])
        sources = {call.kwargs["source"] for call in render_clip.call_args_list}
        self.assertEqual(sources, {Path("sec.mp4")})

    def test_empty_window_list_renders_nothing(self):
        records, download_section, _, render_clip, _ = self.render(
            [], self.config(parallel=False)
        )
        self.assertEqual(records, [])
        download_section.assert_not_called()
        render_clip.assert_not_called()

    def test_dry_run_skips_download_and_render(self):
        records, download_section, download_full, render_clip, _ = self.render(
            self.windows, self.config(parallel=False, dry_run=True)
        )
        self.assertEqual(len(records), 3)
        self.assertEqual([record.file for record in records], ["", "", ""])
        self.assertFalse(any(record.meets_minimum for record in records))
        download_section.assert_not_called()
        download_full.assert_not_called()
        render_clip.assert_not_called()


class FullDownloadModeTests(PipelineTestCase):
    def test_single_download_is_shared_by_every_clip(self):
        records, download_section, download_full, render_clip, _ = self.render(
            self.windows, self.config(parallel=False, download_mode="full")
        )
        self.assertEqual(len(records), 3)
        download_full.assert_called_once()
        download_section.assert_not_called()
        sources = {call.kwargs["source"] for call in render_clip.call_args_list}
        self.assertEqual(sources, {Path("full.mp4")})


class ParallelRenderTests(PipelineTestCase):
    def test_pool_renders_every_clip(self):
        records, _, _, render_clip, pool = self.render(
            self.windows, self.config(workers=2), executor=_InlineExecutor
        )
        self.assertEqual(pool.submitted, 3)
        self.assertEqual(pool.max_workers, 2)
        self.assertEqual(render_clip.call_count, 3)
        self.assertEqual([record.index for record in records], [1, 2, 3])
        self.assertTrue(all(record.file for record in records))

    def test_automatic_pool_size_is_clamped_to_the_clip_count(self):
        with patch.object(pipeline.os, "cpu_count", return_value=8):
            _, _, _, _, pool = self.render(
                self.windows, self.config(workers=0), executor=_InlineExecutor
            )
        self.assertEqual(pool.max_workers, 3)

    def test_single_clip_skips_the_pool(self):
        records, _, _, _, pool = self.render(
            self.windows[:1], self.config(workers=2), executor=_InlineExecutor
        )
        self.assertIsNone(pool)
        self.assertEqual(len(records), 1)

    def test_blas_threads_are_capped_before_the_pool_starts(self):
        """Workers inherit the environment, so the cap has to come first."""
        order: list[str] = []

        class _OrderedExecutor(_InlineExecutor):
            def __init__(self, max_workers=None):
                order.append("pool")
                super().__init__(max_workers)

        with patch.object(
            pipeline.util,
            "limit_native_threads",
            side_effect=lambda *args, **kwargs: order.append("limit"),
        ):
            self.render(self.windows, self.config(workers=2), executor=_OrderedExecutor)
        self.assertEqual(order[:2], ["limit", "pool"])


    def test_pool_failure_falls_back_to_sequential_rendering(self):
        records, download_section, _, render_clip, pool = self.render(
            self.windows, self.config(workers=2), executor=_FailingExecutor
        )
        self.assertEqual(pool.submitted, 3)
        self.assertEqual(render_clip.call_count, 3)
        self.assertEqual(download_section.call_count, 3)
        self.assertEqual([record.index for record in records], [1, 2, 3])
        self.assertTrue(all(record.file for record in records))

    def test_one_failing_clip_does_not_stop_the_others(self):
        failure = RuntimeError("ffmpeg boom")
        records, _, _, _, _ = self.render(
            self.windows,
            self.config(workers=2),
            executor=_InlineExecutor,
            render_side_effect=[_rendered(), failure, _rendered()],
        )
        self.assertEqual(len(records), 3)
        self.assertTrue(records[0].file)
        self.assertEqual(records[1].file, "")
        self.assertFalse(records[1].meets_minimum)
        self.assertTrue(records[2].file)
        self.assertEqual(records[2].index, 3)


class ProcessBoundaryTests(PipelineTestCase):
    """The parent must take worker results back from the future.

    A worker process runs on its own copy of the task, so any state it writes is
    invisible to the parent. Reading the local object instead of the future left
    every record ``None`` and made the report crash after the clips were already
    rendered.
    """

    def test_parallel_records_survive_the_process_boundary(self):
        records, _, _, render_clip, pool = self.render(
            self.windows, self.config(workers=2), executor=_OutOfProcessExecutor
        )
        self.assertEqual(pool.submitted, 3)
        self.assertEqual(render_clip.call_count, 3)
        self.assertEqual(len(records), 3)
        self.assertTrue(all(record is not None for record in records))
        self.assertEqual([record.index for record in records], [1, 2, 3])
        self.assertTrue(all(record.file for record in records))

    def test_the_report_can_be_built_from_parallel_records(self):
        """Regression: asdict() blew up on the None records this used to leave."""
        records, _, _, _, _ = self.render(
            self.windows, self.config(workers=2), executor=_OutOfProcessExecutor
        )
        run_report = report.RunReport(
            url="https://youtu.be/x",
            title="Titulo",
            video_id="abc",
            uploader="canal",
            source_duration=120.0,
            language="pt",
            model="small",
            engine="hybrid",
            min_duration=5.0,
            max_duration=30.0,
            clips=records,
        )
        payload = run_report.to_dict()
        self.assertEqual(len(payload["clips"]), 3)
        self.assertEqual(payload["clips"][0]["start_label"], "00:00")
        self.assertIn("clip", payload["clips"][0]["file"])

    def test_a_worker_failure_still_yields_a_record(self):
        records, _, _, _, _ = self.render(
            self.windows,
            self.config(workers=2),
            executor=_OutOfProcessExecutor,
            render_side_effect=[_rendered(), RuntimeError("ffmpeg boom"), _rendered()],
        )
        self.assertEqual(len(records), 3)
        self.assertTrue(records[0].file)
        self.assertEqual(records[1].file, "")
        self.assertTrue(records[2].file)


class EngineFallbackTests(PipelineTestCase):
    """A checkpoint that will not fit in RAM must degrade the run, not abort it.

    This is the failure a real 8 GB machine hits: ctranslate2 cannot allocate the
    weights, and before this behaviour existed the whole job died even though the
    audio-only engine could still have produced clips.
    """

    def analyse(self, engine: str, *, transcript=None, error=None):
        """Run ``analyse`` with the network, ffmpeg and whisper steps patched out."""
        patch_kwargs = {"side_effect": error} if error is not None else {"return_value": transcript}
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    pipeline.download,
                    "fetch_metadata",
                    return_value={"id": "abc", "title": "Titulo", "duration": 120.0},
                )
            )
            stack.enter_context(
                patch.object(pipeline.download, "download_audio", return_value=Path("a.webm"))
            )
            stack.enter_context(
                patch.object(pipeline.audio_mod, "extract_audio", return_value=Path("a.wav"))
            )
            stack.enter_context(
                patch.object(pipeline.audio_mod, "analyze_audio", return_value=self.analysis)
            )
            transcribe_mock = stack.enter_context(
                patch.object(pipeline, "_transcribe_cached", **patch_kwargs)
            )
            result = pipeline.analyse(self.config(engine=engine), WORK, Logger(quiet=True))
        return result, transcribe_mock

    def test_hybrid_keeps_going_when_transcription_dies(self):
        _, _, transcript, units = self.analyse(
            "hybrid", error=ClipperError("Could not load a whisper model: out of memory")
        )[0]
        self.assertIsNone(transcript)
        self.assertTrue(units)

    def test_hybrid_units_are_built_from_audio_alone(self):
        _, _, transcript, units = self.analyse("hybrid", error=ClipperError("boom"))[0]
        self.assertIsNone(transcript)
        self.assertTrue(all(not unit.text for unit in units))

    def test_hybrid_still_returns_the_transcript_when_it_works(self):
        _, _, transcript, units = self.analyse("hybrid", transcript=self.transcript)[0]
        self.assertIs(transcript, self.transcript)
        self.assertTrue(units)

    def test_transcript_engine_still_fails_loudly(self):
        """Asking for transcription explicitly means a failure is the answer."""
        with self.assertRaises(ClipperError):
            self.analyse("transcript", error=ClipperError("Could not load a whisper model"))

    def test_audio_engine_never_uses_the_transcript(self):
        _, _, transcript, units = self.analyse("audio", transcript=self.transcript)[0]
        self.assertIsNone(transcript)
        self.assertTrue(units)

    def test_audio_engine_does_not_pay_for_a_model(self):
        _, transcribe_mock = self.analyse("audio", transcript=self.transcript)
        transcribe_mock.assert_not_called()


def _write_wav(path: Path, seconds: float = 0.5) -> Path:
    """Write a small silent PCM WAV so the cache has real bytes to fingerprint."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * int(seconds * 16_000))
    return path


class TranscribeCacheTests(unittest.TestCase):
    """The two pass lookup: cheap on the configured checkpoint, honest on a fallback.

    A run that fell back to a smaller checkpoint stored its transcript under a
    key naming that checkpoint. A later run asking for the full size model must
    neither reuse it silently nor be denied it when the model still does not fit.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vc-transcribe-cache-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache_dir = self.tmp / "cache"
        self.wav = _write_wav(self.tmp / "analysis.wav")
        self.transcript = make_transcript([make_word(0.0, 1.0, "ola")])
        self.logger = Logger(quiet=True)

    def config(self, **overrides) -> ClipConfig:
        payload = {"whisper_model": "small", "cache_dir": self.cache_dir}
        payload.update(overrides)
        return make_config(**payload)

    def seed(self, config: ClipConfig, model: str) -> str:
        """Store a transcript the way a run that resolved ``model`` would."""
        key = transcript_cache.cache_key("vid1", self.wav, config, model=model)
        transcript_cache.save(
            self.cache_dir, key, self.transcript,
            source_id="vid1", wav_path=self.wav, config=config, logger=self.logger,
        )
        return key

    def loaded(self, name: str) -> transcribe.LoadedModel:
        return transcribe.LoadedModel(
            model=object(), name=name, device="cpu", compute_type="int8"
        )

    def run_cached(self, config: ClipConfig, *, loaded=None, transcribe_mock=None):
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(pipeline.transcribe, "load_model", return_value=loaded)
            )
            if transcribe_mock is not None:
                stack.enter_context(
                    patch.object(pipeline.transcribe, "transcribe", transcribe_mock)
                )
            return pipeline._transcribe_cached(self.wav, "vid1", config, self.tmp, self.logger)

    def test_a_hit_on_the_configured_checkpoint_never_loads_a_model(self):
        config = self.config()
        self.seed(config, "small")
        load_model = MagicMock(side_effect=AssertionError("must not load a model"))
        with patch.object(pipeline.transcribe, "load_model", load_model):
            result = pipeline._transcribe_cached(self.wav, "vid1", config, self.tmp, self.logger)
        self.assertEqual(result.text, self.transcript.text)
        load_model.assert_not_called()

    def test_a_fallback_entry_is_reused_without_transcribing_again(self):
        config = self.config()
        self.seed(config, "base")
        transcribe_mock = MagicMock(side_effect=AssertionError("must not transcribe"))
        result = self.run_cached(config, loaded=self.loaded("base"), transcribe_mock=transcribe_mock)
        self.assertEqual(result.text, self.transcript.text)
        transcribe_mock.assert_not_called()

    def test_a_fallback_entry_is_not_served_as_the_requested_model(self):
        """Asking for 'small' must not silently accept a cached 'base' transcript."""
        config = self.config()
        self.seed(config, "base")
        transcribe_mock = MagicMock(return_value=self.transcript)
        self.run_cached(config, loaded=self.loaded("small"), transcribe_mock=transcribe_mock)
        transcribe_mock.assert_called_once()

    def test_a_miss_stores_under_the_checkpoint_that_actually_ran(self):
        config = self.config()
        transcribe_mock = MagicMock(return_value=self.transcript)
        self.run_cached(config, loaded=self.loaded("base"), transcribe_mock=transcribe_mock)
        resolved = transcript_cache.cache_key("vid1", self.wav, config, model="base")
        requested = transcript_cache.cache_key("vid1", self.wav, config)
        self.assertTrue((self.cache_dir / f"{resolved}.json").exists())
        self.assertFalse((self.cache_dir / f"{requested}.json").exists())

    def test_a_second_run_reuses_what_the_fallback_stored(self):
        config = self.config()
        transcribe_mock = MagicMock(return_value=self.transcript)
        self.run_cached(config, loaded=self.loaded("base"), transcribe_mock=transcribe_mock)
        result = self.run_cached(config, loaded=self.loaded("base"), transcribe_mock=MagicMock(
            side_effect=AssertionError("must not transcribe twice")
        ))
        self.assertEqual(result.text, self.transcript.text)

    def test_a_disabled_cache_goes_straight_to_transcription(self):
        config = self.config(transcript_cache=False)
        load_model = MagicMock(side_effect=AssertionError("transcribe loads its own model"))
        transcribe_mock = MagicMock(return_value=self.transcript)
        with patch.object(pipeline.transcribe, "load_model", load_model), \
             patch.object(pipeline.transcribe, "transcribe", transcribe_mock):
            result = pipeline._transcribe_cached(
                self.wav, "vid1", config, self.tmp, self.logger
            )
        self.assertEqual(result.text, self.transcript.text)
        load_model.assert_not_called()
        transcribe_mock.assert_called_once()


class UnreachableMinScoreWarningTests(unittest.TestCase):
    """The gate-vs-ceiling warning fires the moment the transcript is lost."""

    def test_warns_when_gate_is_above_the_audio_only_ceiling(self):
        logger = MagicMock()
        config = make_config(min_score=60.0)
        pipeline._warn_unreachable_min_score(config, None, logger)
        logger.warn.assert_called_once()
        message = logger.warn.call_args.args[0]
        self.assertIn("--min-score 60", message)
        self.assertIn("unreachable", message)

    def test_reports_headroom_when_gate_sits_under_the_ceiling(self):
        logger = MagicMock()
        pipeline._warn_unreachable_min_score(make_config(min_score=45.0), None, logger)
        logger.warn.assert_not_called()
        logger.info.assert_called_once()
        self.assertIn("tops out", logger.info.call_args.args[0])

    def test_silent_when_transcript_exists(self):
        logger = MagicMock()
        transcript = make_transcript([make_word(0.0, 0.5, "oi")])
        pipeline._warn_unreachable_min_score(make_config(min_score=45.0), transcript, logger)
        logger.warn.assert_not_called()
        logger.info.assert_not_called()

    def test_silent_when_gate_is_off(self):
        logger = MagicMock()
        pipeline._warn_unreachable_min_score(make_config(min_score=0.0), None, logger)
        logger.warn.assert_not_called()
        logger.info.assert_not_called()


class TemplateRenderTests(PipelineTestCase):
    """Templates and the variant matrix, exercised through the real task builder."""

    def _tasks(self, config):
        return pipeline._build_tasks(
            self.windows,
            self.analysis,
            self.transcript,
            config,
            {"id": "abc"},
            WORK,
            self.output_dir,
            None,
            Logger(quiet=True),
        )

    def test_no_template_is_one_task_per_window(self):
        tasks = self._tasks(self.config())
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(task.template is None for task in tasks))
        self.assertTrue(all(task.variant == "" for task in tasks))

    def test_a_full_frame_template_draws_no_zones_but_still_travels(self):
        # split-card would compose; full-frame is the identity composition, so
        # it must not add a variant suffix or change the task count.
        tasks = self._tasks(self.config(template="full-frame"))
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(task.variant == "" for task in tasks))
        self.assertEqual(tasks[0].template.name, "full-frame")

    def test_split_card_template_is_a_single_task_per_window(self):
        tasks = self._tasks(self.config(template="split-card"))
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0].template.name, "split-card")
        # The unadorned filename is kept when there is only one rendering.
        self.assertEqual(tasks[0].variant, "")

    def test_variant_axes_multiply_the_task_list(self):
        tasks = self._tasks(
            self.config(
                template="split-card",
                variant_presets=["neon", "fire"],
                variant_layouts=["focus", "blur"],
            )
        )
        self.assertEqual(len(tasks), 12)  # 3 windows x 4 variants

    def test_each_variant_gets_its_own_config(self):
        tasks = self._tasks(
            self.config(
                template="split-card",
                variant_presets=["neon", "fire"],
                variant_layouts=["focus"],
            )
        )
        seen = {(task.config.caption_preset, task.config.layout) for task in tasks}
        self.assertEqual(seen, {("neon", "focus"), ("fire", "focus")})
        # The base config must not be mutated by any variant.
        self.assertEqual(self.config().caption_preset, "karaoke")

    def test_each_variant_gets_its_own_work_directory(self):
        tasks = self._tasks(
            self.config(
                template="split-card",
                variant_presets=["neon", "fire"],
                variant_layouts=["focus", "blur"],
            )
        )
        # Two renders of the same window must never share a captions.ass.
        self.assertEqual(len({str(task.clip_dir) for task in tasks}), len(tasks))

    def test_variant_filenames_are_distinct_and_grouped(self):
        tasks = self._tasks(
            self.config(template="split-card", variant_presets=["neon", "fire"])
        )
        names = [
            _clip_filename(task.position, task.window, {"id": "abc"}, task.variant)
            for task in tasks
        ]
        self.assertEqual(len(set(names)), len(names))
        self.assertTrue(any("__neon" in name for name in names))
        self.assertTrue(any("__fire" in name for name in names))

    def test_repeated_windows_share_one_download(self):
        records, download_section, _, render_clip, _ = self.render(
            self.windows,
            self.config(
                parallel=False,
                template="split-card",
                variant_presets=["neon", "fire"],
                variant_layouts=["focus", "blur"],
            ),
        )
        # 3 windows x 4 variants = 12 renders, but the section is fetched once
        # per window: an axis that only re-encodes must not cost network.
        self.assertEqual(render_clip.call_count, 12)
        self.assertEqual(download_section.call_count, 3)

    def test_every_variant_receives_the_resolved_template(self):
        _, _, _, render_clip, _ = self.render(
            self.windows,
            self.config(
                parallel=False,
                template="split-card",
                variant_presets=["neon", "fire"],
            ),
        )
        templates = [call.kwargs.get("template") for call in render_clip.call_args_list]
        self.assertTrue(all(t is not None for t in templates))
        self.assertEqual(
            {t.caption_preset for t in templates}, {"neon", "fire"}
        )

