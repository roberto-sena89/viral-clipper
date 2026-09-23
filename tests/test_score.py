"""Unit tests for :mod:`viralclipper.score`.

These cover the three stages that turn words into clips: unit building (word
grouping and audio fallback), candidate generation (duration limits) and
ranking (scoring, non-overlap, determinism).
"""

from __future__ import annotations

import unittest

import numpy as np

from tests._fixtures import (
    make_analysis,
    make_config,
    make_transcript,
    make_unit,
    make_word as w,
)
from viralclipper import score


class BuildUnitsFromWordsTests(unittest.TestCase):
    def test_splits_on_sentence_ending(self):
        words = [
            w(0.0, 0.4, "Oi"),
            w(0.4, 0.8, "tudo"),
            w(0.8, 1.2, "bem."),
            w(1.2, 1.6, "Outra"),
            w(1.6, 2.0, "frase"),
        ]
        units = score.build_units_from_words(words, make_analysis())
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].text, "Oi tudo bem.")
        self.assertEqual(units[1].text, "Outra frase")

    def test_splits_on_long_pause(self):
        words = [w(0.0, 0.4, "a"), w(0.4, 0.8, "b"), w(2.0, 2.4, "c")]
        units = score.build_units_from_words(words, make_analysis())
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].text, "a b")
        self.assertEqual(units[1].text, "c")

    def test_splits_oversized_unit(self):
        words = [w(0.0, 24.5, "a"), w(24.5, 25.0, "b")]
        units = score.build_units_from_words(words, make_analysis())
        self.assertEqual(len(units), 2)
        self.assertLess(units[0].duration, 24.5 + 1e-6)

    def test_indexes_are_sequential(self):
        words = [
            w(0.0, 0.4, "Um."),
            w(0.4, 0.8, "Dois."),
            w(0.8, 1.2, "Tres."),
        ]
        units = score.build_units_from_words(words, make_analysis())
        self.assertEqual([unit.index for unit in units], list(range(len(units))))

    def test_hook_fields_are_populated(self):
        words = [
            w(0.0, 0.4, "O"),
            w(0.4, 0.8, "segredo"),
            w(0.8, 1.2, "e"),
            w(1.2, 1.6, "esse."),
        ]
        units = score.build_units_from_words(words, make_analysis())
        self.assertEqual(len(units), 1)
        self.assertGreater(units[0].hook_score, 0.0)
        self.assertTrue(units[0].hook_terms)

    def test_gap_before_and_after_come_from_silences(self):
        analysis = make_analysis(silences=[(0.5, 1.0), (2.2, 2.7)])
        words = [w(1.0, 1.4, "a"), w(1.4, 1.8, "b"), w(1.8, 2.2, "c.")]
        units = score.build_units_from_words(words, analysis)
        self.assertEqual(len(units), 1)
        self.assertAlmostEqual(units[0].gap_before, 0.5, places=3)
        self.assertAlmostEqual(units[0].gap_after, 0.5, places=3)


class BuildUnitsFromAudioTests(unittest.TestCase):
    def test_long_voiced_run_is_split_into_max_chunks(self):
        units = score.build_units_from_audio(make_analysis(duration=50.0))
        self.assertEqual(len(units), 4)
        for unit in units:
            self.assertAlmostEqual(unit.duration, 12.5, places=3)

    def test_short_voiced_runs_are_skipped(self):
        analysis = make_analysis(duration=10.0)
        analysis.voiced[:] = False
        analysis.voiced[100:110] = True  # 0.2 s run, below the 0.6 s floor
        self.assertEqual(score.build_units_from_audio(analysis), [])


class BuildUnitsTests(unittest.TestCase):
    def test_prefers_transcript_words(self):
        words = [w(0.0, 0.4, "Oi"), w(0.4, 0.8, "tudo.")]
        units = score.build_units(make_transcript(words), make_analysis())
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].text, "Oi tudo.")

    def test_falls_back_to_audio_when_transcript_empty(self):
        transcript = make_transcript([])
        units = score.build_units(transcript, make_analysis(duration=40.0))
        self.assertTrue(units)
        self.assertEqual(units[0].text, "")

    def test_falls_back_to_audio_when_transcript_missing(self):
        units = score.build_units(None, make_analysis(duration=40.0))
        self.assertTrue(units)


