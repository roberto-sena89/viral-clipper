"""Unit tests for :mod:`viralclipper.template`.

Everything asserted here is decided before ffmpeg runs: the band geometry and
the filtergraph string. That keeps the whole suite offline and lets a broken
composition be caught without encoding a frame.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from viralclipper import template as tpl
from viralclipper.util import ClipperError

from ._fixtures import make_config


class ZoneValidationTests(unittest.TestCase):
    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.Zone(kind="hologram", fraction=1.0).validate(1)
        self.assertIn("hologram", str(ctx.exception))

    def test_fraction_outside_the_band_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="video", fraction=0.005).validate(1)
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="video", fraction=1.5).validate(1)

    def test_image_zone_without_a_source_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.Zone(kind="image", fraction=0.4).validate(2)
        self.assertIn("source", str(ctx.exception))

    def test_margins_may_not_swallow_the_band(self):
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="video", fraction=1.0, margin_top=0.6, margin_bottom=0.5).validate(1)


class TemplateValidationTests(unittest.TestCase):
    def test_zones_must_sum_to_one(self):
        broken = tpl.Template(
            name="broken",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="frame", fraction=0.3),
            ),
        )
        with self.assertRaises(ClipperError) as ctx:
            broken.validate()
        self.assertIn("0.9000", str(ctx.exception))

    def test_float_rounding_does_not_break_the_sum(self):
        # 0.55 + 0.45 is not exactly 1.0 in binary floating point; the check
        # has to tolerate that or a hand-written template fails for no reason.
        tpl.Template(
            name="ok",
            zones=(
                tpl.Zone(kind="video", fraction=0.55),
                tpl.Zone(kind="frame", fraction=0.45),
            ),
        ).validate()

    def test_caption_zone_must_come_last(self):
        bad = tpl.Template(
            name="bad",
            zones=(
                tpl.Zone(kind="captions", fraction=0.0),
                tpl.Zone(kind="video", fraction=1.0),
            ),
        )
        with self.assertRaises(ClipperError) as ctx:
            bad.validate()
        self.assertIn("ultima", str(ctx.exception))

    def test_only_one_video_zone(self):
        bad = tpl.Template(
            name="bad",
            zones=(
                tpl.Zone(kind="video", fraction=0.5),
                tpl.Zone(kind="video", fraction=0.5),
            ),
        )
        with self.assertRaises(ClipperError):
            bad.validate()

    def test_empty_template_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.Template(name="empty", zones=()).validate()


class PlanBandsTests(unittest.TestCase):
    def test_full_frame_is_exactly_the_canvas(self):
        bands = tpl.plan_bands(tpl.FULL_FRAME, 1080, 1920)
        self.assertEqual(len(bands), 1)
        band = bands[0]
        self.assertEqual((band.y, band.height), (0, 1920))
        self.assertEqual((band.inner_width, band.inner_height), (1080, 1920))

    def test_bands_tile_the_canvas_without_a_seam(self):
        for height in (1920, 1080, 1280, 999):
            bands = [b for b in tpl.plan_bands(tpl.SPLIT_CARD, 1080, height) if b.kind != "captions"]
            covered = 0
            for band in bands:
                self.assertEqual(band.y, covered, f"gap before {band.kind} at h={height}")
                covered += band.height
            self.assertEqual(covered, height, f"bands do not reach the bottom at h={height}")

    def test_a_non_integral_split_still_tiles(self):
        # 1080 * 0.62 does not round cleanly; the leftover pixel has to land
        # somewhere rather than becoming a one-pixel black seam.
        odd = tpl.Template(
            name="odd",
            zones=(
                tpl.Zone(kind="video", fraction=0.333),
                tpl.Zone(kind="frame", fraction=0.333),
                tpl.Zone(kind="solid", fraction=0.334),
            ),
        )
        bands = tpl.plan_bands(odd, 1080, 1920)
        self.assertEqual(sum(b.height for b in bands), 1920)
        cursor = 0
        for band in bands:
            self.assertEqual(band.y, cursor)
            cursor += band.height

    def test_margins_shrink_the_inner_rectangle_in_pixels(self):
        band = tpl.plan_bands(tpl.SPLIT_CARD, 1080, 1920)[1]
        # margin_left 0.03 of 1080 = 32px, and the same on the right.
        self.assertEqual(band.inner_x, 32)
        self.assertEqual(band.inner_width, 1080 - 64)

    def test_captions_band_is_full_canvas_and_last(self):
        bands = tpl.plan_bands(tpl.SPLIT_CARD, 1080, 1920)
        self.assertEqual(bands[-1].kind, "captions")
        self.assertEqual(bands[-1].height, 1920)


class ComposeTests(unittest.TestCase):
    def test_full_frame_needs_no_overlay(self):
        graph, label = tpl.compose(tpl.FULL_FRAME, 1080, 1920)
        self.assertNotIn("overlay", graph)
        self.assertEqual(label, "[c0]")

    def test_split_card_stacks_video_and_still(self):
        graph, label = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        self.assertIn("overlay", graph)
        # The 'frame' zone draws a still extracted from the clip, which is a
        # separate input, so the graph must reference [1:v] rather than nesting
        # the running composite inside its own poster.
        self.assertIn("[1:v]", graph)
        self.assertEqual(label, "[c1]")

    def test_a_still_zone_never_reads_the_running_composite(self):
        graph, _ = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        # The frame band's scale stage must be fed by the still input, not [c0].
        self.assertNotIn("[c0]scale=", graph)

    def test_image_zone_consumes_the_next_input(self):
        with_image = tpl.Template(
            name="with-image",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="image", fraction=0.4, source="logo.png"),
            ),
        )
        graph, _ = tpl.compose(with_image, 1080, 1920)
        self.assertIn("[1:v]", graph)

    def test_two_image_zones_use_two_inputs_in_order(self):
        with_two = tpl.Template(
            name="two-images",
            zones=(
                tpl.Zone(kind="video", fraction=0.5),
                tpl.Zone(kind="image", fraction=0.25, source="a.png"),
                tpl.Zone(kind="image", fraction=0.25, source="b.png"),
            ),
        )
        graph, _ = tpl.compose(with_two, 1080, 1920)
        self.assertIn("[1:v]", graph)
        self.assertIn("[2:v]", graph)

    def test_contain_fits_and_pads_cover_fills(self):
        contain = tpl.Template(
            name="contain",
            zones=(tpl.Zone(kind="image", fraction=1.0, source="l.png", fit="contain"),),
        )
        graph, _ = tpl.compose(contain, 1080, 1920)
        self.assertIn("force_original_aspect_ratio=decrease", graph)
        self.assertIn("pad=", graph)

        cover = tpl.Template(
            name="cover",
            zones=(tpl.Zone(kind="image", fraction=1.0, source="l.png", fit="cover"),),
        )
        graph, _ = tpl.compose(cover, 1080, 1920)
        self.assertIn("force_original_aspect_ratio=increase", graph)

    def test_rounded_zone_builds_a_mask(self):
        rounded = tpl.Template(
            name="rounded",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="image", fraction=0.4, source="c.png", corner_radius=0.04),
            ),
        )
        graph, _ = tpl.compose(rounded, 1080, 1920)
        self.assertIn("alphamerge", graph)
        self.assertIn("gblur", graph)
        self.assertIn("lutyuv", graph)

    def test_solid_zone_paints_a_colour(self):
        solid = tpl.Template(
            name="solid",
            zones=(
                tpl.Zone(kind="video", fraction=0.8),
                tpl.Zone(kind="solid", fraction=0.2, color="0x112233"),
            ),
        )
        graph, _ = tpl.compose(solid, 1080, 1920)
        self.assertIn("color=c=0x112233", graph)

    def test_caption_only_template_is_a_passthrough(self):
        only = tpl.Template(name="caps", zones=(tpl.Zone(kind="captions", fraction=0.0),))
        graph, label = tpl.compose(only, 1080, 1920)
        self.assertIn("null", graph)
        self.assertEqual(label, "[c0]")

    def test_the_base_plate_is_an_infinite_colour_source(self):
        # A one-frame plate (``d=1``) caps overlay at a single frame and
        # truncates the entire clip to 1 frame. The plate must run indefinitely
        # so ffmpeg's -t stays the only length authority.
        graph, _ = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        plate = [p for p in graph.split(";") if p.startswith("color=") and "s=1080x1920" in p]
        self.assertEqual(len(plate), 1)
        self.assertNotIn(":d=", plate[0])

    def test_overlay_does_not_use_shortest(self):
        # shortest=1 would end the composite as soon as the shorter input stops
        # (the still), producing a clip that is one frame long.
        graph, _ = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        self.assertNotIn("shortest=1", graph)

    def test_graph_brackets_are_balanced(self):
        for name in tpl.BUILTIN:
            graph, _ = tpl.compose(tpl.BUILTIN[name], 1080, 1920)
            self.assertEqual(graph.count("["), graph.count("]"), f"unbalanced in {name}")


class FromDictTests(unittest.TestCase):
    def _payload(self, **overrides):
        data = {
            "name": "meu-template",
            "zones": [
                {"kind": "video", "fraction": 0.6},
                {"kind": "frame", "fraction": 0.4},
            ],
        }
        data.update(overrides)
        return data

    def test_a_valid_mapping_becomes_a_template(self):
        parsed = tpl.from_dict(self._payload())
        self.assertEqual(parsed.name, "meu-template")
        self.assertEqual(len(parsed.zones), 2)
        self.assertEqual(parsed.zones[0].kind, "video")

    def test_unknown_top_level_key_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.from_dict(self._payload(zones=[{"kind": "video", "fraction": 1.0}], colour="red"))
        self.assertIn("colour", str(ctx.exception))

    def test_unknown_zone_key_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.from_dict(
                self._payload(zones=[{"kind": "video", "fraction": 1.0, "blur": 3}])
            )
        self.assertIn("blur", str(ctx.exception))

    def test_zone_without_kind_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.from_dict(self._payload(zones=[{"fraction": 1.0}]))

    def test_zone_without_fraction_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.from_dict(self._payload(zones=[{"kind": "video"}]))
        self.assertIn("fraction", str(ctx.exception))

    def test_empty_zone_list_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.from_dict(self._payload(zones=[]))

    def test_template_name_falls_back_to_the_stem(self):
        data = self._payload(zones=[{"kind": "video", "fraction": 1.0}])
        del data["name"]
        self.assertEqual(tpl.from_dict(data, name="do_arquivo").name, "do_arquivo")


class TemplateFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_template_"))

    def test_toml_round_trip(self):
        path = self.tmp / "canal.toml"
        path.write_text(
            "name = \"canal\"\n"
            "caption_preset = \"neon\"\n"
            "[[zones]]\n"
            "kind = \"video\"\n"
            "fraction = 0.62\n"
            "[[zones]]\n"
            "kind = \"frame\"\n"
            "fraction = 0.38\n"
            "margin_left = 0.03\n",
            encoding="utf-8",
        )
        loaded = tpl.load_template(path)
        self.assertEqual(loaded.name, "canal")
        self.assertEqual(loaded.caption_preset, "neon")
        self.assertEqual(len(loaded.zones), 2)
        self.assertAlmostEqual(loaded.zones[1].margin_left, 0.03)

    def test_yaml_round_trip(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML nao instalado")
        path = self.tmp / "canal.yaml"
        path.write_text(
            "name: canal\n"
            "zones:\n"
            "  - kind: video\n"
            "    fraction: 1.0\n",
            encoding="utf-8",
        )
        loaded = tpl.load_template(path)
        self.assertEqual(loaded.zones[0].kind, "video")

    def test_missing_file_raises(self):
        with self.assertRaises(ClipperError):
            tpl.load_template(self.tmp / "nao-existe.toml")

    def test_unknown_extension_raises(self):
        path = self.tmp / "canal.ini"
        path.write_text("name = x", encoding="utf-8")
        with self.assertRaises(ClipperError):
            tpl.load_template(path)


class GetTemplateTests(unittest.TestCase):
    def test_builtins_load_by_name(self):
        for name in tpl.BUILTIN:
            self.assertEqual(tpl.get_template(name).name, name)

    def test_lookup_is_case_and_space_tolerant(self):
        self.assertEqual(tpl.get_template("  Split-Card ").name, "split-card")

    def test_unknown_name_lists_the_choices(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.get_template("nao-existe")
        message = str(ctx.exception)
        self.assertIn("full-frame", message)
        self.assertIn("split-card", message)


class ExpandVariationsTests(unittest.TestCase):
    def test_no_axes_returns_the_base_template(self):
        self.assertEqual(tpl.expand_variations(tpl.SPLIT_CARD), [tpl.SPLIT_CARD])

    def test_one_preset_one_layout_is_a_single_variant(self):
        variants = tpl.expand_variations(
            tpl.SPLIT_CARD, caption_presets=["neon"], layouts=["focus"]
        )
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].caption_preset, "neon")
        self.assertEqual(variants[0].layout, "focus")
        self.assertEqual(variants[0].name, "split-card__neon-focus")

    def test_axes_multiply(self):
        variants = tpl.expand_variations(
            tpl.SPLIT_CARD,
            caption_presets=["neon", "karaoke", "minimal"],
            layouts=["focus", "blur"],
        )
        self.assertEqual(len(variants), 6)
        self.assertEqual(len({v.name for v in variants}), 6)

    def test_variants_inherit_the_base_zones(self):
        variants = tpl.expand_variations(tpl.SPLIT_CARD, caption_presets=["neon"])
        self.assertEqual(variants[0].zones, tpl.SPLIT_CARD.zones)

    def test_blank_axis_entries_are_ignored(self):
        # Blank entries are dropped; with every entry dropped the axis falls
        # back to the template's own value, so exactly one variant remains -
        # the base composition re-encoded, not a duplicate.
        variants = tpl.expand_variations(
            tpl.SPLIT_CARD, caption_presets=["neon", "", "  "]
        )
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].caption_preset, "neon")

    def test_all_blank_axes_fall_back_to_the_base_values(self):
        base = tpl.Template(
            name="base",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_preset="neon",
            layout="focus",
        )
        variants = tpl.expand_variations(base, caption_presets=["", " "], layouts=[""])
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].caption_preset, "neon")
        self.assertEqual(variants[0].layout, "focus")


class ApplyToConfigTests(unittest.TestCase):
    def test_none_fields_leave_the_config_alone(self):
        config = make_config(caption_preset="karaoke", layout="focus")
        quiet = tpl.Template(name="quiet", zones=(tpl.Zone(kind="video", fraction=1.0),))
        result = tpl.apply_to_config(config, quiet)
        self.assertIs(result, config)

    def test_set_fields_win_over_the_config(self):
        config = make_config(caption_preset="karaoke", layout="focus")
        loud = tpl.Template(
            name="loud",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_preset="ultra-impact",
            layout="blur",
            progress_bar=True,
        )
        result = tpl.apply_to_config(config, loud)
        self.assertEqual(result.caption_preset, "ultra-impact")
        self.assertEqual(result.layout, "blur")
        self.assertTrue(result.progress_bar)
        # Untouched fields survive the replace.
        self.assertEqual(result.url, config.url)

    def test_the_original_config_is_not_mutated(self):
        config = make_config(caption_preset="karaoke")
        loud = tpl.Template(
            name="loud",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_preset="neon",
        )
        tpl.apply_to_config(config, loud)
        self.assertEqual(config.caption_preset, "karaoke")


class DescribeTests(unittest.TestCase):
    def test_description_mentions_every_band(self):
        text = tpl.describe(tpl.SPLIT_CARD, 1080, 1920)
        self.assertIn("video", text)
        self.assertIn("frame", text)
        self.assertIn("captions", text)
        self.assertIn("1080x1920", text)


if __name__ == "__main__":
    unittest.main()
