"""Unit tests for :mod:`viralclipper.viral_report`.

The report is a documented heuristic over the scorer's own components, so the
tests fix the contract: which lexicon drives which metric, how the potential
blends them, and which opening label a given window gets.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._fixtures import make_unit
from viralclipper import viral_report
from viralclipper.score import Window


def _window(*, text: str, components: dict | None = None, hook_terms=None, score: float = 60.0) -> Window:
    window = Window(
        start=10.0,
        end=50.0,
        unit_start=0,
        unit_end=1,
        text=text,
        hook_terms=list(hook_terms or []),
    )
    window.score = score
    window.components = dict(components or {})
    return window


FULL = {
    "hook_start": 1.0,
    "hook_peak": 0.9,
    "hook_density": 0.8,
    "question": 1.0,
    "speech": 1.0,
    "energy": 0.8,
    "boundary": 1.0,
    "length": 1.0,
    "clean": 1.0,
}


class OpeningLabelTests(unittest.TestCase):
    def test_secret_hook(self):
        self.assertEqual(
            viral_report.opening_label("Ninguem te conta esse segredo", FULL),
            "REVELACAO / SEGREDO",
        )

    def test_numbered_list(self):
        self.assertEqual(
            viral_report.opening_label("tres dicas para crescer rapido", FULL),
            "LISTA NUMERADA",
        )

    def test_question(self):
        self.assertEqual(
            viral_report.opening_label("por que o doutor usa uma espada", FULL),
            "PERGUNTA DIRETA",
        )

    def test_strong_generic_hook(self):
        self.assertEqual(
            viral_report.opening_label("olha isso agora", {"hook_start": 0.8}),
            "ABERTURA IMPACTANTE",
        )

    def test_neutral_opening(self):
        self.assertEqual(viral_report.opening_label("a mesa tem quatro pernas", {}), "ABERTURA NEUTRA")


class AnalyseWindowTests(unittest.TestCase):
    def setUp(self):
        self.units = [
            make_unit(10.0, 20.0, text="Ninguem te conta esse segredo.", hook_score=0.9, hook_terms=["segredo"]),
            make_unit(20.0, 30.0, text="Mas isso e uma mentira absurda, concorda?", hook_score=0.5),
        ]

    def test_hook_and_peak_come_from_the_units(self):
        analysis = viral_report.analyse_window(
            _window(text="Ninguem te conta esse segredo.", components=FULL), self.units, index=1
        )
        self.assertEqual(analysis.index, 1)
        self.assertIn("segredo", analysis.hook)
        self.assertIn("segredo", analysis.peak)
        self.assertIn("concorda", analysis.conclusion)

    def test_metrics_stay_in_range_and_potential_blends_them(self):
        analysis = viral_report.analyse_window(
            _window(text="dicas e passos para tudo", components=FULL), self.units, index=1
        )
        for value in (
            analysis.retention,
            analysis.comments,
            analysis.shares,
            analysis.controversy,
            analysis.viral_potential,
        ):
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)
        expected = round(
            0.45 * analysis.retention
            + 0.20 * analysis.shares
            + 0.20 * analysis.comments
            + 0.15 * analysis.controversy
        )
        self.assertLessEqual(abs(analysis.viral_potential - expected), 1)

    def test_strong_components_beat_weak_ones(self):
        strong = viral_report.analyse_window(_window(text="dicas e passos", components=FULL), self.units, index=1)
        weak = viral_report.analyse_window(
            _window(text="dicas e passos", components={"hook_start": 0.0, "clean": 0.2}), self.units, index=2
        )
        self.assertGreater(strong.retention, weak.retention)
        self.assertGreater(strong.viral_potential, weak.viral_potential)

    def test_controversy_lexicon_moves_the_metric(self):
        spicy = viral_report.analyse_window(
            _window(text="isso e uma mentira absurda", components=FULL), self.units, index=1
        )
        calm = viral_report.analyse_window(
            _window(text="a mesa tem quatro pernas", components=FULL), self.units, index=2
        )
        self.assertGreater(spicy.controversy, calm.controversy)

    def test_share_lexicon_moves_the_metric(self):
        useful = viral_report.analyse_window(
            _window(text="tres passos e dicas praticas", components=FULL), self.units, index=1
        )
        plain = viral_report.analyse_window(
            _window(text="a mesa tem quatro pernas", components=FULL), self.units, index=2
        )
        self.assertGreater(useful.shares, plain.shares)

    def test_why_mentions_filler_when_clean_is_low(self):
        analysis = viral_report.analyse_window(
            _window(text="opa", components={"hook_start": 0.0, "clean": 0.1}), self.units, index=1
        )
        self.assertIn("muletas", analysis.why)

    def test_long_text_is_truncated_with_ellipsis(self):
        analysis = viral_report.analyse_window(
            _window(text="palavra " * 60, components=FULL), self.units, index=1
        )
        self.assertTrue(analysis.subject.endswith("..."))
        self.assertIsNotNone(analysis.hook)
        self.assertLessEqual(len(analysis.subject.split()), 23)

    def test_dict_and_markdown_carry_every_field(self):
        analysis = viral_report.analyse_window(
            _window(text="segredo revelado", components=FULL), self.units, index=3
        )
        payload = analysis.to_dict()
        for key in (
            "headline", "subject", "why", "hook", "peak", "conclusion",
            "retention", "comments", "shares", "controversy", "viral_potential",
        ):
            self.assertIn(key, payload)
        markdown = analysis.to_markdown()
        self.assertIn("#3 - ", markdown)
        self.assertIn("Potencial de viralizacao:", markdown)
        self.assertIn("Retencao:", markdown)


class AnalyseWindowsTests(unittest.TestCase):
    def test_indexes_are_one_based_in_order(self):
        units = [make_unit(0.0, 40.0, text="oi", hook_score=0.1)]
        windows = [
            _window(text="primeiro", components=FULL),
            _window(text="segundo", components=FULL),
        ]
        analyses = viral_report.analyse_windows(windows, units)
        self.assertEqual([analysis.index for analysis in analyses], [1, 2])

    def test_markdown_file_is_written(self):
        units = [make_unit(0.0, 40.0, text="oi", hook_score=0.1)]
        analyses = viral_report.analyse_windows([_window(text="primeiro", components=FULL)], units)
        tmp = Path(tempfile.mkdtemp(prefix="vc_vr_"))
        path = viral_report.write_markdown(analyses, tmp / "viral_report.md", "Video teste")
        body = path.read_text(encoding="utf-8")
        self.assertIn("Video teste", body)
        self.assertIn("#1 - ", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

