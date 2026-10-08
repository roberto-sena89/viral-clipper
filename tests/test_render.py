"""Unit tests for :mod:`viralclipper.render`.

Headline, highlight color, progress bar and caption margin are all decided
before ffmpeg runs (they live in the ASS file and in the filter graph string),
so they are locked down here without a single render.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from viralclipper import render
from viralclipper import template as tpl
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
        # MarginV keeps the hook clear of the notch: 1920 - 6% = 1805 at default height
        self.assertTrue(headline_style.endswith(",60,60,1805,1"))

    def test_headline_style_uses_the_headline_font_size(self):
        body = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola.")], headline_font_size=120
        ).read_text(encoding="utf-8")
        headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
        self.assertIn(",120,", headline_style)

    def test_headline_is_centered_by_default(self):
        body = _write_captions(self.tmp, [make_word(10.0, 10.4, "ola.")]).read_text(encoding="utf-8")
        headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
        self.assertIn(",1,5,2,8,60,60,1805,1", headline_style)

    def test_headline_align_left_and_right_move_the_digit(self):
        for align, digit in (("left", "7"), ("right", "9")):
            body = _write_captions(
                self.tmp, [make_word(10.0, 10.4, "ola.")], headline_align=align
            ).read_text(encoding="utf-8")
            headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
            self.assertIn(",1,5,2," + digit + ",60,60,1805,1", headline_style)

    def test_headline_top_margin_respects_the_ratio(self):
        # A smaller ratio pushes the hook lower (larger MarginV from the bottom).
        body = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola.")], headline_margin_top_ratio=0.02
        ).read_text(encoding="utf-8")
        headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
        # 1920 - 2% = 1882 at default height
        self.assertTrue(headline_style.endswith(",60,60,1882,1"))

    def test_headline_top_margin_scales_with_canvas_height(self):
        body = _write_captions(
            self.tmp,
            [make_word(10.0, 10.4, "ola.")],
            width=720,
            height=1280,
            headline_margin_top_ratio=0.06,
        ).read_text(encoding="utf-8")
        headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
        # 1280 - 6% = 1203 at the smaller canvas
        self.assertTrue(headline_style.endswith(",60,60,1203,1"))

    def test_headline_align_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            make_config(headline_align="middle").validate()

    def test_box_theme_light_forces_light_box_and_dark_text(self):
        style = render.caption_presets.resolve(make_config(caption_box_theme="light"))
        self.assertEqual(style.border_style, 3)
        self.assertEqual(style.box_color, "&H00F2F2F2")
        self.assertEqual(style.primary_color, "&H00141414")

    def test_box_theme_dark_forces_dark_box_and_white_text(self):
        style = render.caption_presets.resolve(make_config(caption_box_theme="dark"))
        self.assertEqual(style.border_style, 3)
        self.assertEqual(style.box_color, "&HB3141417")
        self.assertEqual(style.primary_color, "&H00FFFFFF")

    def test_box_theme_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            make_config(caption_box_theme="neon").validate()

    def test_headline_side_margins_reach_the_style(self):
        body = _write_captions(
            self.tmp, [make_word(10.0, 10.4, "ola.")], headline_margin_side=120
        ).read_text(encoding="utf-8")
        headline_style = [line for line in body.splitlines() if line.startswith("Style: Headline,")][0]
        # MarginV keeps the hook clear of the notch: 1920 - 6% = 1805 at default height
        self.assertIn(",8,120,120,1805,1", headline_style)

    def test_headline_side_margins_reject_negatives(self):
        with self.assertRaises(ValueError):
            make_config(headline_margin_side=-5).validate()

    def test_manual_reframe_builds_zoom_pan_filter(self):
        filt = render._layout_filter(
            make_config(reframe_zoom=2, reframe_pan_x=0.25, reframe_pan_y=0.75),
            1920,
            1080,
        )
        self.assertIn("scale=2160:3840", filt)
        self.assertIn("x=(in_w-out_w)*0.25", filt)
        self.assertIn("y=(in_h-out_h)*0.75", filt)

    def test_reframe_rejects_zoom_below_one_and_pan_out_of_range(self):
        with self.assertRaises(ValueError):
            make_config(reframe_zoom=0.5).validate()
        with self.assertRaises(ValueError):
            make_config(reframe_pan_x=1.5).validate()
        with self.assertRaises(ValueError):
            make_config(reframe_pan_y=-0.1).validate()


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

    def test_article_presets_use_their_documented_faces_and_colors(self):
        cases = (
            ("roboto-bold", "Roboto", "&H00FFE500"),
            ("inter-bold", "Inter", "&H0000FFCC"),
            ("montserrat-bold", "Montserrat", "&H0000D7FF"),
            ("helvetica-classic", "Helvetica", "&H0000FFFF"),
            ("merriweather-black", "Merriweather", "&H0000D7FF"),
        )
        for name, font, color in cases:
            with self.subTest(preset=name):
                style = render.caption_presets.resolve(make_config(caption_preset=name))
                self.assertEqual(style.font, font)
                self.assertEqual(style.highlight_color, color)

    def test_every_preset_has_a_name_and_description(self):
        for name, preset in render.caption_presets.PRESETS.items():
            with self.subTest(preset=name):
                self.assertEqual(preset.name, name)
                self.assertTrue(preset.description)
                self.assertGreaterEqual(preset.font_size, 20)

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


class TemplateCaptionBandTests(unittest.TestCase):
    """The caption band must follow the video zone, not the canvas bottom."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vc_render_tpl_")

    def _margin(self, template, **overrides):
        config = make_config(**overrides)
        style = render.caption_presets.resolve(config)
        return render._caption_margin_for_band(config, style, template)

    def test_no_template_keeps_the_preset_margin(self):
        style = render.caption_presets.resolve(make_config())
        self.assertEqual(
            render._caption_margin_for_band(make_config(), style, None), style.margin_v
        )

    def test_full_frame_video_zone_keeps_the_preset_margin(self):
        self.assertEqual(
            self._margin(tpl.FULL_FRAME),
            render.caption_presets.resolve(make_config()).margin_v,
        )

    def test_split_template_hugs_the_video_band_bottom_edge(self):
        # split-card's video zone is 62% of 1920 = 1190px, with 730px of card
        # below it. Captions float 12% of the band (143px) above its bottom
        # edge: lower third of the footage, the viral spot.
        margin = self._margin(tpl.SPLIT_CARD)
        self.assertEqual(margin, 730 + 143)

    def test_bottom_anchored_video_clears_the_platform_ui(self):
        # X-like layout: image on top (34%), video below (66%). 12% of the
        # 1267px band is 152px — under the 307px social floor, so the floor
        # wins and the networks' bottom UI never covers the text.
        zones = (
            tpl.Zone(kind="image", fraction=0.34),
            tpl.Zone(kind="video", fraction=0.66),
            tpl.Zone(kind="captions", fraction=0.0),
        )
        template = tpl.Template(name="x", zones=zones)
        self.assertEqual(self._margin(template), 307)

    def test_the_hugging_margin_reaches_the_ass_header(self):
        path = render.build_captions(
            [make_word(10.0, 10.4, "ola")],
            10.0,
            Path(self.tmp) / "captions.ass",
            make_config(),
            tpl.SPLIT_CARD,
        )
        body = path.read_text(encoding="utf-8")
        self.assertIn(",873,1", body)

    def test_default_call_without_a_template_is_unchanged(self):
        plain = render.build_captions(
            [make_word(10.0, 10.4, "ola.")], 10.0, Path(self.tmp) / "a.ass", make_config()
        ).read_text(encoding="utf-8")
        expected = render.caption_presets.resolve(make_config()).margin_v
        self.assertIn(f",{expected},1", plain)

    def test_social_preset_uses_two_word_pops_in_the_safe_zone(self):
        preset = render.caption_presets.get_preset("social")
        self.assertEqual(preset.words_per_line, 2)
        self.assertEqual(preset.margin_v, 560)
        self.assertEqual(preset.font_size, 88)

    def test_low_margin_is_lifted_to_the_social_safe_floor(self):
        # 16% of 1920 = 307: below the TikTok/Reels bottom UI the text would
        # be covered, so the margin is lifted even when asked lower.
        style = render.caption_presets.resolve(make_config(caption_margin_v=100))
        self.assertEqual(
            render._caption_margin_for_band(make_config(caption_margin_v=100), style, None), 307
        )

    def test_safe_floor_scales_with_canvas_height(self):
        style = render.caption_presets.resolve(make_config(caption_margin_v=0))
        self.assertEqual(
            render._caption_margin_for_band(
                make_config(caption_margin_v=0, height=1280), style, None
            ),
            205,
        )


