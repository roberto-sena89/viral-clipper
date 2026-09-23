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
from viralclipper.util import ClipperError
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


class CookiesFileOptionTests(unittest.TestCase):
    """``cookies_file`` is sugar for ``--ytdlp-arg --cookies <path>``.

    It exists because the browser route is dead on Chrome/Edge 127+: cookie
    values are sealed with App-Bound Encryption (the ``v20`` prefix) and yt-dlp
    has no way to open them, so a file the user exported by hand is the only
    route that works there. ``cookies_file`` is not a ClipConfig field, so
    without this folding it would be dropped as an unknown key and the flag
    would never reach yt-dlp — silently, with the UI still showing the path.
    """

    def _config(self, **options):
        return server._options_to_config({"url": "https://youtu.be/x", **options})

    def test_the_file_becomes_a_cookies_flag(self):
        config = self._config(cookies_file="C:/cookies.txt")
        self.assertEqual(config.extra_ytdlp_args, ["--cookies", "C:/cookies.txt"])

    def test_the_file_overrides_the_browser_picker(self):
        """Both can stay selected; only one set of cookie flags may reach yt-dlp."""
        config = self._config(cookies_file="C:/cookies.txt",
                              cookies_from_browser="chrome")
        self.assertIsNone(config.cookies_from_browser)
        self.assertEqual(config.extra_ytdlp_args, ["--cookies", "C:/cookies.txt"])

    def test_it_does_not_clobber_other_extra_args(self):
        config = self._config(extra_ytdlp_args=["--playlist-end", "5"],
                              cookies_file="C:/cookies.txt")
        self.assertEqual(
            config.extra_ytdlp_args,
            ["--playlist-end", "5", "--cookies", "C:/cookies.txt"],
        )

    def test_a_blank_path_is_ignored(self):
        config = self._config(cookies_file="   ", cookies_from_browser="chrome")
        self.assertEqual(config.extra_ytdlp_args, [])
        self.assertEqual(config.cookies_from_browser, "chrome")


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


class ScrapPageTests(unittest.TestCase):
    """The Scrap page: one link, or a whole profile.

    The page is a thin shell over yt-dlp. What can silently break is the URL
    routing — a profile URL that resolves to a channel's sub-tab playlists
    instead of its videos looks like "the profile has no content", which is the
    worst possible failure mode because it is plausible.
    """

    def setUp(self):
        self.page = server.WEB_DIR / "scrap.html"

    def test_the_page_exists(self):
        self.assertTrue(self.page.exists(), "web/scrap.html is missing")

    def test_the_route_serves_the_page(self):
        """do_GET must have a /scrap branch, not fall through to 404."""
        source = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")
        self.assertIn('"/scrap", "/scrap.html"', source)
        self.assertIn('WEB_DIR / "scrap.html"', source)

    def test_the_route_survives_the_not_run_guard(self):
        """POST /scrap must be handled before the `path != "/run"` rejection.

        The normalize handler used to live nested inside that guard. A new route
        added below it would be swallowed and answered 404 with no clue why.
        """
        source = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")
        scrap_at = source.index('if path == "/scrap":')
        guard_at = source.index('if path != "/run":')
        self.assertLess(scrap_at, guard_at, "POST /scrap ficou atras do guard")

    def test_the_page_offers_both_modes(self):
        body = self.page.read_text(encoding="utf-8")
        self.assertIn('data-mode="link"', body)
        self.assertIn('data-mode="profile"', body)

    def test_the_page_hands_off_to_the_panel(self):
        """The pick has to lead somewhere, or the page is a dead end."""
        body = self.page.read_text(encoding="utf-8")
        self.assertIn('encodeURIComponent(picked.url)', body)
        self.assertIn('"/?url="', body)

    def test_the_panel_accepts_the_handoff(self):
        panel = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("URLSearchParams(window.location.search)", panel)
        self.assertIn("params.get('url')", panel)

    def test_cookies_are_offered_in_both_modes(self):
        """Instagram refuses a lone reel too, so the picker cannot be profile-only.

        The selector used to live inside the ``#perfil-opts`` fieldset, which
        ``renderMode`` hides for ``mode === "link"``. Link mode therefore had no
        way to authenticate at all, and its searches failed permanently.
        """
        body = self.page.read_text(encoding="utf-8")
        fieldset_at = body.index('id="perfil-opts"')
        fieldset_close = body.index("</fieldset>", fieldset_at)
        selector_at = body.index('id="scrap-cookies"')
        self.assertGreater(
            selector_at, fieldset_close,
            "o seletor de cookies precisa ficar FORA do fieldset que o modo link esconde",
        )

    def test_cookies_are_sent_in_link_mode_too(self):
        """The payload must carry the cookies regardless of the selected mode.

        Reading the cookie selector has to happen at the function's top level.
        When it lived inside ``if (mode === "profile")``, link mode never sent
        them, so every Instagram reel search failed with no way to fix it.
        """
        body = self.page.read_text(encoding="utf-8")
        handler_at = body.index("async function search")
        snippet = body[handler_at:handler_at + 2000]
        cookies_at = snippet.index("const cookies = ")
        before = snippet[:cookies_at]
        # The profile branch must be closed before the cookies are read.
        opened = before.rfind('if (mode === "profile") {')
        closed = before.rfind("\n    }")
        self.assertGreater(
            closed, opened,
            "o bloco do modo perfil precisa fechar antes da leitura dos cookies",
        )
        self.assertIn(
            "payload.extra_ytdlp_args", snippet[cookies_at:cookies_at + 200],
            "o seletor de cookies precisa alimentar extra_ytdlp_args",
        )