class BuildCandidatesTests(unittest.TestCase):
    def _units(self):
        return [
            make_unit(0.0, 10.0),
            make_unit(10.0, 20.0),
            make_unit(20.0, 30.0),
            make_unit(30.0, 40.0),
        ]

    def test_respects_duration_limits(self):
        config = make_config(min_duration=15.0, max_duration=25.0)
        candidates = score.build_candidates(self._units(), config)
        self.assertEqual([round(c.duration, 3) for c in candidates], [20.0, 20.0, 20.0])
        for candidate in candidates:
            self.assertGreaterEqual(candidate.duration, config.min_duration)
            self.assertLessEqual(candidate.duration, config.max_duration)

    def test_empty_when_units_too_short(self):
        units = [make_unit(0.0, 5.0), make_unit(5.0, 10.0)]
        config = make_config(min_duration=15.0, max_duration=25.0)
        self.assertEqual(score.build_candidates(units, config), [])

    def test_hook_terms_are_deduplicated_case_insensitively(self):
        units = [
            make_unit(0.0, 10.0, text="a", hook_terms=["segredo"]),
            make_unit(10.0, 20.0, text="b", hook_terms=["Segredo", "dinheiro"]),
        ]
        config = make_config(min_duration=15.0, max_duration=25.0)
        candidates = score.build_candidates(units, config)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].hook_terms, ["segredo", "dinheiro"])

    def test_window_text_joins_units(self):
        units = [
            make_unit(0.0, 10.0, text="primeira"),
            make_unit(10.0, 20.0, text="segunda"),
        ]
        config = make_config(min_duration=15.0, max_duration=25.0)
        candidates = score.build_candidates(units, config)
        self.assertEqual(candidates[0].text, "primeira segunda")


class ScoreWindowsTests(unittest.TestCase):
    def _scored(self):
        units = [
            make_unit(0.0, 10.0, text="O segredo e esse", hook_score=1.0),
            make_unit(10.0, 20.0, text="nada aqui", hook_score=0.0),
            make_unit(20.0, 30.0, text="Atencao agora", hook_score=0.8),
            make_unit(30.0, 40.0, text="mais texto", hook_score=0.0),
        ]
        config = make_config(min_duration=10.0, max_duration=30.0, target_duration=20.0)
        candidates = score.build_candidates(units, config)
        score.score_windows(candidates, units, make_analysis(), config)
        return candidates

    def test_every_candidate_is_scored(self):
        for candidate in self._scored():
            self.assertGreater(candidate.score, 0.0)
            self.assertLessEqual(candidate.score, 100.0)

    def test_components_cover_every_signal(self):
        for candidate in self._scored():
            for key in score.WEIGHTS:
                self.assertIn(key, candidate.components)
                self.assertIn(f"raw_{key}", candidate.components)

    def test_hook_signal_rewards_the_hook_window(self):
        candidates = self._scored()
        with_hook = next(c for c in candidates if "segredo" in c.text)
        without_hook = next(c for c in candidates if c.text == "nada aqui")
        self.assertGreater(with_hook.components["hook_peak"], without_hook.components["hook_peak"])

    def test_scoring_is_deterministic(self):
        first = [c.score for c in self._scored()]
        second = [c.score for c in self._scored()]
        self.assertEqual(first, second)

    def test_empty_candidate_list_is_a_no_op(self):
        score.score_windows([], [], make_analysis(), make_config())  # must not raise


class RankWindowsTests(unittest.TestCase):
    def _setup(self, count=5, min_gap=1.0):
        units = [make_unit(i * 10.0, (i + 1) * 10.0) for i in range(10)]
        config = make_config(
            min_duration=10.0, max_duration=20.0, target_duration=15.0,
            count=count, min_gap=min_gap,
        )
        candidates = score.build_candidates(units, config)
        return units, config, candidates

    def test_respects_count(self):
        units, config, candidates = self._setup(count=2)
        chosen = score.rank_windows(candidates, units, make_analysis(), config)
        self.assertEqual(len(chosen), 2)

    def test_never_returns_overlapping_windows(self):
        units, config, candidates = self._setup(count=5)
        chosen = score.rank_windows(candidates, units, make_analysis(), config)
        self.assertTrue(chosen)
        for i, first in enumerate(chosen):
            for second in chosen[i + 1:]:
                self.assertFalse(score._too_close(first, second, config.min_gap))
                self.assertFalse(score._too_close(second, first, config.min_gap))

    def test_results_are_sorted_by_start(self):
        units, config, candidates = self._setup(count=5)
        chosen = score.rank_windows(candidates, units, make_analysis(), config)
        starts = [window.start for window in chosen]
        self.assertEqual(starts, sorted(starts))

    def test_ranking_is_deterministic_across_runs(self):
        units, config, candidates = self._setup(count=5)
        first = [w.start for w in score.rank_windows(candidates, units, make_analysis(), config)]
        units2, config2, candidates2 = self._setup(count=5)
        second = [w.start for w in score.rank_windows(candidates2, units2, make_analysis(), config2)]
        self.assertEqual(first, second)

    def test_best_scoring_window_wins_when_not_overlapping(self):
        # Two well separated units, the second one hooked, min_gap large enough
        # that only one clip can survive.
        units = [
            make_unit(0.0, 10.0, text="nada aqui", hook_score=0.0),
            make_unit(100.0, 110.0, text="O segredo e esse", hook_score=1.0),
        ]
        config = make_config(
            min_duration=10.0, max_duration=10.0, target_duration=10.0,
            count=1, min_gap=5.0,
        )
        candidates = score.build_candidates(units, config)
        chosen = score.rank_windows(candidates, units, make_analysis(), config)
        self.assertEqual(len(chosen), 1)
        self.assertIn("segredo", chosen[0].text)


