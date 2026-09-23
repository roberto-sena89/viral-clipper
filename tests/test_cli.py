"""Unit tests for the CLI, focused on its exit codes.

The stage functions are patched out so the tests describe the contract a caller
depends on: which code comes back for which outcome.
"""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tests._fixtures import make_analysis, make_config
from viralclipper import cli, report
from viralclipper.util import Logger


def _clip(index: int, *, meets_minimum: bool) -> report.ClipRecord:
    return report.ClipRecord(
        index=index,
        start=0.0,
        end=10.0,
        duration=10.0,
        score=50.0,
        meets_minimum=meets_minimum,
        file="clip.mp4" if meets_minimum else "",
    )


class ExitCodeTests(unittest.TestCase):
    """Exit 3 means "a clip came out too short", and only that."""

    def run_single(self, *, clips, dry_run: bool = False, quiet: bool = False) -> int:
        config = make_config(
            url="https://youtu.be/x",
            output_dir=Path("out"),
            dry_run=dry_run,
            quiet=quiet,
            keep_temp=True,
        )
        analysis = make_analysis(duration=120.0)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "analyse",
                    return_value=({"id": "abc", "title": "Titulo"}, analysis, None, []),
                )
            )
            stack.enter_context(patch.object(cli.pipeline, "select_windows", return_value=[]))
            stack.enter_context(
                patch.object(cli.pipeline, "render_windows", return_value=clips)
            )
            stack.enter_context(
                patch.object(cli.report, "write_json", return_value=Path("c.json"))
            )
            stack.enter_context(
                patch.object(cli.report, "write_markdown", return_value=Path("c.md"))
            )
            stack.enter_context(patch.object(cli.util, "ensure_dir", return_value=Path("out")))
            code, _run_report, _error = cli.run_single(config, Logger(quiet=True))
        return code

    def test_all_clips_long_enough_returns_zero(self):
        self.assertEqual(
            self.run_single(clips=[_clip(1, meets_minimum=True)]), 0
        )

    def test_a_short_clip_returns_three(self):
        self.assertEqual(
            self.run_single(clips=[_clip(1, meets_minimum=True), _clip(2, meets_minimum=False)]),
            3,
        )

    def test_the_exit_code_does_not_depend_on_quiet(self):
        """A caller in a pipeline cannot have its exit code changed by -q."""
        clips = [_clip(1, meets_minimum=False)]
        self.assertEqual(self.run_single(clips=clips, quiet=False), 3)
        self.assertEqual(self.run_single(clips=clips, quiet=True), 3)

    def test_plan_only_does_not_report_a_short_clip(self):
        """Nothing was rendered, so there is no duration to judge."""
        self.assertEqual(
            self.run_single(clips=[_clip(1, meets_minimum=False)], dry_run=True), 0
        )

    def test_no_clips_returns_zero(self):
        self.assertEqual(self.run_single(clips=[]), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
