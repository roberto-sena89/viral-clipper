"""Unit tests for :mod:`viralclipper.batch`.

The runner is injected, so the whole batch loop — isolation, resume, retry —
is exercised without ffmpeg, yt-dlp or a network.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from viralclipper import batch
from viralclipper.util import ClipperError, Logger


class BatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="batch-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = Logger(quiet=True)
        self.connection = batch.open_manifest(self.tmp / "manifest.sqlite3")
        self.addCleanup(self.connection.close)


class LoadUrlsTests(BatchTestCase):
    def _write(self, text: str) -> Path:
        path = self.tmp / "urls.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_one_url_per_line(self):
        path = self._write("https://a\nhttps://b\n")
        self.assertEqual(batch.load_urls(path), ["https://a", "https://b"])

    def test_blank_lines_and_comments_are_ignored(self):
        path = self._write("# lote de segunda\n\nhttps://a\n   \n# fim\nhttps://b\n")
        self.assertEqual(batch.load_urls(path), ["https://a", "https://b"])

    def test_duplicates_are_dropped_keeping_order(self):
        path = self._write("https://b\nhttps://a\nhttps://b\n")
        self.assertEqual(batch.load_urls(path), ["https://b", "https://a"])

    def test_surrounding_whitespace_is_trimmed(self):
        path = self._write("  https://a  \n\thttps://b\t\n")
        self.assertEqual(batch.load_urls(path), ["https://a", "https://b"])

    def test_utf8_bom_is_tolerated(self):
        path = self.tmp / "bom.txt"
        path.write_text("https://a\n", encoding="utf-8-sig")
        self.assertEqual(batch.load_urls(path), ["https://a"])

    def test_missing_file_raises(self):
        with self.assertRaises(ClipperError):
            batch.load_urls(self.tmp / "nope.txt")

    def test_empty_file_raises(self):
        with self.assertRaises(ClipperError):
            batch.load_urls(self._write("# nada aqui\n\n"))


class UrlSlugTests(unittest.TestCase):
    def test_short_url(self):
        self.assertEqual(batch.url_slug("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_watch_url(self):
        self.assertEqual(
            batch.url_slug("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ"
        )

    def test_watch_url_with_extra_params(self):
        self.assertEqual(
            batch.url_slug("https://www.youtube.com/watch?list=PL1&v=dQw4w9WgXcQ&t=10"),
            "dQw4w9WgXcQ",
        )

    def test_shorts_and_embed_and_live(self):
        for shape in (
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
        ):
            self.assertEqual(batch.url_slug(shape), "dQw4w9WgXcQ")

    def test_non_youtube_url_falls_back_to_a_digest(self):
        slug = batch.url_slug("https://example.com/video/123")
        self.assertTrue(slug.startswith("url_"))
        self.assertEqual(len(slug), len("url_") + 12)

    def test_slug_is_stable(self):
        self.assertEqual(
            batch.url_slug("https://example.com/a"),
            batch.url_slug("https://example.com/a"),
        )

    def test_different_urls_get_different_slugs(self):
        self.assertNotEqual(
            batch.url_slug("https://example.com/a"),
            batch.url_slug("https://example.com/b"),
        )


class ManifestTests(BatchTestCase):
    def test_seed_inserts_new_urls_as_pending(self):
        self.assertEqual(batch.seed(self.connection, ["a", "b"]), 2)
        self.assertEqual(batch.counts(self.connection)[batch.STATUS_PENDING], 2)

    def test_seed_is_idempotent(self):
        batch.seed(self.connection, ["a", "b"])
        self.assertEqual(batch.seed(self.connection, ["a", "b", "c"]), 1)
        self.assertEqual(batch.counts(self.connection)[batch.STATUS_PENDING], 3)

    def test_seed_does_not_resurrect_a_finished_job(self):
        batch.seed(self.connection, ["a"])
        batch.mark_done(self.connection, "a", clips=3)
        batch.seed(self.connection, ["a"])
        self.assertEqual(batch.counts(self.connection)[batch.STATUS_DONE], 1)
        self.assertEqual(batch.counts(self.connection)[batch.STATUS_PENDING], 0)

    def test_mark_running_counts_attempts(self):
        batch.seed(self.connection, ["a"])
        batch.mark_running(self.connection, "a")
        batch.mark_running(self.connection, "a")
        job = batch.all_jobs(self.connection)[0]
        self.assertEqual(job.status, batch.STATUS_RUNNING)
        self.assertEqual(job.attempts, 2)

    def test_mark_done_records_clips_and_output(self):
        batch.seed(self.connection, ["a"])
        batch.mark_done(self.connection, "a", clips=4, output_dir="out/abc")
        job = batch.all_jobs(self.connection)[0]
        self.assertTrue(job.is_done)
        self.assertEqual(job.clips, 4)
        self.assertEqual(job.output_dir, "out/abc")

    def test_mark_failed_records_the_error(self):
        batch.seed(self.connection, ["a"])
        batch.mark_failed(self.connection, "a", "yt-dlp: video unavailable")
        job = batch.all_jobs(self.connection)[0]
        self.assertEqual(job.status, batch.STATUS_FAILED)
        self.assertIn("unavailable", job.error)

    def test_error_message_is_truncated(self):
        batch.seed(self.connection, ["a"])
        batch.mark_failed(self.connection, "a", "x" * 2000)
        self.assertLessEqual(len(batch.all_jobs(self.connection)[0].error), 500)

    def test_reset_failed_returns_jobs_to_the_queue(self):
        batch.seed(self.connection, ["a", "b"])
        batch.mark_failed(self.connection, "a", "boom")
        batch.mark_done(self.connection, "b", clips=1)
        self.assertEqual(batch.reset_failed(self.connection), 1)
        self.assertEqual(batch.counts(self.connection)[batch.STATUS_PENDING], 1)
        self.assertEqual(batch.counts(self.connection)[batch.STATUS_DONE], 1)

    def test_reset_failed_clears_the_error(self):
        batch.seed(self.connection, ["a"])
        batch.mark_failed(self.connection, "a", "boom")
        batch.reset_failed(self.connection)
        self.assertEqual(batch.all_jobs(self.connection)[0].error, "")

    def test_reset_stale_running_recovers_a_killed_process(self):
        batch.seed(self.connection, ["a"])
        batch.mark_running(self.connection, "a")
        self.assertEqual(batch.reset_stale_running(self.connection), 1)
        self.assertEqual(batch.counts(self.connection)[batch.STATUS_PENDING], 1)

    def test_pending_jobs_keeps_insertion_order(self):
        batch.seed(self.connection, ["c", "a", "b"])
        self.assertEqual([job.url for job in batch.pending_jobs(self.connection)], ["c", "a", "b"])

    def test_counts_includes_empty_statuses(self):
        tally = batch.counts(self.connection)
        self.assertEqual(set(tally), {
            batch.STATUS_PENDING, batch.STATUS_RUNNING,
            batch.STATUS_DONE, batch.STATUS_FAILED,
        })

    def test_manifest_is_reopenable(self):
        batch.seed(self.connection, ["a"])
        self.connection.commit()
        reopened = sqlite3.connect(str(self.tmp / "manifest.sqlite3"))
        try:
            rows = reopened.execute("SELECT url FROM jobs").fetchall()
        finally:
            reopened.close()
        self.assertEqual(rows, [("a",)])


class RunBatchTests(BatchTestCase):
    def _runner(self, results):
        calls: list[str] = []

        def runner(url: str):
            calls.append(url)
            return results[url]

        return runner, calls

    def test_every_pending_job_runs(self):
        batch.seed(self.connection, ["a", "b"])
        runner, calls = self._runner({
            "a": (0, "", 3, "out/a"),
            "b": (0, "", 2, "out/b"),
        })
        summary = batch.run_batch(self.connection, runner, self.log)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual((summary.processed, summary.succeeded, summary.failed), (2, 2, 0))

    def test_one_failure_does_not_stop_the_batch(self):
        batch.seed(self.connection, ["a", "b", "c"])
        runner, calls = self._runner({
            "a": (0, "", 1, "out/a"),
            "b": (1, "video unavailable", 0, ""),
            "c": (0, "", 1, "out/c"),
        })
        summary = batch.run_batch(self.connection, runner, self.log)
        self.assertEqual(calls, ["a", "b", "c"])
        self.assertEqual((summary.succeeded, summary.failed), (2, 1))
        statuses = {job.url: job.status for job in batch.all_jobs(self.connection)}
        self.assertEqual(statuses["a"], batch.STATUS_DONE)
        self.assertEqual(statuses["b"], batch.STATUS_FAILED)
        self.assertEqual(statuses["c"], batch.STATUS_DONE)

    def test_a_raising_runner_is_recorded_as_a_failure(self):
        batch.seed(self.connection, ["a", "b"])

        def runner(url: str):
            if url == "a":
                raise RuntimeError("ffmpeg exploded")
            return 0, "", 1, "out/b"

        summary = batch.run_batch(self.connection, runner, self.log)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.succeeded, 1)
        failed = next(job for job in batch.all_jobs(self.connection) if job.url == "a")
        self.assertIn("ffmpeg exploded", failed.error)

    def test_a_second_run_skips_both_done_and_failed_jobs(self):
        """Only ``pending`` rows are picked up; failed ones wait for --retry-failed."""
        batch.seed(self.connection, ["a", "b"])
        runner, first_calls = self._runner({
            "a": (0, "", 1, "out/a"),
            "b": (1, "boom", 0, ""),
        })
        batch.run_batch(self.connection, runner, self.log)

        second_calls: list[str] = []

        def second(url: str):
            second_calls.append(url)
            return 0, "", 1, f"out/{url}"

        summary = batch.run_batch(self.connection, second, self.log)
        self.assertEqual(first_calls, ["a", "b"])
        self.assertEqual(second_calls, [])
        self.assertEqual(summary.processed, 0)

    def test_a_resumed_batch_picks_up_only_what_is_left(self):
        """A third URL added to the file later is the only one that runs."""
        batch.seed(self.connection, ["a"])
        runner, _ = self._runner({"a": (0, "", 1, "out/a")})
        batch.run_batch(self.connection, runner, self.log)

        batch.seed(self.connection, ["a", "b"])
        resumed: list[str] = []

        def second(url: str):
            resumed.append(url)
            return 0, "", 1, f"out/{url}"

        batch.run_batch(self.connection, second, self.log)
        self.assertEqual(resumed, ["b"])

    def test_retry_failed_requeues_the_failures(self):
        batch.seed(self.connection, ["a", "b"])
        runner, _ = self._runner({
            "a": (0, "", 1, "out/a"),
            "b": (1, "boom", 0, ""),
        })
        batch.run_batch(self.connection, runner, self.log)

        retried: list[str] = []

        def second(url: str):
            retried.append(url)
            return 0, "", 2, f"out/{url}"

        summary = batch.run_batch(self.connection, second, self.log, retry_failed=True)
        self.assertEqual(retried, ["b"])
        self.assertEqual(summary.succeeded, 1)

    def test_stale_running_jobs_are_picked_up_again(self):
        batch.seed(self.connection, ["a"])
        batch.mark_running(self.connection, "a")
        runner, calls = self._runner({"a": (0, "", 1, "out/a")})
        batch.run_batch(self.connection, runner, self.log)
        self.assertEqual(calls, ["a"])

    def test_empty_queue_does_nothing(self):
        runner, calls = self._runner({})
        summary = batch.run_batch(self.connection, runner, self.log)
        self.assertEqual(calls, [])
        self.assertEqual(summary.processed, 0)

    def test_keyboard_interrupt_propagates_and_leaves_the_job_resumable(self):
        batch.seed(self.connection, ["a", "b"])

        def runner(url: str):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            batch.run_batch(self.connection, runner, self.log)
        job = batch.all_jobs(self.connection)[0]
        self.assertEqual(job.status, batch.STATUS_RUNNING)
        # The next run recovers it instead of losing it.
        self.assertEqual(batch.reset_stale_running(self.connection), 1)

    def test_clip_counts_are_stored(self):
        batch.seed(self.connection, ["a"])
        runner, _ = self._runner({"a": (0, "", 7, "out/a")})
        batch.run_batch(self.connection, runner, self.log)
        self.assertEqual(batch.all_jobs(self.connection)[0].clips, 7)


class ReportingTests(BatchTestCase):
    def test_summary_mentions_the_remaining_work(self):
        batch.seed(self.connection, ["a", "b", "c"])
        batch.mark_failed(self.connection, "a", "boom")
        batch.mark_done(self.connection, "b", clips=1)
        text = batch.format_summary(self.connection, batch.BatchSummary(processed=2, succeeded=1, failed=1))
        self.assertIn("Resumo do lote", text)
        self.assertIn("pendentes   : 1", text)
        self.assertIn("--retry-failed", text)

    def test_summary_without_failures_omits_the_retry_hint(self):
        batch.seed(self.connection, ["a"])
        text = batch.format_summary(self.connection, batch.BatchSummary(processed=0))
        self.assertNotIn("--retry-failed", text)

    def test_json_export_is_valid_and_complete(self):
        batch.seed(self.connection, ["a", "b"])
        batch.mark_done(self.connection, "a", clips=2, output_dir="out/a")
        path = batch.write_json(self.connection, self.tmp / "batch.json")
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["counts"][batch.STATUS_DONE], 1)
        self.assertEqual(len(document["jobs"]), 2)
        self.assertEqual(document["jobs"][0]["url"], "a")

    def test_json_export_creates_the_parent_directory(self):
        batch.seed(self.connection, ["a"])
        path = batch.write_json(self.connection, self.tmp / "nested" / "deep" / "batch.json")
        self.assertTrue(path.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
