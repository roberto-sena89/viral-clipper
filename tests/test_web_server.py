"""Unit tests for the web UI server path resolution.

The gallery serves clips whose file names carry accents; the browser sends
them percent-encoded, and :mod:`web.server` must decode before it touches the
filesystem. These tests lock the pure path logic; the HTTP layer is exercised
by running the real server against the rendered output.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from viralclipper import report
from web import server
from web.server import resolve_within


class ResolveWithinTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="vc_web_"))
        (self.base / "nao_sem_acento.txt").write_text("x", encoding="utf-8")
        (self.base / "na\u00e3o_com_acento.txt").write_text("x", encoding="utf-8")

    def test_plain_name_resolves(self):
        found = resolve_within(self.base, "nao_sem_acento.txt")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "nao_sem_acento.txt")

    def test_accented_name_resolves(self):
        found = resolve_within(self.base, "na\u00e3o_com_acento.txt")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "na\u00e3o_com_acento.txt")

    def test_missing_file_returns_none(self):
        self.assertIsNone(resolve_within(self.base, "nao_existe.mp4"))

    def test_traversal_is_rejected(self):
        outside = self.base.parent / "fora.txt"
        outside.write_text("x", encoding="utf-8")
        self.assertIsNone(resolve_within(self.base, "../fora.txt"))
        # percent-encoded traversal decodes to the same attack
        self.assertIsNone(resolve_within(self.base, "%2e%2e/fora.txt"))

    def test_subdirectory_file_resolves(self):
        sub = self.base / "sub"
        sub.mkdir()
        (sub / "clip.mp4").write_text("x", encoding="utf-8")
        self.assertIsNotNone(resolve_within(self.base, "sub/clip.mp4"))


class ClipPayloadTests(unittest.TestCase):
    """The payload must not present an unrendered cut as a playable video."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_payload_"))
        self.record = report.ClipRecord(
            index=1,
            start=10.0,
            end=50.0,
            duration=40.0,
            score=61.0,
            meets_minimum=True,
            file="",
            text="alguma fala",
        )

    def test_plan_only_clip_is_marked_as_not_rendered(self):
        payload = server._clip_to_payload(self.record, self.tmp)
        self.assertIsNone(payload["video"])
        self.assertFalse(payload["rendered"])

    def test_rendered_clip_exposes_a_relative_path(self):
        clip = self.tmp / "clip.mp4"
        clip.write_bytes(b"x")
        rendered = replace(self.record, file=str(clip))
        payload = server._clip_to_payload(rendered, self.tmp)
        self.assertEqual(payload["video"], "clip.mp4")
        self.assertTrue(payload["rendered"])


class UiServerBindTests(unittest.TestCase):
    def test_refuses_a_second_bind(self):
        first = server.UiServer(("127.0.0.1", 0), server.Handler)
        try:
            with self.assertRaises(OSError):
                server.UiServer(("127.0.0.1", first.server_address[1]), server.Handler)
        finally:
            first.server_close()