class TooCloseTests(unittest.TestCase):
    def test_overlapping_windows_are_too_close(self):
        first = make_unit(0.0, 20.0)
        second = make_unit(10.0, 30.0)
        self.assertTrue(score._too_close(first, second, 6.0))
        self.assertTrue(score._too_close(second, first, 6.0))

    def test_separated_windows_are_not_too_close(self):
        first = make_unit(0.0, 20.0)
        second = make_unit(40.0, 60.0)
        self.assertFalse(score._too_close(first, second, 6.0))
        self.assertFalse(score._too_close(second, first, 6.0))


class NormalizationTests(unittest.TestCase):
    def test_constant_array_maps_to_half(self):
        result = score._normalize(np.array([3.0, 3.0, 3.0]))
        self.assertTrue(np.allclose(result, 0.5))

    def test_scales_values_into_zero_one(self):
        result = score._normalize(np.linspace(0.0, 100.0, 21))
        self.assertAlmostEqual(float(result.min()), 0.0, places=6)
        self.assertAlmostEqual(float(result.max()), 1.0, places=6)

    def test_empty_array_is_returned_unchanged(self):
        result = score._normalize(np.array([]))
        self.assertEqual(result.size, 0)

    def test_band_clamps_to_zero_one(self):
        self.assertEqual(score._band(-100.0, -45.0, -12.0), 0.0)
        self.assertEqual(score._band(0.0, -45.0, -12.0), 1.0)
        self.assertAlmostEqual(score._band(-20.0, -45.0, -12.0), 25.0 / 33.0, places=6)

    def test_band_with_zero_span_returns_half(self):
        self.assertEqual(score._band(5.0, 5.0, 5.0), 0.5)


class AbsoluteScoreTests(unittest.TestCase):
    """The score must mean the same thing in every video.

    Rescaling signals across the candidate set of one video made the score
    relative: a window scored 0 when it sat among strong neighbours and 100
    when the same window sat among weak ones. That made the number useless as
    a quality gate and hid the fact that a video contained no good clip.
    """

    def _score_target(self, hook_score, neighbours=()):
        units = [make_unit(0.0, 10.0, text="alvo", hook_score=hook_score)]
        for index, value in enumerate(neighbours):
            start = 10.0 + index * 10.0
            units.append(make_unit(start, start + 10.0, text="x", hook_score=value))
        config = make_config(min_duration=10.0, max_duration=10.0, target_duration=10.0)
        candidates = score.build_candidates(units, config)
        score.score_windows(candidates, units, make_analysis(), config)
        return next(c for c in candidates if c.text == "alvo").score

    def test_score_is_independent_of_the_neighbouring_windows(self):
        alone = self._score_target(0.5)
        strong = self._score_target(0.5, neighbours=(1.0, 1.0, 1.0))
        weak = self._score_target(0.5, neighbours=(0.0, 0.0, 0.0))
        self.assertAlmostEqual(alone, strong, places=6)
        self.assertAlmostEqual(alone, weak, places=6)

    def test_a_video_without_any_hook_cannot_produce_a_top_score(self):
        units = [
            make_unit(index * 10.0, (index + 1) * 10.0, text="nada aqui", hook_score=0.0)
            for index in range(6)
        ]
        config = make_config(min_duration=10.0, max_duration=10.0, target_duration=10.0)
        candidates = score.build_candidates(units, config)
        score.score_windows(candidates, units, make_analysis(), config)
        self.assertLess(max(candidate.score for candidate in candidates), 60.0)

    def test_a_hooked_window_scores_above_a_flat_one(self):
        flat = self._score_target(0.0)
        hooked = self._score_target(1.0)
        self.assertGreater(hooked, flat + 20.0)

    def test_scores_stay_inside_the_declared_range(self):
        for hook in (0.0, 0.25, 0.5, 0.75, 1.0):
            value = self._score_target(hook)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 100.0)

    def test_bounded_signals_are_not_rescaled(self):
        """A raw hook signal of 0.8 must survive as 0.8, not become 1.0."""
        units = [
            make_unit(0.0, 10.0, text="a", hook_score=0.2),
            make_unit(10.0, 20.0, text="b", hook_score=0.8),
        ]
        config = make_config(min_duration=10.0, max_duration=10.0, target_duration=10.0)
        candidates = score.build_candidates(units, config)
        score.score_windows(candidates, units, make_analysis(), config)
        by_text = {candidate.text: candidate for candidate in candidates}
        self.assertAlmostEqual(by_text["b"].components["hook_peak"], 0.8, places=6)
        self.assertAlmostEqual(by_text["a"].components["hook_peak"], 0.2, places=6)

    def test_loudness_is_banded_rather_than_ranked(self):
        """Energy uses a fixed dB band, so it does not depend on the set either."""
        units = [make_unit(0.0, 10.0, text="a", hook_score=0.0)]
        config = make_config(min_duration=10.0, max_duration=10.0, target_duration=10.0)
        for db_value in (-20.0, -30.0):
            analysis = make_analysis(db_value=db_value)
            candidates = score.build_candidates(units, config)
            score.score_windows(candidates, units, analysis, config)
            # Components are rounded to four decimals when recorded.
            self.assertAlmostEqual(
                candidates[0].components["energy"],
                round(score._band(db_value, -45.0, -12.0), 4),
                places=4,
            )