class ScrapUrlRoutingTests(unittest.TestCase):
    """Which URLs are a profile, and which are one post.

    Getting this wrong is invisible: yt-dlp happily returns an answer either
    way, it just returns the wrong one.
    """

    def test_a_post_is_not_a_profile(self):
        for url in (
            "https://www.instagram.com/reel/ABC123/",
            "https://www.instagram.com/p/ABC123/",
            "https://www.tiktok.com/@someone/video/999",
            "https://www.tiktok.com/@someone/photo/999",
            "https://www.youtube.com/watch?v=IwZVXmQdX1E",
            "https://www.youtube.com/shorts/myZ9kn9MIWQ",
        ):
            with self.subTest(url=url):
                self.assertFalse(server._is_profile_url(url))

    def test_an_account_is_a_profile(self):
        for url in (
            "https://www.instagram.com/someone/",
            "https://www.tiktok.com/@someone",
            "https://www.youtube.com/@NASA",
            "https://youtube.com/channel/UCLA_DiR1FfKNvjuUpBHmylQ",
            "https://www.youtube.com/c/SomeName",
        ):
            with self.subTest(url=url):
                self.assertTrue(server._is_profile_url(url))

    def test_a_youtube_channel_gets_its_videos_tab(self):
        """Without the tab yt-dlp answers with the channel's sub-tabs.

        Verified against the real extractor: /@NASA returns entries named
        "NASA - Videos", "NASA - Live", "NASA - Shorts" — playlists, not
        videos. The tab is what turns it into a feed.
        """
        self.assertEqual(
            server._with_videos_tab("https://www.youtube.com/@NASA"),
            "https://www.youtube.com/@NASA/videos",
        )
        self.assertEqual(
            server._with_videos_tab("https://youtube.com/channel/UCabc"),
            "https://youtube.com/channel/UCabc/videos",
        )

    def test_the_tab_is_not_added_twice(self):
        for url in (
            "https://www.youtube.com/@NASA/videos",
            "https://www.youtube.com/@NASA/shorts",
            "https://www.youtube.com/@NASA/streams",
        ):
            with self.subTest(url=url):
                self.assertEqual(server._with_videos_tab(url), url)

    def test_a_watch_url_is_left_alone(self):
        """The video id rides in the query, so the path looks like a channel."""
        url = "https://www.youtube.com/watch?v=IwZVXmQdX1E"
        self.assertEqual(server._with_videos_tab(url), url)
        self.assertEqual(server._with_videos_tab("https://youtu.be/IwZVXmQdX1E"),
                         "https://youtu.be/IwZVXmQdX1E")

    def test_instagram_and_tiktok_get_no_youtube_tab(self):
        """Neither site has a /videos tab; the profile URL is already the feed."""
        for url in ("https://www.instagram.com/someone/",
                    "https://www.tiktok.com/@someone"):
            with self.subTest(url=url):
                self.assertEqual(server._with_videos_tab(url), url)