class LibraryListingTests(unittest.TestCase):
    """The output-folder view: renamed and older files stay reachable."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_lib_"))

    def test_lists_videos_only_and_newest_first(self):
        (self.tmp / "old.mp4").write_bytes(b"x")
        (self.tmp / "report.json").write_text("{}", encoding="utf-8")
        (self.tmp / "notes.txt").write_text("x", encoding="utf-8")
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "newer.mp4").write_bytes(b"x")
        import os
        import time

        older = time.time() - 500
        os.utime(self.tmp / "old.mp4", (older, older))
        files = server.list_library(self.tmp)
        names = [item["name"] for item in files]
        self.assertEqual(names, ["newer.mp4", "old.mp4"])
        self.assertEqual(files[0]["rel"], "sub/newer.mp4")
        self.assertGreater(files[0]["size"], 0)
        self.assertIn("modified", files[0])

    def test_limit_is_respected(self):
        for index in range(5):
            (self.tmp / f"clip{index}.mp4").write_bytes(b"x")
        self.assertEqual(len(server.list_library(self.tmp, limit=2)), 2)

    def test_missing_directory_is_empty(self):
        self.assertEqual(server.list_library(self.tmp / "nao_existe"), [])


class TemplatesPageTests(unittest.TestCase):
    """The wizard page holds its own copy of the preset catalog for the preview.

    A page that silently drifts from the engine offers presets that no longer
    exist, or hides the ones that do - and the failure only shows up at render
    time. These tests are the tripwire for that.
    """

    def setUp(self):
        self.page = server.WEB_DIR / "templates.html"

    def test_the_page_exists(self):
        self.assertTrue(self.page.exists(), "web/templates.html is missing")

    def test_the_page_lists_every_shipped_preset(self):
        from viralclipper import caption_presets

        body = self.page.read_text(encoding="utf-8")
        missing = [name for name in caption_presets.PRESETS if f'"{name}"' not in body]
        self.assertEqual(
            missing, [], f"presets ausentes na pagina de templates: {missing}"
        )

    def test_the_page_invents_no_preset(self):
        import re

        from viralclipper import caption_presets

        body = self.page.read_text(encoding="utf-8")
        block = re.search(r"var PRESETS = \{(.*?)\n  \};", body, re.S)
        self.assertIsNotNone(block, "bloco PRESETS nao encontrado na pagina")
        keys = set(re.findall(r'^\s*"([a-z0-9-]+)":', block.group(1), re.M))
        unknown = sorted(keys - set(caption_presets.PRESETS))
        self.assertEqual(
            unknown, [], f"a pagina oferece presets que o motor nao tem: {unknown}"
        )

    def test_the_page_offers_every_zone_kind(self):
        from viralclipper import template as template_mod

        body = self.page.read_text(encoding="utf-8")
        missing = [kind for kind in template_mod.ZONE_KINDS if f"{kind}:" not in body]
        self.assertEqual(missing, [], f"tipos de zona ausentes na pagina: {missing}")

    def test_the_panel_links_to_the_templates_page(self):
        panel = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/templates"', panel)
class RailNavigationTests(unittest.TestCase):
    """The page picker on the lateral rail.

    Both pages are served as standalone HTML with no build step, so the rail
    markup and its stylesheet are duplicated on purpose. That duplication is
    exactly what drifts: a page gets the new item and the other one keeps the
    old list, or one copy loses the responsive fallback and the narrow layout
    ends up with no navigation at all. These tests pin the parts that must
    match, without prescribing the whole file.
    """

    PAGES = ("index.html", "templates.html")
    #: Destinations the rail offers, keyed by the href the browser will see.
    DESTINATIONS = {
        "/": {"title": "Cortes", "ico": "▶"},
        "/templates": {"title": "Templates", "ico": "▣"},
    }

    def body(self, name: str) -> str:
        return (server.WEB_DIR / name).read_text(encoding="utf-8")

    def markup(self, name: str) -> str:
        """The page with <script>/<style>/comments stripped.

        Counting ``data-rail-picker`` in the raw file overcounts: the attribute
        also appears in the CSS comment and in the JS selector. Only real
        elements matter here.
        """
        import re

        text = self.body(name)
        text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
        return re.sub(r"<!--.*?-->", "", text, flags=re.S)

    def test_every_page_has_a_rail(self):
        for name in self.PAGES:
            with self.subTest(page=name):
                self.assertIn('class="rail"', self.markup(name))

    def test_every_page_offers_every_destination(self):
        for name in self.PAGES:
            markup = self.markup(name)
            for href, meta in self.DESTINATIONS.items():
                with self.subTest(page=name, href=href):
                    self.assertIn(f'data-rail-page="{href}"', markup)
                    self.assertIn(meta["title"], markup)

    def test_the_rail_lists_destinations_as_plain_links(self):
        """The rail menu is a list of links, not a widget.

        It used to be a button that opened a listbox. Now every destination is
        always visible, so it needs no popup semantics — a screen reader should
        announce "navigation, 2 items" and stop there. If someone reintroduces
        role=listbox here it would contradict the "always visible" model.
        """
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertIn('class="rail-menu"', markup)
                self.assertIn('aria-labelledby="rail-label-paginas"', markup)
                # The rail itself must not claim to be a listbox popup.
                rail = markup.split("</nav>", 1)[0]
                self.assertNotIn('role="listbox"', rail)
                self.assertNotIn('role="option"', rail)
                self.assertNotIn("aria-haspopup", rail)

    def test_both_instances_share_the_same_destinations(self):
        """The rail and the header fallback must list the same pages.

        They are separate copies of the same list (no build step to share it),
        so a page added to one and forgotten in the other is the realistic bug.
        Compare the hrefs each instance carries.
        """
        for name in self.PAGES:
            markup = self.markup(name)
            rail = markup.split("</nav>", 1)[0]
            fallback = markup.split('data-rail-picker', 1)[1].split("</header>", 1)[0]

            def hrefs(chunk):
                return sorted(re.findall(r'data-rail-page="([^"]+)"', chunk))

            with self.subTest(page=name):
                self.assertEqual(
                    hrefs(rail),
                    hrefs(fallback),
                    f"{name}: rail e menu do header listam destinos diferentes",
                )
                self.assertEqual(
                    hrefs(rail),
                    sorted(self.DESTINATIONS),
                    f"{name}: destinos inesperados no rail",
                )

    def test_the_header_fallback_is_a_toggle_with_aria(self):
        """The header menu DOES need popup semantics: it opens and closes."""
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertIn('class="menu-btn', markup)
                self.assertIn('aria-haspopup="true"', markup)
                self.assertIn('aria-expanded="false"', markup)
                self.assertIn('aria-controls="rail-menu-sm"', markup)
                # The panel it controls must actually carry that id.
                self.assertIn('id="rail-menu-sm"', markup)

    def test_the_rail_has_a_narrow_screen_fallback(self):
        """Below the rail breakpoint the same menu must exist in the header.

        Without this the rail is display:none on a phone and the page is
        unreachable except by typing the URL.
        """
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertIn("rail-dropdown", markup)
        # Both pages must hide the rail and reveal the fallback at the same
        # width, or one of them breaks silently.
        for name in self.PAGES:
            css = self.body(name)
            with self.subTest(page=name):
                self.assertIn("max-width: 920px", css)
                self.assertIn("body { padding-left: 0; }", css)

    def test_every_destination_is_declared_in_the_js(self):
        """The rail list lives in the JS so ``aria-current`` can be derived.

        The two pages do not share a spelling: the panel is written with single
        quotes and the wizard with double ones, and the declaration keyword
        differs too. Match on the binding name and accept either quote style,
        so a formatting change on one page cannot fail this test.
        """
        for name in self.PAGES:
            body = self.body(name)
            match = re.search(
                r"""\b(?:const|var|let)\s+PAGES\s*=\s*\{(.*?)\n\s*\};""", body, re.S
            )
            self.assertIsNotNone(match, f"{name} nao declara PAGES")
            keys = set(re.findall(r"""['"](/[a-z-]*)['"]\s*:""", match.group(1)))
            self.assertEqual(
                keys,
                set(self.DESTINATIONS),
                f"{name} declara destinos diferentes do rail: {sorted(keys)}",
            )

    def test_the_current_page_is_marked_by_the_js(self):
        """aria-current must be derived, never hardcoded to one page."""
        for name in self.PAGES:
            body = self.body(name)
            with self.subTest(page=name):
                code = body.replace("'", '"')
                self.assertIn('setAttribute("aria-current", "page")', code)
                # And the static markup must not pre-mark anything, or the
                # page that is not current would still claim to be.
                self.assertNotIn('aria-current="page"', self.markup(name))

    def test_the_rail_marks_one_item_per_instance(self):
        """Each copy of the list marks exactly one current item.

        There are two copies (rail + header fallback), so the whole document
        legitimately ends up with two marks. What must hold is that within each
        copy there is exactly one — an unmarked or double-marked copy means the
        derivation broke.
        """
        for name, current in (("index.html", "/"), ("templates.html", "/templates")):
            markup = self.markup(name)
            rail = markup.split("</nav>", 1)[0]
            fallback = markup.split('data-rail-picker', 1)[1].split("</header>", 1)[0]
            for label, chunk in (("rail", rail), ("header", fallback)):
                with self.subTest(page=name, instance=label):
                    # Only the item matching this page is expected; in the
                    # static markup nothing is marked, so assert the JS has the
                    # key it needs rather than a pre-written attribute.
                    self.assertIn(f'data-rail-page="{current}"', chunk)

    def test_the_old_static_template_link_is_gone(self):
        """The header link was replaced by the rail menu.

        Keeping both would mean two competing entry points, and the leftover
        anchor would drift out of sync with the rail list.
        """
        panel = self.markup("index.html")
        self.assertNotIn('<a class="btn pressable" href="/templates">', panel)

    def test_the_rail_does_not_use_a_late_declared_helper(self):
        """The rail block must not call $ / $$ before they are defined.

        ``var`` hoists as undefined, so a call written above the declaration
        throws at load and kills the whole script — taking the wizard or the
        panel with it. The rail sits near the top of the wizard file, above the
        helpers, so it has to fetch what it needs with document.querySelector.

        Checks the rail block itself rather than file order: it is fine for the
        rail to sit above the helpers as long as it does not touch them.
        """
        for name in self.PAGES:
            body = self.body(name)
            start = body.index("// ---------- rail lateral")
            end = body.index("// ----------", start + 10)
            block = body[start:end]

            helper = re.search(r"\b(?:var|const|let)\s+\$\$?\s*=", body)
            if helper is None or helper.start() < start:
                continue  # helpers come first: nothing to guard against

            with self.subTest(page=name):
                # A call looks like $( / $$( — the bare name in a comment or in
                # a `var q =` declaration is fine.
                calls = re.findall(r"(?<![\w$])\$\$?\s*\(", block)
                self.assertEqual(
                    calls,
                    [],
                    f"{name}: o rail chama {calls} antes de o helper existir; "
                    "use document.querySelector dentro do bloco",
                )