def _text_template(**overrides):
    """O formato Meme: faixa de texto preta no topo, vídeo reduzido, barra embaixo.

    As três frações somam 1.00 — a faixa de texto ocupa altura de verdade, e é
    por isso que o vídeo é 0.58 e não o 0.74 de antes dela existir.
    """
    fields = {
        "text": "POV: voce usou o formato de meme",
        "fraction": 0.16,
        "margin_top": 0.008,
        "margin_bottom": 0.008,
        "margin_left": 0.05,
        "margin_right": 0.05,
        "color": "black",
    }
    fields.update(overrides)
    return tpl.Template(
        name="meme",
        zones=(
            tpl.Zone(kind="text", **fields),
            tpl.Zone(kind="video", fraction=0.58),
            tpl.Zone(kind="image", fraction=0.26, source="id.png"),
        ),
    )


class TextZoneCaptionTests(unittest.TestCase):
    """A faixa de texto no ASS: o ``Style`` e o ``Dialogue`` ancorado.

    É aqui que o número do motor encontra o libass. O ``text_anchor`` que a
    prévia do painel desenha tem de ser exatamente o ``\\pos`` do arquivo — se
    divergirem, o usuário ajusta o texto na janela e ele sai em outro lugar no
    vídeo, sem nenhum erro no meio do caminho.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vc_render_txt_")

    def _ass(self, template, duration: float | None = None, **overrides) -> str:
        path = Path(self.tmp) / "captions.ass"
        render.build_captions(
            [make_word(10.0, 10.4, "ola.")],
            10.0,
            path,
            make_config(**overrides),
            template,
            duration,
        )
        return path.read_text(encoding="utf-8")

    def test_the_switch_the_panel_writes_reaches_the_ass(self):
        """O caminho inteiro: ``captions = false`` no .toml vira um ASS mudo.

        Cada peça já tem teste: o painel escreve a chave, ``apply_to_config``
        traduz, ``build_captions`` respeita. Nenhum deles sozinho prova que os
        três se encaixam — e é a concatenação que o render usa, não
        qualquer um deles isolado. Se um passo deixar de repassar o valor, o
        clipe sai com a legenda queimada e nenhum teste isolado acusa.
        """
        template = tpl.from_dict(
            {
                "name": "mudo",
                "caption_preset": "ultra-impact",
                "captions": False,
                "headline_seconds": 3.0,
                "zones": [
                    {
                        "kind": "text",
                        "fraction": 0.16,
                        "text": "POV: voce viralizou",
                    },
                    {"kind": "video", "fraction": 0.84},
                ],
            }
        )
        config = tpl.apply_to_config(make_config(), template)
        path = render.build_captions(
            [make_word(10.0, 10.4, "ola.")],
            10.0,
            Path(self.tmp) / "captions.ass",
            config,
            template,
            5.0,
        )
        body = path.read_text(encoding="utf-8")
        # A palavra falada NÃO sai...
        self.assertNotIn(",Default,,", body)
        # ... mas o gancho e a faixa de texto saem: o preset do .toml continua
        # valendo para os dois, e desligar a legenda não é escolher outro visual.
        self.assertIn(",Headline,,", body)
        self.assertIn(",Zona1,,", body)
        self.assertIn("POV", body.upper())

    def test_the_style_is_named_after_the_zone_and_wears_the_caption_font(self):
        # O nome é o que o `Dialogue` procura: repetido ou trocado, o libass
        # aborta com "Unable to find style" e o clipe sai sem a faixa. A fonte
        # vem do preset das legendas porque um banner com outra família lê como
        # dois desenhos brigando no mesmo quadro.
        body = self._ass(_text_template())
        config = make_config()
        font = render.caption_presets.resolve(config).font
        size = round(config.height * 0.05)  # o default de `text_size`
        self.assertIn(f"Style: Zona1,{font},{size},&H00FFFFFF,", body)
        self.assertIn(",Zona1,,", body)

    def test_the_position_is_the_anchor_the_engine_resolved(self):
        body = self._ass(_text_template())
        config = make_config()
        band = tpl.plan_bands(_text_template(), config.width, config.height)[0]
        x, y, an = tpl.text_anchor(band, band.zone, config.width, config.height)
        self.assertIn(rf"{{\an{an}\pos({x},{y})}}", body)

    def test_the_colour_is_byte_reversed_for_ass(self):
        # ASS guarda a cor invertida (BBGGRR) com o alfa na frente: escrever
        # `#facc15` direto daria azul — e nenhum erro.
        self.assertIn(",&H0015CCFA,", self._ass(_text_template(text_color="#facc15")))
        # Quem já escreveu sintaxe ASS passa direto, sem ser "consertado".
        self.assertIn(",&H00FF00FF,", self._ass(_text_template(text_color="&H00FF00FF")))

    def test_the_banner_keeps_the_authors_line_breaks(self):
        # `\N` é a quebra dura do ASS. Numa legenda o `\n` vira espaço de
        # propósito (a linha é remontada por tempo); num banner não — o usuário
        # quebrou a linha no campo de texto e espera duas linhas no vídeo.
        body = self._ass(_text_template(text="POV: voce usou\no formato de meme"))
        self.assertIn(r"POV: voce usou\No formato de meme", body)

    def test_uppercase_is_applied_at_burn_time(self):
        body = self._ass(_text_template(text="pov: olha isso", text_uppercase=True))
        self.assertIn("POV: OLHA ISSO", body)
        # Desligado, o texto sai como foi escrito — inclusive as minúsculas.
        self.assertIn("pov: olha isso", self._ass(_text_template(text="pov: olha isso")))

    def test_the_outline_and_the_weight_reach_the_style(self):
        # Contorno é fração da ALTURA, a mesma medida da fonte: assim o peso
        # relativo das letras não muda quando o canvas troca de resolução.
        height = make_config().height
        body = self._ass(_text_template(text_outline=0.004, text_bold=False))
        self.assertIn(
            f"&H00000000,0,0,0,0,100,100,0,0,1,{round(height * 0.004)},0,5,0,0,0,1", body
        )
        self.assertIn(
            "&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
            self._ass(_text_template()),
        )

    def test_the_banner_comes_before_the_captions_on_the_same_layer(self):
        # Numa mesma camada o libass desenha os eventos POSTERIORES por cima. O
        # banner é emitido antes para que, se os dois se cruzarem, a palavra
        # falada ganhe — ela é o conteúdo, o banner é a moldura.
        body = self._ass(_text_template())
        line = [row for row in body.splitlines() if ",Zona1,," in row][0]
        self.assertTrue(line.startswith("Dialogue: 0,"), line)
        self.assertLess(body.index(",Zona1,,"), body.index(",Default,,"))

    def test_the_banner_runs_to_the_end_of_the_clip(self):
        # Ele é parte da composição, não um momento: aparece no primeiro quadro
        # e fica. Com a duração em mãos, o fim é o número real; sem ela, o evento
        # fica aberto e o libass o corta no fim do vídeo — a mesma resposta.
        self.assertIn("0:00:00.00,0:00:12.50,Zona1", self._ass(_text_template(), 12.5))
        self.assertIn(f"0:00:00.00,{render.OPEN_END},Zona1", self._ass(_text_template()))

    def test_a_template_without_a_text_zone_adds_nothing(self):
        # O caminho sem zona de texto tem de continuar o de antes: o placeholder
        # `{zone_styles}` vazio não pode deixar uma linha em branco a mais no
        # cabeçalho. O libass tolera, mas o arquivo deixaria de ser comparável
        # com o que a versão anterior escrevia.
        body = self._ass(tpl.SPLIT_CARD)
        self.assertNotIn("Zona", body)
        self.assertIn("Style: Headline,", body)
        self.assertNotIn("\n\n\n", body.split("[Events]", 1)[0])

    def test_two_text_zones_get_two_styles(self):
        # Duas faixas de texto no mesmo template é o que o editor permite ao
        # acrescentar zona. Nomes repetidos fariam a segunda herdar a primeira e
        # o libass abortaria.
        two = tpl.Template(
            name="dois",
            zones=(
                tpl.Zone(kind="text", fraction=0.15, text="gancho do topo"),
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="text", fraction=0.25, text="chamada de baixo"),
            ),
        )
        body = self._ass(two)
        self.assertIn("Style: Zona1,", body)
        self.assertIn("Style: Zona3,", body)
        self.assertIn("gancho do topo", body)
        self.assertIn("chamada de baixo", body)


class TemplateComposeIntegrationTests(unittest.TestCase):
    """``render_clip`` must build a zone graph only when a template needs one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_render_compose_"))
        self.commands: list[list[str]] = []

    def _render(self, template=None, **overrides):
        config = make_config(width=1080, height=1920, **overrides)

        def fake_run_streaming(command, **kwargs):
            self.commands.append(list(command))
            # ffmpeg writes the temp file the caller then moves into place.
            work = Path(kwargs.get("cwd") or self.tmp)
            work.mkdir(parents=True, exist_ok=True)
            (work / f"render{Path(self.tmp / 'out.mp4').suffix}").write_bytes(b"")

        with patch.object(render.util, "run_streaming", side_effect=fake_run_streaming), \
             patch.object(render.util, "probe_video_size", return_value=(1920, 1080)), \
             patch.object(render.util, "probe_duration", return_value=10.0), \
             patch.object(render, "has_audio_stream", return_value=False), \
             patch.object(render, "_focus_crop_x", return_value=None):
            render.render_clip(
                source=self.tmp / "src.mp4",
                destination=self.tmp / "out.mp4",
                clip_start=10.0,
                clip_end=20.0,
                config=config,
                ffmpeg="ffmpeg",
                ffprobe="ffprobe",
                words=[],
                work_dir=self.tmp / "work",
                template=template,
            )
        return self.commands[-1]

    def _graph(self, command) -> str:
        return command[command.index("-filter_complex") + 1]

    def test_no_template_uses_the_plain_layout(self):
        graph = self._graph(self._render())
        self.assertIn("scale=1080:1920", graph)
        self.assertNotIn("overlay", graph)

    def test_full_frame_template_keeps_the_plain_path(self):
        graph = self._graph(self._render(template=tpl.FULL_FRAME))
        # A single video zone needs no composer: captions-only templates must
        # not pay the cost of an extra overlay pass.
        self.assertNotIn("[z0s]", graph)

    def test_split_template_composes_zones(self):
        graph = self._graph(self._render(template=tpl.SPLIT_CARD))
        self.assertIn("overlay", graph)
        # The video band is 62% of 1920, so zones must be scaled to a 1190px band.
        self.assertIn("1190", graph)

    def test_split_template_adds_a_still_input(self):
        command = self._render(template=tpl.SPLIT_CARD)
        # One -i for the clip, one more for the extracted frame.
        self.assertEqual(command.count("-i"), 2)

    def test_composed_graph_has_balanced_labels(self):
        graph = self._graph(self._render(template=tpl.SPLIT_CARD))
        self.assertEqual(graph.count("["), graph.count("]"))

    def test_an_image_zone_without_an_asset_falls_back_to_the_clip(self):
        """Sem arquivo, o input da zona aponta para o PRÓPRIO clipe.

        ``Path("")`` é ``.``, e ``.`` existe — sem a distinção, o diretório de
        saída inteiro entraria no ffmpeg como input e o render morreria com
        "Is a directory", em vez de degradar, que é o que o motor promete e o que
        faz um formato da galeria abrir válido antes de o usuário ter o asset.
        """
        template = tpl.Template(
            name="sem-asset",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="image", fraction=0.4),
            ),
        )
        command = self._render(template)
        inputs = [command[i + 1] for i, item in enumerate(command) if item == "-i"]
        self.assertEqual(inputs, [str((self.tmp / "src.mp4").resolve())] * 2)


if __name__ == "__main__":
    unittest.main()