class MinScoreGateTests(unittest.TestCase):
    def _candidates(self, config):
        units = [
            make_unit(0.0, 10.0, text="O segredo e esse", hook_score=1.0),
            make_unit(20.0, 30.0, text="nada aqui", hook_score=0.0),
        ]
        candidates = score.build_candidates(units, config)
        score.score_windows(candidates, units, make_analysis(), config)
        return candidates

    def test_zero_min_score_keeps_every_candidate_eligible(self):
        config = make_config(min_duration=10.0, max_duration=10.0, target_duration=10.0)
        candidates = self._candidates(config)
        self.assertEqual(len(score.pick_windows(candidates, config)), 2)

    def test_gate_drops_candidates_below_the_floor(self):
        config = make_config(
            min_duration=10.0, max_duration=10.0, target_duration=10.0, min_score=50.0
        )
        candidates = self._candidates(config)
        chosen = score.pick_windows(candidates, config)
        self.assertEqual(len(chosen), 1)
        self.assertIn("segredo", chosen[0].text)

    def test_gate_above_every_candidate_yields_nothing(self):
        config = make_config(
            min_duration=10.0, max_duration=10.0, target_duration=10.0, min_score=99.9
        )
        self.assertEqual(score.pick_windows(self._candidates(config), config), [])


class PickWindowsTests(unittest.TestCase):
    def test_selection_is_shared_between_rank_and_pick(self):
        units = [make_unit(index * 10.0, (index + 1) * 10.0) for index in range(8)]
        config = make_config(
            min_duration=10.0, max_duration=20.0, target_duration=15.0, count=3, min_gap=1.0
        )
        candidates = score.build_candidates(units, config)
        via_rank = score.rank_windows(list(candidates), units, make_analysis(), config)

        candidates2 = score.build_candidates(units, config)
        score.score_windows(candidates2, units, make_analysis(), config)
        via_pick = score.pick_windows(candidates2, config)

        self.assertEqual(
            [(w.start, w.end) for w in via_rank], [(w.start, w.end) for w in via_pick]
        )


class AudioOnlyCeilingTests(unittest.TestCase):
    """max_score_without_transcript derives from WEIGHTS, never a constant."""

    def test_ceiling_matches_the_audio_signal_weight_share(self):
        expected = (
            sum(w for key, w in score.WEIGHTS.items() if key not in score._TEXT_SIGNALS)
            / sum(score.WEIGHTS.values())
            * 100.0
        )
        self.assertAlmostEqual(score.max_score_without_transcript(), expected)

    def test_ceiling_sits_below_half_with_the_shipped_weights(self):
        ceiling = score.max_score_without_transcript()
        self.assertGreater(ceiling, 40.0)
        self.assertLess(ceiling, 50.0)

    def test_text_signals_are_the_hook_family(self):
        self.assertEqual(
            score._TEXT_SIGNALS,
            frozenset({"hook_start", "hook_peak", "hook_density", "question"}),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