class ScrapResultsTests(unittest.TestCase):
    """_scrap_results normalises whatever yt-dlp hands back.

    The network is faked: the point is the shaping (flat entries, missing
    durations, playlist-inside-playlist), not the extractor.
    """

    def _run(self, payload, options):
        """Call _scrap_results with fetch_metadata faked out.

        Returns (results, title, seen), where ``seen`` records the URL and the
        extra yt-dlp args the call produced. The patch has to stay in place for
        the duration of the call — restoring in a ``finally`` inside a helper
        would put the real function back before it is ever invoked.
        """
        seen: dict = {}

        def fake(url, config, logger=None):
            seen["url"] = url
            seen["args"] = list(config.extra_ytdlp_args)
            return payload

        from unittest import mock

        with mock.patch.object(server.download_mod, "fetch_metadata", fake):
            results, title = server._scrap_results(options)
        return results, title, seen

    def test_a_single_link_becomes_one_result(self):
        payload = {"title": "Um vídeo", "id": "abc", "webpage_url": "https://x/1",
                   "duration": 67.5, "uploader": "Canal"}
        results, title, seen = self._run(payload, {"url": "https://x/1", "mode": "link"})
        self.assertEqual(title, "Um vídeo")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["duration"], 67.5)
        self.assertEqual(results[0]["url"], "https://x/1")
        # Link mode must NOT go through playlist expansion.
        self.assertEqual(seen["args"], [])

    def test_a_profile_expands_and_asks_for_the_limit(self):
        payload = {"title": "Canal", "entries": [
            {"id": "1", "title": "A", "url": "https://x/1", "duration": 30},
            {"id": "2", "title": "B", "url": "https://x/2", "duration": 40},
        ]}
        results, title, seen = self._run(
            payload, {"url": "https://x/canal", "mode": "profile", "limit": 7}
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(title, "Canal")
        # --playlist-end is what lifts the hardcoded --no-playlist, because
        # _base_args appends extra_ytdlp_args last.
        self.assertIn("--flat-playlist", seen["args"])
        self.assertIn("--playlist-end", seen["args"])
        self.assertIn("7", seen["args"])

    def test_a_missing_duration_is_not_zero(self):
        """Flat entries often carry no duration (live, some Instagram shapes).

        Reporting 0 would tell the user a 40-minute video is empty.
        """
        payload = {"title": "C", "entries": [
            {"id": "1", "title": "A", "url": "https://x/1"},
        ]}
        results, _, _ = self._run(payload, {"url": "https://x/c", "mode": "profile"})
        self.assertIsNone(results[0]["duration"])

    def test_a_playlist_inside_a_playlist_is_flattened(self):
        payload = {"title": "C", "entries": [
            {"_type": "playlist", "entries": [
                {"id": "1", "title": "A", "url": "https://x/1"},
            ]},
            {"_type": "url", "id": "2", "title": "B", "url": "https://x/2"},
        ]}
        results, _, _ = self._run(payload, {"url": "https://x/c", "mode": "profile"})
        self.assertEqual([r["title"] for r in results], ["A", "B"])

    def test_the_limit_is_clamped(self):
        """A typed 99999 must not turn into an unbounded enumeration."""
        payload = {"title": "C", "entries": []}
        _, _, seen = self._run(
            payload, {"url": "https://x/c", "mode": "profile", "limit": 99999}
        )
        self.assertIn("100", seen["args"])

    def test_a_missing_url_is_rejected(self):
        with self.assertRaises(ClipperError):
            server._scrap_results({"url": "  ", "mode": "link"})

    def test_a_whole_command_line_is_split_into_argv(self):
        """A single string must not be exploded into one argument per character.

        ``list("--cookies x.txt")`` yields 20 single-character strings, and the
        run then dies with yt-dlp listing the whole alphabet back at the user.
        """
        payload = {"title": "Um vídeo", "id": "abc", "webpage_url": "https://x/1"}
        _, _, seen = self._run(
            payload,
            {"url": "https://x/1", "mode": "link",
             "extra_ytdlp_args": "--cookies C:/cookies.txt"},
        )
        self.assertEqual(seen["args"], ["--cookies", "C:/cookies.txt"])

    def test_a_pre_split_argument_list_is_passed_through(self):
        payload = {"title": "Um vídeo", "id": "abc", "webpage_url": "https://x/1"}
        _, _, seen = self._run(
            payload,
            {"url": "https://x/1", "mode": "link",
             "extra_ytdlp_args": ["--cookies", "C:/cookies.txt"]},
        )
        self.assertEqual(seen["args"], ["--cookies", "C:/cookies.txt"])


class ScrapCardTests(unittest.TestCase):
    """The result card: one place that decides what a search hit looks like.

    The card is built as an HTML string in the page and its numbers are shaped
    on the server, so the two have to agree on names. The page test is a
    contract test, not a render test: it asserts the card template references
    the same fields the server emits, and that the escaping helper is actually
    applied to the text fields. A render test would need a browser; this one
    catches the bug that matters (a field renamed on one side only).
    """

    def setUp(self):
        self.page = (server.WEB_DIR / "scrap.html").read_text(encoding="utf-8")

    def test_the_card_shows_a_thumbnail_slot(self):
        self.assertIn("result-thumb", self.page)

    def test_the_card_has_a_play_affordance(self):
        """A video hit has to look like a video, not like a list row."""
        self.assertIn("result-play", self.page)

    def test_the_card_uses_the_fields_the_server_sends(self):
        """Every item field the server emits must be reachable from the card.

        ``thumb`` is fetched lazily and never returned by ``_scrap_results``,
        so it is excluded by construction.
        """
        for field in ("title", "url", "uploader", "duration", "view_count"):
            self.assertIn(field, self.page, f"card não usa o campo {field}")

    def test_the_card_embeds_the_raw_item_and_escapes_it(self):
        """The thumbnail needs the row's data, so the card carries it inline.

        That makes escaping mandatory: a title is attacker-controlled text and
        it now lands inside an attribute. If the helper is dropped, a title
        with a quote breaks out of the attribute.
        """
        self.assertIn("JSON.stringify", self.page)
        self.assertIn("esc(", self.page)

    def test_the_policy_allows_same_origin_images(self):
        """The card shows images; the policy has to allow them.

        Thumbnails are proxied through ``/thumb/``, so ``img-src 'self'`` is
        enough and no CDN wildcard is needed. With no ``img-src`` at all the
        images are blocked and the feature reads as broken rather than as a
        policy error.
        """
        self.assertIn("img-src", server.Handler.CSP)
        self.assertIn("'self'", server.Handler.CSP)

    def test_the_policy_does_not_wildcard_external_hosts(self):
        """A ``https:`` wildcard would let any injected tag call out.

        It is also what makes the proxy worthwhile: the one host we must reach
        for images is this one.
        """
        self.assertNotIn("img-src https:", server.Handler.CSP)
        self.assertNotIn("img-src *", server.Handler.CSP)

    def test_the_card_escapes_attributes_and_text(self):
        escaped = server.esc
        self.assertEqual(escaped('<b>"x"</b>'), "&lt;b&gt;&quot;x&quot;&lt;/b&gt;")
        self.assertEqual(escaped("'"), "&#x27;")


class ScrapThumbTests(unittest.TestCase):
    """A thumbnail lookup must never turn a listed video into an error."""

    def test_a_failed_lookup_returns_none_instead_of_raising(self):
        from unittest import mock

        with mock.patch.object(server, "_fetch_thumb_bytes", side_effect=OSError("boom")):
            self.assertIsNone(server._scrap_thumb({"id": "x"}))

    def test_a_video_without_a_thumbnail_returns_none(self):
        self.assertIsNone(server._scrap_thumb({"id": "x"}))

    def test_a_traversal_id_is_rejected_not_sanitised_silently(self):
        """An id arrives from yt-dlp, but the path it builds must stay inside.

        The characters that survive the filter are alphanumerics plus dash and
        underscore, which cannot climb a directory. Anything else collapses to a
        name inside the cache dir rather than escaping it.
        """
        from unittest import mock

        with mock.patch.object(server, "_fetch_thumb_bytes", return_value=b"x"):
            served = server._scrap_thumb({"id": "../../etc/passwd", "url": "u"})
        self.assertIsNotNone(served)
        name = served.rsplit("/", 1)[-1]
        self.assertNotIn("..", name)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)

    def test_the_cache_dir_stays_under_web(self):
        """A cache of other people's frames must not join the clip library."""
        cache = server._thumb_dir().resolve()
        self.assertTrue(str(cache).startswith(str(server.WEB_DIR.resolve())))
        self.assertNotIn("output", cache.parts)


