"""Unit tests for :mod:`viralclipper.hooks`.

The hook detector is a pure function over text, so these tests assert the
scoring contract (saturating combination, anchored question patterns, PT/EN
coverage, term extraction) rather than exact pattern internals.
"""

from __future__ import annotations

import math
import unittest

from viralclipper import hooks


class ScoreTextTests(unittest.TestCase):
    def test_empty_text_scores_zero(self):
        result = hooks.score_text("")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.hits, [])
        self.assertFalse(result.is_question)

    def test_whitespace_only_scores_zero(self):
        self.assertEqual(hooks.score_text("   \n\t ").score, 0.0)

    def test_neutral_sentence_has_no_hook(self):
        self.assertEqual(hooks.score_text("A mesa tem quatro pernas.").score, 0.0)

    def test_single_strong_hook_uses_saturating_formula(self):
        # "segredo" is a 1.0 weight pattern and nothing else matches.
        result = hooks.score_text("O segredo e esse")
        self.assertAlmostEqual(result.score, round(1.0 - math.exp(-1.0), 4), places=4)

    def test_portuguese_nobody_tells_hook(self):
        result = hooks.score_text("Ninguem te conta isso")
        self.assertGreater(result.score, 0.5)
        self.assertTrue(result.matched_terms)

    def test_english_nobody_tells_hook(self):
        result = hooks.score_text("Nobody tells you this")
        self.assertGreater(result.score, 0.5)

    def test_anchored_question_pattern_plus_question_mark(self):
        # "Por que ..." (0.7, anchored) and the trailing "?" (0.55) both fire.
        result = hooks.score_text("Por que isso acontece?")
        self.assertAlmostEqual(result.score, round(1.0 - math.exp(-1.25), 4), places=4)
        self.assertTrue(result.is_question)

    def test_question_mark_alone_flags_question(self):
        result = hooks.score_text("Isso funciona mesmo?")
        self.assertTrue(result.is_question)
        self.assertGreater(result.score, 0.0)

    def test_matching_is_case_insensitive(self):
        lower = hooks.score_text("o segredo do dinheiro")
        upper = hooks.score_text("O SEGREDO DO DINHEIRO")
        self.assertEqual(lower.score, upper.score)

    def test_money_and_attention_hooks_fire(self):
        money = hooks.score_text("Isso gera dinheiro de verdade")
        attention = hooks.score_text("Atencao: olha isso agora")
        self.assertGreater(money.score, 0.5)
        self.assertGreater(attention.score, 0.5)

    def test_score_saturates_below_one(self):
        stacked = hooks.score_text(
            "Atencao: o segredo do dinheiro, o que ninguem conta, nunca fale sobre isso?"
        )
        self.assertGreater(stacked.score, 0.5)
        self.assertLess(stacked.score, 1.0)

    def test_matched_terms_are_non_empty_strings(self):
        result = hooks.score_text("Nunca compre esse erro fatal")
        self.assertTrue(result.hits)
        for term in result.matched_terms:
            self.assertIsInstance(term, str)
            self.assertTrue(term.strip())

    def test_whitespace_is_normalized_before_matching(self):
        spaced = hooks.score_text("O    segredo\n\ne   esse")
        self.assertGreater(spaced.score, 0.0)


class OpeningBonusTests(unittest.TestCase):
    def test_empty_text_returns_zero(self):
        self.assertEqual(hooks.opening_bonus(""), 0.0)

    def test_hook_inside_first_words_scores(self):
        text = "O segredo que ninguem conta sobre dinheiro"
        expected = hooks.score_text(" ".join(text.split()[:12])).score
        self.assertAlmostEqual(hooks.opening_bonus(text), expected, places=6)

    def test_hook_beyond_first_twelve_words_is_ignored(self):
        filler = " ".join(f"palavra{index}" for index in range(12))
        text = f"{filler} segredo"
        self.assertEqual(hooks.opening_bonus(text), 0.0)
        self.assertGreater(hooks.score_text(text).score, 0.0)


class FillerRatioTests(unittest.TestCase):
    def test_empty_text_returns_zero(self):
        self.assertEqual(hooks.filler_ratio(""), 0.0)

    def test_no_filler_returns_zero(self):
        self.assertEqual(hooks.filler_ratio("Eu falei isso ontem"), 0.0)

    def test_all_filler_returns_one(self):
        self.assertEqual(hooks.filler_ratio("tipo assim ne"), 1.0)

    def test_punctuation_is_stripped_before_lookup(self):
        self.assertEqual(hooks.filler_ratio("tipo, assim."), 1.0)

    def test_ratio_is_fraction_of_words(self):
        # "tipo" and "assim" out of four words.
        self.assertAlmostEqual(hooks.filler_ratio("tipo assim eu falei"), 0.5, places=6)

    def test_matching_is_case_insensitive(self):
        self.assertEqual(hooks.filler_ratio("TIPO ASSIM"), 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
