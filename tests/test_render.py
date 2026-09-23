"""Unit tests for :mod:`viralclipper.render`.

Headline, highlight color, progress bar and caption margin are all decided
before ffmpeg runs (they live in the ASS file and in the filter graph string),
so they are locked down here without a single render.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from viralclipper import render
from viralclipper.util import ClipperError

from ._fixtures import make_config, make_word


def _write_captions(tmp: str, words, **overrides) -> Path | None:
    config = make_config(**overrides)
    return render.build_captions(words, 10.0, Path(tmp) / "captions.ass", config)


class BuildCaptionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vc_render_")

    def test_karaoke_uses_the_configured_highlight_color(self):
        path = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola"), make_word(10.4, 10.9, "mundo.")],
            highlight_color="&H0000CCFF",
        )
        body = path.read_text(encoding="utf-8")
        self.assertIn(r"{\1c&H0000CCFF&\fscx112\fscy112}", body)

    def test_default_highlight_is_yellow(self):
        path = _write_captions(self.tmp, [make_word(10.0, 10.4, "ola")])
        self.assertIn(r"{\1c&H0000FFFF&\fscx112\fscy112}", path.read_text(encoding="utf-8"))

    def test_headline_is_off_by_default(self):
        body = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola"), make_word(10.4, 10.8, "mundo.")]
        ).read_text(encoding="utf-8")
        self.assertNotIn(",Headline,,", body)
        self.assertIn(",Default,,", body)

    def test_headline_is_the_opening_sentence(self):
        words = [
            make_word(10.0, 10.3, "Ninguem"),
            make_word(10.3, 10.6, "te"),
            make_word(10.6, 10.9, "conta."),
            make_word(10.9, 11.2, "depois"),
        ]
        body = _write_captions(self.tmp, words, headline_seconds=3.0).read_text(encoding="utf-8")
        self.assertIn("Dialogue: 1,0:00:00.00,0:00:03.00,Headline,,0,0,0,,", body)
        headline = [line for line in body.splitlines() if ",Headline," in line][0]
        self.assertIn("NINGUEM TE CONTA.", headline)
        self.assertNotIn("DEPOIS", headline)

    def test_headline_caps_at_max_words(self):
        words = [make_word(10.0 + i * 0.3, 10.3 + i * 0.3, f"w{i}") for i in range(20)]
        body = _write_captions(self.tmp, words, headline_seconds=3.0).read_text(encoding="utf-8")
        headline = [line for line in body.splitlines() if ",Headline," in line][0]
        self.assertIn("W0 W1 W2 W3 W4 W5 W6 W7 W8 W9 W10 W11", headline)
        self.assertNotIn("W12", headline)
        # a cut sentence is marked, never presented as complete
        self.assertIn("…", headline)

    def test_full_sentence_inside_the_limit_is_not_marked(self):
        words = [make_word(10.0, 10.3, "Ninguem"), make_word(10.3, 10.6, "conta"), make_word(10.6, 10.9, "isso.")]
        body = _write_captions(self.tmp, words, headline_seconds=3.0).read_text(encoding="utf-8")
        headline = [line for line in body.splitlines() if ",Headline," in line][0]
        self.assertIn("NINGUEM CONTA ISSO.", headline)
        self.assertNotIn("…", headline)

    def test_headline_disabled_with_zero_seconds(self):
        body = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola.")], headline_seconds=0.0
        ).read_text(encoding="utf-8")
        self.assertNotIn(",Headline,,", body)

    def test_custom_headline_text_wins(self):
        body = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola.")],
            headline_text="presta atencao nisso",
            headline_seconds=3.0,
        ).read_text(encoding="utf-8")
        self.assertIn("PRESTA ATENCAO NISSO", body)

    def test_caption_style_none_still_burns_the_headline(self):
        path = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola.")],
            caption_style="none",
            headline_seconds=3.0,
        )
        self.assertIsNotNone(path)
        body = path.read_text(encoding="utf-8")
        self.assertIn(",Headline,", body)
        self.assertNotIn(",Default,", body)

    def test_no_captions_and_no_headline_writes_nothing(self):
        result = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola.")],
            caption_style="none",
            headline_seconds=0.0,
        )
        self.assertIsNone(result)

    def test_caption_margin_goes_to_the_default_style(self):
        body = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola.")], caption_margin_v=640
        ).read_text(encoding="utf-8")
        default_style = [line for line in body.splitlines() if line.startswith("Style: Default,")][0]
        self.assertTrue(default_style.endswith(",90,90,640,1"))

    def test_header_wraps_long_lines_inside_the_margins(self):
        body = _write_captions(self.tmp, [make_word(10.0, 10.4, "ola.")]).read_text(encoding="utf-8")
        self.assertIn("WrapStyle: 0", body)
        # the headline style keeps side margins, so wrapping has a boundary
        headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
        self.assertTrue(headline_style.endswith(",60,60,60,1"))

    def test_headline_style_uses_the_headline_font_size(self):
        body = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola.")], headline_font_size=120
        ).read_text(encoding="utf-8")
        headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
        self.assertIn(",120,", headline_style)


class ProgressBarTests(unittest.TestCase):
    def test_filter_string(self):
        self.assertEqual(
            render._progress_bar_filter(make_config(progress_bar=True), 42.0),
            "drawbox=x=0:y=0:w=iw*t/42.000:h=10:color=yellow@0.9:t=fill",
        )

    def test_disabled_by_default(self):
        self.assertEqual(render._progress_bar_filter(make_config(), 42.0), "")

    def test_disabled_explicitly(self):
        self.assertEqual(render._progress_bar_filter(make_config(progress_bar=False), 42.0), "")

    def test_zero_height_disables(self):
        config = make_config(progress_bar=True, progress_bar_height=0)
        self.assertEqual(render._progress_bar_filter(config, 42.0), "")

    def test_custom_color_and_height(self):
        config = make_config(progress_bar=True, progress_bar_color="0x00FF00", progress_bar_height=14)
        fragment = render._progress_bar_filter(config, 30.0)
        self.assertIn("h=14:color=0x00FF00@0.9", fragment)
        self.assertIn("w=iw*t/30.000", fragment)


class CaptionPresetTests(unittest.TestCase):
    """The preset bundles the look; an explicit flag always wins over it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vc_preset_")

    def test_default_preset_is_the_karaoke_look(self):
        style = render.caption_presets.resolve(make_config())
        self.assertEqual(style.name, "karaoke")
        self.assertEqual(style.font, "Arial Black")
        self.assertEqual(style.font_size, 84)

    def test_unknown_preset_lists_the_choices(self):
        with self.assertRaises(ClipperError) as ctx:
            make_config(caption_preset="nao-existe").validate()
        self.assertIn("karaoke", str(ctx.exception))
        self.assertIn("neon", str(ctx.exception))

    def test_an_explicit_override_beats_the_preset(self):
        style = render.caption_presets.resolve(
            make_config(caption_preset="minimal", font_size=99)
        )
        self.assertEqual(style.font_size, 99)          # override
        self.assertEqual(style.font, "Arial")           # preset
        self.assertFalse(style.uppercase)               # preset

    def test_neon_highlight_reaches_the_ass(self):
        path = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola"), make_word(10.4, 10.8, "mundo.")],
            caption_preset="neon",
        )
        body = path.read_text(encoding="utf-8")
        self.assertIn("&H0088FF00", body)

    def test_bold_box_uses_the_opaque_box_style(self):
        path = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola.")],
            caption_preset="bold-box",
        )
        body = path.read_text(encoding="utf-8")
        default_style = [line for line in body.splitlines() if line.startswith("Style: Default,")][0]
        # BorderStyle 3 (opaque box), outline 14 acting as box padding, no shadow
        self.assertIn(",3,14,0,", default_style)

    def test_boxed_preset_moves_the_box_color_into_the_outline_slot(self):
        # libass paints a BorderStyle-3 box with the OUTLINE colour: the preset's
        # box colour has to land there or a light box comes out dark.
        path = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola.")],
            caption_preset="candy",
        )
        body = path.read_text(encoding="utf-8")
        default_style = [line for line in body.splitlines() if line.startswith("Style: Default,")][0]
        self.assertIn("&HCCF4F4F4", default_style)

    def test_minimal_keeps_sentence_case(self):
        path = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola"), make_word(10.4, 10.8, "mundo.")],
            caption_preset="minimal",
        )
        body = path.read_text(encoding="utf-8")
        self.assertIn("ola", body)
        self.assertNotIn("OLA", body)

    def test_karaoke_keeps_uppercase(self):
        path = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola."), ]
        )
        body = path.read_text(encoding="utf-8")
        self.assertIn("OLA.", body)

    def test_vibrant_presets_reach_the_ass(self):
        for preset, color in (
            ("fire", "&H000055FF"),
            ("magenta-pop", "&H00CC00FF"),
            ("candy", "&H00A56FFF"),
            ("ultra-impact", "&H0000FFFF"),
            ("gold-box", "&H0000D7FF"),
        ):
            with self.subTest(preset=preset):
                path = _write_captions(
                    self.tmp,
                    [make_word(10.0, 10.4, "ola"), make_word(10.4, 10.8, "mundo.")],
                    caption_preset=preset,
                )
                self.assertIn(color, path.read_text(encoding="utf-8"))

    def test_candy_inverts_text_over_a_light_box(self):
        style = render.caption_presets.resolve(make_config(caption_preset="candy"))
        self.assertEqual(style.primary_color, "&H00141414")
        # The box colour lives in box_color: a BorderStyle-3 box is painted by
        # libass with the OUTLINE colour, so render.py moves box_color into that
        # slot. Asserting back_color here passed for the wrong reason - it only
        # ever held the dataclass default.
        self.assertEqual(style.box_color, "&HCCF4F4F4")
        self.assertEqual(style.border_style, 3)

    def test_ultra_impact_uses_impact_and_two_words(self):
        style = render.caption_presets.resolve(make_config(caption_preset="ultra-impact"))
        self.assertEqual(style.font, "Impact")
        self.assertEqual(style.words_per_line, 2)


class ConfigValidationTests(unittest.TestCase):
    def test_highlight_color_must_be_ass_format(self):
        with self.assertRaises(ValueError):
            make_config(highlight_color="yellow").validate()

    def test_negative_headline_seconds_rejected(self):
        with self.assertRaises(ValueError):
            make_config(headline_seconds=-1.0).validate()

    def test_defaults_validate(self):
        make_config().validate()


if __name__ == "__main__":
    unittest.main()