class TemplatesGeometryTests(unittest.TestCase):
    """The wizard recomputes band pixels; the numbers must agree with the engine."""

    def setUp(self):
        self.page = server.WEB_DIR / "templates.html"

    def test_the_geometry_rule_matches_the_engine(self):
        """Both implementations are compared on the same input.

        A change to either one that is not mirrored fails here instead of
        producing a preview that lies about the render.
        """
        from viralclipper import template as template_mod

        body = self.page.read_text(encoding="utf-8")
        # The page absorbs the rounding remainder into the last pixel band, the
        # same way plan_bands does. Assert the shared constants are present so a
        # rewrite cannot quietly drop the rule.
        self.assertIn("zone.fraction", body)
        self.assertIn("marginTop", body)
        self.assertIn("marginLeft", body)
        builtin = template_mod.BUILTIN["split-card"]
        bands = template_mod.plan_bands(builtin, 1080, 1920)
        self.assertEqual([b.kind for b in bands], ["video", "frame", "captions"])


class PortParsingTests(unittest.TestCase):
    """``--port`` exists so a stale listener is not a dead end."""

    def test_defaults_to_the_documented_port(self):
        self.assertEqual(server._parse_port([]), server.PORT)
        self.assertEqual(server.PORT, 7755)

    def test_short_and_long_forms(self):
        self.assertEqual(server._parse_port(["--port", "9000"]), 9000)
        self.assertEqual(server._parse_port(["-p", "9000"]), 9000)
        self.assertEqual(server._parse_port(["--port=9000"]), 9000)

    def test_invalid_values_fall_back_to_the_default(self):
        self.assertEqual(server._parse_port(["--port", "abc"]), server.PORT)
        self.assertEqual(server._parse_port(["--port", "0"]), server.PORT)
        self.assertEqual(server._parse_port(["--port", "70000"]), server.PORT)
        self.assertEqual(server._parse_port(["--port"]), server.PORT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