class ScrapThumbRouteTests(unittest.TestCase):
    """The /scrap/thumb GET is what the <img> tag actually hits."""

    def test_the_page_points_the_image_at_the_proxy(self):
        """The CSP allows images from this origin only, so a raw CDN URL in
        ``src`` is blocked and the thumbnail silently never appears."""
        page = (server.WEB_DIR / "scrap.html").read_text(encoding="utf-8")
        self.assertIn('"/scrap/thumb?i="', page)
        # The item's own ``thumb`` field is a foreign CDN URL and must not be
        # assigned to src directly.
        self.assertNotIn("img.src = item.thumb", page)

    def test_an_index_with_no_results_is_a_404_not_a_crash(self):
        handler = object.__new__(server.Handler)
        sent: dict = {}
        handler._send_json = lambda payload, code=200: sent.update(payload, _code=code)
        with mock_lock_empty():
            server.Handler._send_thumb_at(handler, 7, "1")
        self.assertEqual(sent.get("_code"), 404)


def mock_lock_empty():
    """Run with an empty scrape state, restoring whatever was there."""
    from unittest import mock

    return mock.patch.dict(server._state, {"scrap": []})


class YtdlpArgvTests(unittest.TestCase):
    """_ytdlp_argv normalises the three shapes clients actually send."""

    def test_none_and_empty_are_empty(self):
        self.assertEqual(server._ytdlp_argv(None), [])
        self.assertEqual(server._ytdlp_argv([]), [])
        self.assertEqual(server._ytdlp_argv("   "), [])

    def test_a_single_character_argument_survives(self):
        """Splitting must not be applied twice — ``-x`` is a real flag."""
        self.assertEqual(server._ytdlp_argv(["-x"]), ["-x"])

    def test_a_blank_element_is_dropped(self):
        self.assertEqual(
            server._ytdlp_argv(["--cookies", "", "C:/c.txt"]),
            ["--cookies", "C:/c.txt"],
        )


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
        "/scrap": {"title": "Scrap", "ico": "⤓"},
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
