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
from unittest import mock

from viralclipper import report
from viralclipper.ig_profile import ProfileItem, ProfileListing
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


def page_source(name: str) -> str:
    """A page plus its external CSS and JS, as one string.

    Each page keeps markup in the ``.html`` and styles/script in sibling
    files. The contract tests below care about the effective page — which
    class name or helper it exposes — not about which file that lands in, so
    they read all three here.
    """
    base = server.WEB_DIR / name
    parts = [base.read_text(encoding="utf-8")]
    for suffix in (".css", ".js"):
        asset = base.with_suffix(suffix)
        if asset.exists():
            parts.append(asset.read_text(encoding="utf-8"))
    return "\n".join(parts)


class ScrapPageTests(unittest.TestCase):
    """The Scrap page: one link, or a whole profile.

    The page is a thin shell over yt-dlp. What can silently break is the URL
    routing — a profile URL that resolves to a channel's sub-tab playlists
    instead of its videos looks like "the profile has no content", which is the
    worst possible failure mode because it is plausible.
    """

    def setUp(self):
        self.page = page_source("scrap.html")

    def test_the_page_exists(self):
        self.assertTrue(
            (server.WEB_DIR / "scrap.html").exists(), "web/scrap.html is missing"
        )

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
        body = self.page
        self.assertIn('data-mode="link"', body)
        self.assertIn('data-mode="profile"', body)

    def test_the_page_hands_off_to_the_panel(self):
        """The pick has to lead somewhere, or the page is a dead end."""
        body = self.page
        self.assertIn('encodeURIComponent(picked.url)', body)
        self.assertIn('"/?url="', body)

    def test_the_panel_accepts_the_handoff(self):
        panel = page_source("index.html")
        self.assertIn("URLSearchParams(window.location.search)", panel)
        self.assertIn("params.get('url')", panel)

    def test_cookies_are_offered_in_both_modes(self):
        """Instagram refuses a lone reel too, so the picker cannot be profile-only.

        The selector used to live inside the ``#perfil-opts`` fieldset, which
        ``renderMode`` hides for ``mode === "link"``. Link mode therefore had no
        way to authenticate at all, and its searches failed permanently.
        """
        body = self.page
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
        body = self.page
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
            results, title, removed = server._scrap_results(options)
        seen["removed"] = removed
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

    def test_viral_lists_up_to_100_and_sorts_by_views(self):
        """viral=true fetches the ceiling and ranks before cutting to limit."""
        payload = {"title": "Canal", "entries": [
            {"id": "1", "title": "Fraco", "url": "https://x/1", "view_count": 10},
            {"id": "2", "title": "HIT", "url": "https://x/2", "view_count": 9000},
            {"id": "3", "title": "Sem número", "url": "https://x/3"},
            {"id": "4", "title": "Meio", "url": "https://x/4", "view_count": 500},
        ]}
        results, _, seen = self._run(
            payload, {"url": "https://x/c", "mode": "profile", "limit": 2, "viral": True}
        )
        self.assertEqual([r["title"] for r in results], ["HIT", "Meio"])
        self.assertIn("100", seen["args"])

    def test_without_viral_the_feed_order_is_kept(self):
        payload = {"title": "Canal", "entries": [
            {"id": "1", "title": "Fraco", "url": "https://x/1", "view_count": 10},
            {"id": "2", "title": "HIT", "url": "https://x/2", "view_count": 9000},
        ]}
        results, _, seen = self._run(
            payload, {"url": "https://x/c", "mode": "profile", "limit": 2}
        )
        self.assertEqual([r["title"] for r in results], ["Fraco", "HIT"])
        self.assertIn("2", seen["args"])

    def test_a_repeated_id_is_listed_once(self):
        """The extractor may repeat an item across pages/tabs."""
        payload = {"title": "Canal", "entries": [
            {"id": "1", "title": "A", "url": "https://x/1"},
            {"id": "1", "title": "A", "url": "https://x/1"},
            {"id": "2", "title": "B", "url": "https://x/2"},
        ]}
        results, _, seen = self._run(
            payload, {"url": "https://x/c", "mode": "profile"}
        )
        self.assertEqual([r["title"] for r in results], ["A", "B"])
        self.assertEqual(seen["removed"], 1)

    def test_a_repost_keeps_the_take_with_more_views(self):
        """Same normalised title + same duration under another id = repost."""
        payload = {"title": "Canal", "entries": [
            {"id": "1", "title": "Declaração positiva existe! #a #b",
             "url": "https://x/1", "duration": 85, "view_count": 231},
            {"id": "2", "title": "declaracao positiva existe @x",
             "url": "https://x/2", "duration": 85, "view_count": 907},
        ]}
        results, _, seen = self._run(
            payload, {"url": "https://x/c", "mode": "profile"}
        )
        self.assertEqual([r["id"] for r in results], ["2"])
        self.assertEqual(seen["removed"], 1)

    def test_episodes_of_a_series_are_not_merged(self):
        """'parte 2' vs 'parte 3' differ after normalisation: both stay."""
        payload = {"title": "Canal", "entries": [
            {"id": "1", "title": "Mentiras premiadas parte 2",
             "url": "https://x/1", "duration": 84, "view_count": 100},
            {"id": "2", "title": "Mentiras premiadas parte 3",
             "url": "https://x/2", "duration": 84, "view_count": 200},
        ]}
        results, _, seen = self._run(
            payload, {"url": "https://x/c", "mode": "profile"}
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(seen["removed"], 0)

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
        self.page = page_source("scrap.html")

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

    def test_the_count_is_written_the_brazilian_way(self):
        """16.000.000 reads as "16mi", not "16M".

        The suffix and the decimal comma are the two things a Brazilian reader
        expects, and "M" is the English spelling of "mi".
        """
        self.assertIn('+ "mi"', self.page)
        self.assertIn('+ "k"', self.page)
        self.assertIn('replace(".", ",")', self.page)
        self.assertNotIn('+ "M"', self.page)

    def test_the_count_formatter_climbs_out_of_the_thousands_band(self):
        """``1000k`` is not something anyone writes, so 999.999 becomes "1mi"."""
        self.assertIn('thousands === "1000"', self.page)


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
        page = page_source("scrap.html")
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


class SelectedDownloadTests(unittest.TestCase):
    """``/scrap/download`` aceita a selecao e roda como job.

    Diferente de ``/scrap/archive`` (que percorre um catalogo do Instagram a
    partir de cookies), aqui a lista ja esta na tela: o que chega e a selecao,
    uma URL por item. A resposta e o ACEITE, nao o resultado: uma selecao de
    vinte videos leva minutos, entao o trabalho roda numa thread e a pagina
    acompanha o progresso em ``/scrap/download/progress``.
    """

    def setUp(self):
        self.sent: dict = {}
        self.handler = object.__new__(server.Handler)
        self.handler._send_json = lambda payload, code=200: self.sent.update(payload, _code=code)
        self.started: list[dict] = []
        with server._lock:
            server._state[server._DOWNLOAD_SLOT] = None

    def _handle(self, payload, *, expected_worker: bool = True):
        """Call the handler with the worker replaced, then wait for the handoff.

        The real thread stays (it is part of what the route does) and its target
        is a recorder plus an event: without the wait the assertions would race
        the thread that was just spawned. A refusal starts no thread at all, so
        there is nothing to wait for and the caller says so.
        """
        import threading
        from unittest import mock

        done = threading.Event()

        def record_worker(targets, root, config, logger):
            self.started.append(
                {
                    "targets": list(targets),
                    "root": Path(root),
                    "config": config,
                    "logger": logger,
                }
            )
            done.set()

        with mock.patch.object(server, "_download_worker", record_worker):
            server.Handler._handle_selected_download(self.handler, payload)
        if expected_worker:
            done.wait(5)
        return self.sent

    def _record(self) -> dict:
        with server._lock:
            return dict(server._state[server._DOWNLOAD_SLOT] or {})

    def test_the_selection_is_accepted_and_handed_over_in_order(self):
        payload = {
            "items": [
                {"url": "https://x/1", "id": "id1", "title": "Um"},
                {"url": "https://x/2", "id": "id2", "title": "Dois"},
            ],
            "collection": "ANCAPSU - Vídeos",
        }
        sent = self._handle(payload)
        targets = self.started[0]["targets"]
        self.assertEqual([t.url for t in targets], ["https://x/1", "https://x/2"])
        self.assertEqual([t.media_id for t in targets], ["id1", "id2"])
        self.assertEqual([t.title for t in targets], ["Um", "Dois"])
        self.assertEqual(sent["_code"], 200)

    def test_the_answer_is_an_acceptance_not_a_result(self):
        """A pagina nao recebe contagens: ela acompanha o progresso."""
        sent = self._handle({"items": [{"url": "https://x/1"}, {"url": "https://x/2"}]})
        self.assertTrue(sent["started"])
        self.assertEqual(sent["total"], 2)
        self.assertNotIn("downloaded", sent)

    def test_the_folder_is_named_after_the_listing_on_screen(self):
        # slugify preserva letra acentuada, entao a pasta tambem: e o nome que o
        # usuario vai ler no Explorer.
        self._handle({"items": [{"url": "https://x/1"}], "collection": "ANCAPSU - Vídeos"})
        self.assertEqual(
            self.started[0]["root"],
            server.REPO_ROOT / "output" / "downloads" / "ancapsu_vídeos",
        )

    def test_a_listing_without_a_title_still_gets_a_folder(self):
        self._handle({"items": [{"url": "https://x/1"}]})
        self.assertEqual(
            self.started[0]["root"],
            server.REPO_ROOT / "output" / "downloads" / "selecionados",
        )

    def test_the_cookies_file_becomes_a_ytdlp_argument(self):
        self._handle({"items": [{"url": "https://x/1"}], "cookies_file": "C:/cookies.txt"})
        args = self.started[0]["config"].extra_ytdlp_args
        self.assertEqual(args[args.index("--cookies") + 1], "C:/cookies.txt")

    def test_the_slot_is_taken_before_the_worker_starts(self):
        """A pagina pode perguntar pelo progresso no instante seguinte ao POST."""
        self._handle({"items": [{"url": "https://x/1"}]})
        record = self._record()
        self.assertTrue(record["active"])
        self.assertEqual(record["state"], "baixando")
        self.assertEqual(record["total"], 1)
        self.assertEqual(record["root"], "output/downloads/selecionados")

    def test_an_empty_selection_is_refused(self):
        for payload in ({}, {"items": []}, {"items": "todos"}):
            with self.subTest(payload=payload):
                sent = self._handle(payload, expected_worker=False)
                self.assertEqual(sent["_code"], 400)
                self.assertIn("nenhum item", sent["error"])
        self.assertEqual(self.started, [])

    def test_items_without_a_url_are_refused(self):
        sent = self._handle(
            {"items": [{"id": "id1", "title": "sem url"}, None, 7]}, expected_worker=False
        )
        self.assertEqual(sent["_code"], 400)
        self.assertIn("URL", sent["error"])
        self.assertEqual(self.started, [])

    def test_a_valid_item_next_to_junk_is_still_downloaded(self):
        self._handle({"items": [None, {"url": "  https://x/1  "}, {"id": "so-id"}]})
        self.assertEqual([t.url for t in self.started[0]["targets"]], ["https://x/1"])

    def test_the_selection_is_capped(self):
        payload = {"items": [{"url": f"https://x/{n}"} for n in range(300)]}
        self._handle(payload)
        self.assertEqual(len(self.started[0]["targets"]), server._MAX_SELECTED_DOWNLOADS)

    def test_a_second_batch_is_refused_while_one_runs(self):
        """Dois lotes na mesma pasta brigariam pelos mesmos nomes."""
        self._handle({"items": [{"url": "https://x/1"}]})
        sent = self._handle({"items": [{"url": "https://x/2"}]}, expected_worker=False)
        self.assertEqual(sent["_code"], 409)
        self.assertIn("andamento", sent["error"])
        self.assertEqual(len(self.started), 1)

    def test_a_new_batch_is_allowed_once_the_previous_ended(self):
        self._handle({"items": [{"url": "https://x/1"}]})
        server._publish_download(active=False, state="concluido")
        self._handle({"items": [{"url": "https://x/2"}]})
        self.assertEqual(len(self.started), 2)

    def _run_worker(self, summary=None, events=(), explode=None) -> list[dict]:
        """Run the worker inline with ``download_many`` faked out.

        Returns the record after every progress event — what the page would have
        painted — plus the final state at the end.
        """
        from unittest import mock

        seen: list[dict] = []

        def fake_many(targets, destination, config, logger=None, on_progress=None):
            for event in events:
                on_progress(event)
                seen.append(self._record())
            if explode is not None:
                raise explode
            return summary

        logger = server.CollectingLogger()
        with mock.patch.object(server.download_mod, "download_many", fake_many):
            server._download_worker([], Path("output/downloads/x"), None, logger)
        seen.append(self._record())
        return seen

    def test_the_worker_publishes_each_step_as_it_happens(self):
        event = server.download_mod.DownloadEvent
        seen = self._run_worker(
            summary=server.download_mod.DownloadSummary(total=1, downloaded=1),
            events=(
                event("item", index=1, total=1, title="Um", percent=0.0),
                event("bytes", index=1, total=1, title="Um", percent=45.5),
                event("bytes", index=1, total=1, title="Um", percent=88.0),
            ),
        )
        self.assertEqual(
            [(record["phase"], record["percent"]) for record in seen[:3]],
            [("item", 0.0), ("bytes", 45.5), ("bytes", 88.0)],
        )
        self.assertEqual(seen[1]["title"], "Um")
        self.assertEqual(seen[1]["index"], 1)

    def test_a_percent_of_none_is_reported_as_zero(self):
        event = server.download_mod.DownloadEvent
        seen = self._run_worker(
            summary=server.download_mod.DownloadSummary(total=1),
            events=(event("skipped", index=1, total=1, title="Um", skipped=1),),
        )
        self.assertEqual(seen[0]["percent"], 0.0)

    def test_the_worker_ends_with_the_summary_the_page_shows(self):
        summary = server.download_mod.DownloadSummary(
            total=3, downloaded=2, skipped=1, failed=0, root=Path("output/downloads/x"),
        )
        final = self._run_worker(summary=summary)[-1]
        self.assertFalse(final["active"])
        self.assertEqual(final["state"], "concluido")
        self.assertEqual(final["percent"], 100.0)
        self.assertEqual((final["downloaded"], final["skipped"]), (2, 1))
        self.assertTrue(any("baixados : 2" in line for line in final["lines"]))

    def test_the_worker_reports_the_failures_one_by_one(self):
        summary = server.download_mod.DownloadSummary(
            total=2, downloaded=1, failed=1, errors=[("id2", "video privado")],
        )
        final = self._run_worker(summary=summary)[-1]
        self.assertEqual(final["errors"], [{"item": "id2", "error": "video privado"}])
        self.assertEqual(final["failed"], 1)

    def test_a_worker_that_explodes_still_leaves_a_record(self):
        """Numa thread uma excecao nao tem quem a veja: o record e o canal."""
        final = self._run_worker(explode=RuntimeError("sem rede"))[-1]
        self.assertFalse(final["active"])
        self.assertEqual(final["state"], "erro")
        self.assertIn("sem rede", final["error"])

    def test_a_folder_outside_the_project_is_still_reported(self):
        """``relative_to`` raises for a path outside the repo; the UI still shows it."""
        summary = server.download_mod.DownloadSummary(
            total=1, downloaded=1, root=Path("D:/fora/do/repo")
        )
        self.assertEqual(self._run_worker(summary=summary)[-1]["root"], "D:/fora/do/repo")

    def test_no_destination_is_reported_as_empty(self):
        summary = server.download_mod.DownloadSummary(total=0)
        self.assertEqual(self._run_worker(summary=summary)[-1]["root"], "")

    def test_the_record_has_every_field_the_page_reads(self):
        """A pagina le o record direto: chave faltando vira bug de render."""
        record = server._download_record()
        for field in (
            "active", "state", "phase", "total", "index", "title", "percent",
            "downloaded", "skipped", "failed", "root", "lines", "errors", "error",
        ):
            self.assertIn(field, record)
        self.assertFalse(record["active"])

    def test_publishing_merges_instead_of_replacing(self):
        server._publish_download(state="baixando", total=7)
        server._publish_download(percent=42.0)
        record = self._record()
        self.assertEqual((record["state"], record["total"], record["percent"]), ("baixando", 7, 42.0))


class ArchiveProgressTests(unittest.TestCase):
    """O painel de arquivamento: percentual, linha corrente e log completo.

    O arquivamento de perfil e a unica rota que baixa *muitos* itens sem a
    lista na tela, entao a pagina so sabe o que acontece pelo record publicado.
    Estes testes travam o que a pagina precisa: a % de trabalho executado, a
    ultima linha do yt-dlp (detalhe da requisicao) e o transcript inteiro que
    alimenta o painel recolhivel.
    """

    def setUp(self):
        with server._lock:
            server._state[server._ARCHIVE_SLOT] = None

    def _record(self) -> dict:
        with server._lock:
            return dict(server._state[server._ARCHIVE_SLOT] or {})

    def _run_worker(self, *, folders=("reels", "posts"), script=None,
                    on_line_lines=(), progress=(), summary=None,
                    explode=None) -> list[dict]:
        """Run the archive worker inline, capturing the record after each event.

        Returns the record as the page would have painted it after every
        progress callback, plus the final record at the end. Corpos de classe
        nao enxergam o escopo da funcao, entao tudo que depende de ``folders``
        e montado fora das classes fake.
        """
        from unittest import mock

        from viralclipper import archive as archive_mod
        from viralclipper import ig_profile as ig_profile_mod

        if script is None:
            script = ([("line", line) for line in on_line_lines]
                      + [("progress", step) for step in progress])

        class FakeItem:
            def __init__(self, folder, url, code):
                self.folder = folder
                self.url = url
                self.code = code

        items = [FakeItem(folder, "https://x/%d" % n, "c%d" % n)
                 for n, folder in enumerate(folders)]

        class FakeListing:
            username = "perfil_teste"
            title = "Teste"
            pages = 1

        FakeListing.items = items

        class FakeSummary:
            username = "perfil_teste"
            root = server.REPO_ROOT / "output" / "instagram" / "perfil_teste"
            errors = []

            def __init__(self):
                self.total = len(items)
                self.downloaded = len(items)
                self.skipped = 0
                self.failed = 0
                self.photos = 0
                self.reels = len([f for f in folders if f == "reels"])
                self.posts = len([f for f in folders if f == "posts"])

            def lines(self):
                return ["resumo"]

        seen: list[dict] = []

        def fake_archive(listing, destination, config, logger, *, on_progress=None,
                         on_line=None, **kwargs):
            # `script` reproduz a ordem real do archive.py: notify("item")
            # antes de baixar, linhas do yt-dlp durante, notify("done") no fim.
            # Sem ele, uma linha nunca apareceria antes do primeiro item.
            for kind, payload in script:
                if kind == "progress":
                    on_progress(*payload)
                else:
                    on_line(payload)
                seen.append(self._record())
            if explode is not None:
                raise explode
            return summary if summary is not None else FakeSummary()

        logger = server.CollectingLogger()
        with mock.patch.object(ig_profile_mod, "list_profile", lambda *a, **k: FakeListing()), \
             mock.patch.object(archive_mod, "archive_profile", fake_archive):
            server._archive_worker("perfil_teste", "cookies.txt", list(folders), None, logger)
        seen.append(self._record())
        return seen

    def test_each_ytdlp_line_is_published_so_the_page_can_echo_it(self):
        lines = (
            "[download]   0.5% of  12.00MiB at  1.2MiB/s",
            "[download]  54.9% of  12.00MiB at  2.4MiB/s",
        )
        seen = self._run_worker(on_line_lines=lines)
        # A pagina pinta a ultima linha do yt-dlp (detalhe da requisicao)...
        self.assertEqual(seen[0]["current_line"], lines[0])
        self.assertEqual(seen[1]["current_line"], lines[1])
        # ...mas guarda o transcript inteiro pro painel de logs.
        self.assertEqual(seen[1]["archive_lines"], list(lines))

    def test_the_worker_reports_the_percentage_of_work_done(self):
        # `position` e o item NA MAO (o notify do archive dispara antes de
        # baixar), entao o percentual so conta itens ja fechados. Com 3 itens:
        # item 1 na mao = 0%, item 2 na mao com o 1 fechado = 33.3%, e o "done"
        # do ultimo fecha os 100%.
        seen = self._run_worker(
            folders=("reels", "posts", "reels"),
            script=[
                ("progress", (1, 3, "c0", "item")),
                ("progress", (1, 3, "c0", "done")),
                ("progress", (2, 3, "c1", "item")),
                ("progress", (3, 3, "c2", "done")),
            ],
        )
        self.assertEqual([r["percent"] for r in seen[:4]],
                         [0.0, 33.3, 33.3, 100.0])
        self.assertEqual([r["index"] for r in seen[:4]], [1, 1, 2, 3])
        self.assertEqual(seen[0]["state"], "baixando")

    def test_a_ytdlp_line_moves_the_bar_inside_the_current_item(self):
        # Sem isso a barra congela enquanto um reel longo baixa: o item so
        # fecha no fim, e com 4 itens o salto seria de 25%.
        seen = self._run_worker(
            folders=("reels", "posts", "reels", "posts"),
            script=[
                ("progress", (1, 4, "c0", "item")),
                ("line", "[download]  50.0% of  10.00MiB at  1.0MiB/s"),
            ],
        )
        # O notify do item abre em 0%; a linha com 50% do item=12.5% do total.
        self.assertEqual(seen[0]["percent"], 0.0)
        self.assertEqual(seen[1]["percent"], 12.5)
        self.assertEqual(seen[1]["item_fraction"], 0.5)
        self.assertEqual(seen[1]["current_line"],
                         "[download]  50.0% of  10.00MiB at  1.0MiB/s")

    def test_a_new_item_resets_the_progress_of_the_previous_one(self):
        # Se o pedaco do item anterior vazasse, a barra andaria para tras.
        seen = self._run_worker(
            folders=("reels", "posts", "reels", "posts"),
            script=[
                ("progress", (1, 4, "c0", "item")),
                ("line", "[download]  90.0% of  10.00MiB"),
                ("progress", (2, 4, "c1", "item")),
                ("line", "[download]  10.0% of  10.00MiB"),
            ],
        )
        # A ultima entrada e o record final do worker (concluido, 100%).
        self.assertEqual([r["percent"] for r in seen[:4]], [0.0, 22.5, 25.0, 27.5])
        self.assertEqual(seen[-1]["percent"], 100.0)

    def test_the_bar_never_goes_backwards_between_item_and_line(self):
        seen = self._run_worker(
            folders=("reels", "posts", "reels", "posts"),
            script=[
                ("progress", (1, 4, "c0", "item")),
                ("progress", (1, 4, "c0", "done")),
                ("progress", (2, 4, "c1", "item")),
            ],
        )
        percents = [r["percent"] for r in seen[:3]]
        self.assertEqual(percents, [0.0, 25.0, 25.0])

    def test_a_finished_run_keeps_the_whole_log_in_the_record(self):
        lines = ("[*] listando", "[download]  10.0% of 5.00MiB", "  baixados : 2")
        seen = self._run_worker(on_line_lines=lines)
        final = seen[-1]
        self.assertEqual(final["state"], "concluido")
        self.assertEqual(final["percent"], 100.0)
        self.assertFalse(final["active"])
        self.assertEqual(final["archive_lines"], list(lines))

    def test_a_failure_keeps_the_lines_it_managed_to_produce(self):
        lines = ("[download]  10.0% of 5.00MiB",)
        seen = self._run_worker(on_line_lines=lines, explode=ClipperError("caiu"))
        final = seen[-1]
        self.assertEqual(final["state"], "erro")
        self.assertEqual(final["archive_lines"], list(lines))
        self.assertIn("caiu", final["error"])

    def test_the_record_starts_idle_with_the_log_fields_the_page_reads(self):
        record = server._archive_record()
        self.assertEqual(record["state"], "ocioso")
        self.assertEqual(record["archive_lines"], [])
        self.assertEqual(record["current_line"], "")
        self.assertEqual(record["percent"], 0.0)

    def test_viral_order_keeps_the_most_engaged_per_folder(self):
        """order="viral" sorts by engagement before the per-folder cap."""
        from viralclipper import archive as archive_mod
        from viralclipper import ig_profile as ig_profile_mod

        def item(code, folder, plays, likes):
            return ig_profile_mod.ProfileItem(
                code=code, url=f"https://x/{code}",
                folder=folder,
                kind="reel" if folder == "reels" else "video",
                media_type=2, play_count=plays, like_count=likes)

        listing = ig_profile_mod.ProfileListing(username="u", items=[
            item("novo-fraco", "reels", 10, 1),
            item("antigo-hit", "reels", 9000, 500),
            item("post-hit", "posts", 7000, 300),
            item("post-fraco", "posts", 5, 0),
        ])
        got = archive_mod.select_items(listing, limit=1, order="viral")
        self.assertEqual([i.code for i in got], ["antigo-hit", "post-hit"])
        recent = archive_mod.select_items(listing, limit=1)
        self.assertEqual([i.code for i in recent], ["novo-fraco", "post-hit"])


class ArchiveLogPageTests(unittest.TestCase):
    """O painel de logs existe no HTML e o JS o mantem acessivel."""

    @classmethod
    def setUpClass(cls):
        cls.html = page_source("scrap.html")

    def test_the_log_panel_markup_is_wired_to_the_progress_box(self):
        for fragment in (
            'id="archive-log"',
            'id="archive-log-head"',
            'id="archive-log-body"',
            'id="archive-log-count"',
            'aria-controls="archive-log-body"',
        ):
            self.assertIn(fragment, self.html, fragment)

    def test_the_head_toggles_the_body_and_never_hides_the_panel(self):
        # Recolher so esconde o corpo: o cabecalho continua clicavel, entao o
        # log nao fica inacessivel quando o download termina.
        self.assertIn("function setArchiveLogOpen(open)", self.html)
        self.assertIn("archiveLogUserToggled", self.html)
        self.assertNotIn("wrap.hidden = !open", self.html)

    def test_the_page_echoes_the_current_line_while_downloading(self):
        self.assertIn("record.current_line", self.html)
        self.assertIn("record.archive_lines", self.html)


class ViralOptionPageTests(unittest.TestCase):
    """O "mais viralizados" existe nos dois paineis e chega ao servidor."""

    @classmethod
    def setUpClass(cls):
        cls.html = page_source("scrap.html")

    def test_the_option_exists_on_search_and_archive(self):
        for fragment in (
            'id="btn-viral"',
            'id="btn-arq-viral"',
            "aria-pressed",
            "payload.viral",
            '"viral"',
            "updateArchiveVisibility",
        ):
            self.assertIn(fragment, self.html, fragment)


class SearchLoadingTests(unittest.TestCase):
    """O "Buscando" é um skeleton animado, não um parágrafo estático."""

    @classmethod
    def setUpClass(cls):
        cls.html = page_source("scrap.html")

    def test_the_search_state_is_a_skeleton(self):
        for fragment in (
            "function renderSearching()",
            'class="search-loading"',
            'class="search-loading-track"',
            'class="skel-card"',
            'role="status"',
        ):
            self.assertIn(fragment, self.html, fragment)

    def test_the_skeleton_is_shown_and_cleared_by_search(self):
        self.assertIn("renderSearching();", self.html)
        # A resposta (ou o erro) pinta por cima: nada de skeleton residual.
        self.assertNotIn("<p class=\"empty\">Buscando", self.html)


class ScrapSelectionPageTests(unittest.TestCase):
    """A selecao em lote, do lado da pagina: os ids e a rota tem de casar."""

    def setUp(self):
        self.page = page_source("scrap.html")
        self.source = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")

    def test_the_page_offers_select_all_and_download(self):
        self.assertIn('id="btn-select-all"', self.page)
        self.assertIn('id="btn-download"', self.page)

    def test_the_download_button_starts_disabled(self):
        """Sem selecao nao ha o que baixar, e o botao diz isso antes do clique."""
        self.assertIn('id="btn-download" disabled', self.page)

    def test_the_toolbar_only_appears_with_a_list(self):
        self.assertIn('id="select-box" hidden', self.page)

    def test_every_card_carries_a_checkbox(self):
        self.assertIn('data-act="check"', self.page)

    def test_the_page_posts_the_selection_to_the_route(self):
        self.assertIn('"/scrap/download"', self.page)

    def test_the_page_follows_the_progress_of_the_route(self):
        self.assertIn("/scrap/download/progress", self.page)

    def test_the_toolbar_carries_a_progress_bar(self):
        self.assertIn('id="download-bar"', self.page)
        self.assertIn('id="download-fill"', self.page)

    def test_the_server_registers_the_route(self):
        self.assertIn('"/scrap/download"', self.source)
        self.assertIn("_handle_selected_download", self.source)

    def test_the_labels_come_from_one_place(self):
        """Rotulos escritos em um lugar so: o contador e os dois botoes sao
        reescritos a cada mudanca de selecao, e nao no clique de cada card."""
        self.assertIn("function renderSelection(", self.page)
        self.assertIn("renderSelection();", self.page)


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


class CookiesFileFromArgsTests(unittest.TestCase):
    """ig_profile reads a jar from disk, so only ``--cookies`` is of use to it."""

    def test_the_long_form_is_read(self):
        self.assertEqual(
            server._cookies_file_from_args(["--cookies", "cookies.txt"]), "cookies.txt"
        )

    def test_the_equals_form_is_read(self):
        self.assertEqual(
            server._cookies_file_from_args(["--cookies=C:/x/cookies.txt"]),
            "C:/x/cookies.txt",
        )

    def test_it_finds_the_flag_among_others(self):
        self.assertEqual(
            server._cookies_file_from_args(["-f", "bv*", "--cookies", "c.txt", "-S", "res"]),
            "c.txt",
        )

    def test_the_browser_form_is_not_a_file(self):
        """That route is sealed by App-Bound Encryption on Chrome 127+ anyway."""
        self.assertEqual(
            server._cookies_file_from_args(["--cookies-from-browser", "chrome"]), ""
        )

    def test_nothing_at_all(self):
        self.assertEqual(server._cookies_file_from_args([]), "")
        self.assertEqual(server._cookies_file_from_args(["--cookies"]), "")


class IgProfileRoutingTests(unittest.TestCase):
    """An Instagram profile must reach the GraphQL lister, never yt-dlp.

    ``InstagramUserIE`` is disabled upstream, so the flat-playlist route answers
    "Unable to extract data" for an account that is reachable in a browser. The
    whole feature is invisible if this diversion regresses — and the failure
    looks like "the account is empty", which is plausible enough to be missed.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_route_"))

    def _route(self, url, *, extra_ytdlp_args=None):
        calls = {"ig": None, "ytdlp": None}

        def fake_ig(username, payload, cookies_file, argv):
            calls["ig"] = {
                "username": username, "cookies_file": cookies_file, "argv": list(argv),
            }
            return [{"index": 1, "id": "A", "folder": "reels"}], username, 0

        def fake_meta(target, config):
            calls["ytdlp"] = target
            return {"entries": [], "title": ""}

        payload = {"url": url, "mode": "profile", "limit": 5}
        if extra_ytdlp_args is not None:
            payload["extra_ytdlp_args"] = extra_ytdlp_args
        with mock.patch.object(server, "_ig_profile_results", side_effect=fake_ig), \
             mock.patch.object(server.download_mod, "fetch_metadata", side_effect=fake_meta), \
             mock.patch.object(server.download_mod, "repair_view_counts",
                               side_effect=lambda *a, **k: None):
            results, _title, _removed = server._scrap_results(payload)
        return calls, results

    def test_a_profile_url_goes_to_the_graphql_lister(self):
        calls, results = self._route(
            "https://www.instagram.com/salmareis/",
            extra_ytdlp_args=["--cookies", "cookies.txt"],
        )
        self.assertIsNone(calls["ytdlp"], "o yt-dlp nao devia nem ser chamado")
        self.assertEqual(calls["ig"]["username"], "salmareis")
        self.assertEqual(calls["ig"]["cookies_file"], "cookies.txt")
        self.assertEqual(results[0]["folder"], "reels")

    def test_a_reel_url_stays_with_yt_dlp(self):
        """A code is not an account; diverting it would answer "0 itens"."""
        calls, _ = self._route("https://www.instagram.com/reel/ABC123/")
        self.assertIsNone(calls["ig"])
        self.assertEqual(calls["ytdlp"], "https://www.instagram.com/reel/ABC123/")

    def test_a_post_url_stays_with_yt_dlp(self):
        calls, _ = self._route("https://www.instagram.com/p/ABC123/")
        self.assertIsNone(calls["ig"])

    def test_a_youtube_channel_is_untouched(self):
        """The diversion must not swallow the path that already worked."""
        calls, _ = self._route("https://www.youtube.com/@NASA/videos")
        self.assertIsNone(calls["ig"])
        self.assertEqual(calls["ytdlp"], "https://www.youtube.com/@NASA/videos")

    def test_the_cookies_travel_with_the_item(self):
        """The row thumbnail is fetched later and needs the same credential."""
        calls, _ = self._route(
            "https://www.instagram.com/salmareis/",
            extra_ytdlp_args=["--cookies", "cookies.txt"],
        )
        self.assertEqual(calls["ig"]["argv"], ["--cookies", "cookies.txt"])


class IgProfileListingTests(unittest.TestCase):
    """The mapping from a ProfileItem to the row the page renders."""

    def _listing(self, items, username="alvo"):
        return ProfileListing(username=username, items=items, pages=1)

    def _run(self, items, **payload):
        with mock.patch.object(
            server.ig_profile_mod, "list_profile", return_value=self._listing(items)
        ):
            return server._ig_profile_results(
                "alvo", payload, "cookies.txt", ["--cookies", "cookies.txt"]
            )

    def test_a_missing_cookies_file_is_named(self):
        with self.assertRaises(ClipperError) as ctx:
            server._ig_profile_results("alvo", {}, "", [])
        self.assertIn("cookies.txt", str(ctx.exception))

    def test_a_caption_becomes_a_single_capped_line(self):
        """The panel gives the title one line; captions are paragraphs."""
        items = [ProfileItem(code="A", url="u", folder="reels", kind="reel",
                             caption="\n\n  Bom dia  \nsegunda linha\n")]
        results, _title, _removed = self._run(items)
        self.assertEqual(results[0]["title"], "Bom dia")

    def test_an_empty_caption_falls_back_to_the_code(self):
        items = [ProfileItem(code="A", url="u", folder="reels", kind="reel")]
        results, _title, _removed = self._run(items)
        self.assertEqual(results[0]["title"], "A")

    def test_a_reel_reports_plays_and_a_post_reports_likes(self):
        items = [
            ProfileItem(code="R", url="u", folder="reels", kind="reel",
                        media_type=2, play_count=900, like_count=10),
            ProfileItem(code="P", url="u", folder="posts", kind="post",
                        media_type=1, play_count=None, like_count=42),
        ]
        results, _title, _removed = self._run(items)
        self.assertEqual(results[0]["view_count"], 900)
        self.assertEqual(results[1]["view_count"], 42)

    def test_a_photo_is_flagged_as_having_no_video(self):
        """The row can say so instead of failing with "no video in this post"."""
        items = [
            ProfileItem(code="P", url="u", folder="posts", kind="post", media_type=1),
            ProfileItem(code="R", url="u", folder="reels", kind="reel", media_type=2),
        ]
        results, _title, _removed = self._run(items)
        self.assertFalse(results[0]["has_video"])
        self.assertTrue(results[1]["has_video"])

    def test_viral_ranks_before_the_cap(self):
        items = [
            ProfileItem(code="velho", url="u", folder="reels", kind="reel",
                        media_type=2, play_count=1),
            ProfileItem(code="viral", url="u", folder="reels", kind="reel",
                        media_type=2, play_count=999),
        ]
        results, _title, _removed = self._run(items, limit=1, viral=True)
        self.assertEqual([r["id"] for r in results], ["viral"])

    def test_the_username_is_the_title(self):
        items = [ProfileItem(code="A", url="u", folder="reels", kind="reel")]
        _results, title, removed = self._run(items)
        self.assertEqual(title, "alvo")
        self.assertEqual(removed, 0)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self, _size=None):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class ThumbFetchTests(unittest.TestCase):
    """A stored CDN URL must not cost an extra extraction per row."""

    def test_a_stored_thumb_is_used_without_asking_yt_dlp(self):
        """300 items would otherwise pay 300 extractions just for decoration."""
        item = {"url": "https://www.instagram.com/reel/A/", "thumb": "https://cdn/a.jpg"}
        with mock.patch.object(server.download_mod, "fetch_metadata",
                               side_effect=AssertionError("extraiu de novo")), \
             mock.patch.object(server, "urlopen", return_value=FakeResponse(b"img")):
            self.assertEqual(server._fetch_thumb_bytes(item), b"img")

    def test_without_a_stored_thumb_it_falls_back_to_yt_dlp(self):
        item = {"url": "https://youtu.be/x", "thumb": ""}
        meta = {"thumbnail": "https://cdn/b.jpg"}
        with mock.patch.object(server.download_mod, "fetch_metadata",
                               return_value=meta) as fetch, \
             mock.patch.object(server, "urlopen", return_value=FakeResponse(b"img2")):
            self.assertEqual(server._fetch_thumb_bytes(item), b"img2")
        fetch.assert_called_once()

    def test_no_url_means_no_bytes(self):
        self.assertIsNone(server._fetch_thumb_bytes({"thumb": "https://cdn/a.jpg"}))


class TemplatesPageTests(unittest.TestCase):
    """The wizard page holds its own copy of the preset catalog for the preview.

    A page that silently drifts from the engine offers presets that no longer
    exist, or hides the ones that do - and the failure only shows up at render
    time. These tests are the tripwire for that.
    """

    def setUp(self):
        self.page = page_source("templates.html")

    def test_the_page_exists(self):
        self.assertTrue(
            (server.WEB_DIR / "templates.html").exists(),
            "web/templates.html is missing",
        )

    def test_the_page_lists_every_shipped_preset(self):
        from viralclipper import caption_presets

        body = self.page
        missing = [name for name in caption_presets.PRESETS if f'"{name}"' not in body]
        self.assertEqual(
            missing, [], f"presets ausentes na pagina de templates: {missing}"
        )

    def test_the_page_invents_no_preset(self):
        import re

        from viralclipper import caption_presets

        body = self.page
        block = re.search(r"var PRESETS = \{(.*?)\n  \};", body, re.S)
        self.assertIsNotNone(block, "bloco PRESETS nao encontrado na pagina")
        keys = set(re.findall(r'^\s*"([a-z0-9-]+)":', block.group(1), re.M))
        unknown = sorted(keys - set(caption_presets.PRESETS))
        self.assertEqual(
            unknown, [], f"a pagina oferece presets que o motor nao tem: {unknown}"
        )

    def test_the_page_offers_every_zone_kind(self):
        from viralclipper import template as template_mod

        body = self.page
        missing = [kind for kind in template_mod.ZONE_KINDS if f"{kind}:" not in body]
        self.assertEqual(missing, [], f"tipos de zona ausentes na pagina: {missing}")

    def test_the_panel_links_to_the_templates_page(self):
        panel = page_source("index.html")
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
            css = page_source(name)
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
            body = page_source(name)
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
            body = page_source(name)
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
            body = page_source(name)
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
        self.page = page_source("templates.html")

    def test_the_geometry_rule_matches_the_engine(self):
        """Both implementations are compared on the same input.

        A change to either one that is not mirrored fails here instead of
        producing a preview that lies about the render.
        """
        from viralclipper import template as template_mod

        body = self.page
        # The page absorbs the rounding remainder into the last pixel band, the
        # same way plan_bands does. Assert the shared constants are present so a
        # rewrite cannot quietly drop the rule.
        self.assertIn("zone.fraction", body)
        self.assertIn("marginTop", body)
        self.assertIn("marginLeft", body)
        builtin = template_mod.BUILTIN["split-card"]
        bands = template_mod.plan_bands(builtin, 1080, 1920)
        self.assertEqual([b.kind for b in bands], ["video", "frame", "captions"])


class TemplatesPreviewFidelityTests(unittest.TestCase):
    """A prévia tem de medir o frame do mesmo jeito que o motor o desenha.

    Duas famílias de defeito já passaram por aqui, e nenhuma delas aparece num
    teste por nome de classe: a legenda pousava dentro da zona de baixo (por
    cento de ``margin-bottom`` resolve contra a LARGURA, não contra a altura) e
    o corpo da fonte saía quase 44% menor. Os testes abaixo leem a página
    efetiva e travam a regra, porque a prévia que mente sobre o render é pior
    do que não ter prévia.
    """

    def setUp(self):
        self.page = page_source("templates.html")

    def test_vertical_measures_are_a_fraction_of_the_height(self):
        """``cqh``, nunca ``cqw``: o motor conta px de um frame de 1920."""
        self.assertIn('state.height * 100) + "cqh"', self.page)

    def test_the_caption_anchors_from_the_base(self):
        """``bottom`` resolve contra a ALTURA; ``margin-bottom`` contra a largura."""
        self.assertIn("el.style.bottom = (marginV / state.height * 100)", self.page)
        self.assertNotIn("marginBottom = (marginV", self.page)

    def test_the_headline_margin_matches_the_engine(self):
        """``render.py`` fixa o MarginV do estilo Headline; a prévia copia.

        Um número copiado à mão e sem trava é o que já divergiu antes. Aqui o
        valor é lido do próprio ``render.py``, então mudar lá sem mudar cá
        quebra o teste em vez de quebrar a prévia.
        """
        source = (server.WEB_DIR.parent / "viralclipper" / "render.py").read_text(
            encoding="utf-8"
        )
        match = re.search(r"^Style: Headline,(.+)$", source, re.MULTILINE)
        self.assertIsNotNone(match, "o estilo ASS do headline sumiu do render.py")
        # Os campos depois de `Style: Headline,`, com os nomes que o `Format:`
        # logo acima declara. Nomeados, e nao contados por posicao: contar de
        # cabeca errou por um campo (leu o Encoding como MarginV). O terceiro e
        # o PrimaryColour, que a linha de estilo escreve como `{highlight}`.
        fields = [
            "fontname", "fontsize", "primary", "secondary", "outline", "back",
            "bold", "italic", "underline", "strikeout", "scaleX", "scaleY",
            "spacing", "angle", "borderstyle", "outlinew", "shadow",
            "alignment", "marginL", "marginR", "marginV", "encoding",
        ]
        style = match.group(1).split(",")
        self.assertEqual(len(style), len(fields), "a linha de estilo mudou de forma")
        engine_margin_v = dict(zip(fields, style))["marginV"]
        self.assertEqual(engine_margin_v, "60", "o MarginV do headline mudou")

        declared = re.search(r"HEADLINE_TOP_PX\s*=\s*(\d+)", self.page)
        self.assertIsNotNone(declared, "a prévia não declara a margem do headline")
        self.assertEqual(declared.group(1), engine_margin_v)

    def test_the_safe_area_number_is_shared_with_the_guide(self):
        """Uma fonte só: o guia desenhado e a decoração não podem discordar."""
        self.assertIn("SAFE_TOP_PCT = 4", self.page)
        self.assertIn("top: 4%", self.page)

    def test_the_pov_anchors_to_the_safe_area_not_the_headline_margin(self):
        """O notch pinta por cima do conteúdo e engolia a primeira linha."""
        self.assertIn("pov.style.top = SAFE_TOP_PCT", self.page)


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


class PhrasesRouteTests(unittest.TestCase):
    """/phrases sugere ganchos via o LLM do ranker, sem config nova."""

    def setUp(self):
        self.sent: dict = {}
        self.handler = object.__new__(server.Handler)
        self.handler._send_json = lambda payload, code=200: self.sent.update(payload, _code=code)

    def test_missing_idea_is_rejected(self):
        server.Handler._handle_phrases(self.handler, {})
        self.assertEqual(self.sent["_code"], 400)

    def test_phrases_come_back_as_a_list(self):
        from unittest import mock

        with mock.patch.object(server, "_suggest_phrases", return_value=["Um", "Dois"]):
            server.Handler._handle_phrases(self.handler, {"idea": "fuga de moto", "count": 2})
        self.assertEqual(self.sent["phrases"], ["Um", "Dois"])

    def test_count_is_clamped_to_ten(self):
        from unittest import mock

        seen = {}

        def record(idea, count):
            seen["count"] = count
            return ["x"]

        with mock.patch.object(server, "_suggest_phrases", side_effect=record):
            server.Handler._handle_phrases(self.handler, {"idea": "fuga de moto", "count": 999})
        self.assertEqual(seen["count"], 10)

    def test_suggest_phrases_cleans_bullets_and_quotes(self):
        from unittest import mock

        class FakeProvider:
            def complete(self, system, user):
                return '- "Primeira frase"\n2. Segunda frase\n\n'

        with mock.patch("viralclipper.ranker.build_provider", return_value=FakeProvider()):
            phrases = server._suggest_phrases("fuga de moto", 5)
        self.assertEqual(phrases, ["Primeira frase", "Segunda frase"])


class AssetContentTypeTests(unittest.TestCase):
    """Static assets need their real type, because nosniff is on.

    The pages used to be single files with everything inline; once the CSS and
    JS moved into siblings the suffix map became load-bearing, and the hero
    preview video made it wider still: a video served as
    ``application/octet-stream`` is a card that never plays, and nothing
    anywhere reports an error.
    """

    def test_video_suffix_is_a_video_type(self):
        self.assertEqual(server.asset_content_type(Path("1.mp4")), "video/mp4")

    def test_text_assets_keep_their_charset(self):
        self.assertTrue(
            server.asset_content_type(Path("index.css")).startswith("text/css")
        )
        self.assertTrue(
            server.asset_content_type(Path("index.js")).startswith("text/javascript")
        )

    def test_images_are_images(self):
        self.assertEqual(server.asset_content_type(Path("capa.jpg")), "image/jpeg")

    def test_lookup_ignores_the_case_of_the_suffix(self):
        """On Windows the suffix comes back in whatever case the file has."""
        self.assertEqual(server.asset_content_type(Path("CLIP.MP4")), "video/mp4")

    def test_unknown_suffix_stays_opaque(self):
        """Guessing a type would turn any stray asset into a script host."""
        self.assertEqual(
            server.asset_content_type(Path("dados.bin")),
            "application/octet-stream",
        )

    def test_the_route_serves_assets_through_the_helper(self):
        source = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")
        self.assertIn("ctype = asset_content_type(asset)", source)


class HeroPreviewTests(unittest.TestCase):
    """The hero preview stack: one real clip, two placeholders.

    The page is the product demo, so the first card plays an actual 9:16 clip
    instead of the schematic. What can break silently is the degradation path:
    the video sits in the repo's own ``web/`` folder and is gitignored, so a
    fresh clone has no file at all and the card has to look exactly as it did
    before — not show a broken media icon.
    """

    def setUp(self):
        self.page = page_source("index.html")
        self.markup = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_the_first_card_loads_the_clip(self):
        self.assertIn('src="/1.mp4"', self.markup)
        self.assertIn('class="preview-video"', self.markup)

    def test_the_clip_is_a_silent_loopable_inline_preview(self):
        """No audio, no controls: it is a background, not a player."""
        video = self.markup.split("preview-video", 1)[1].split("</video>", 1)[0]
        for attribute in ("muted", "playsinline", "loop", "autoplay"):
            self.assertIn(attribute, video, f"falta {attribute} no preview do hero")
        self.assertNotIn("controls", video)

    def test_the_card_starts_as_the_placeholder(self):
        """``data-live="0"`` is the start state; only the script lights it."""
        self.assertIn('id="preview-live" data-live="0"', self.markup)

    def test_the_script_lights_the_card_only_after_a_frame_decodes(self):
        self.assertIn("'loadeddata'", self.page)
        self.assertIn("'data-live'", self.page)

    def test_the_script_steps_back_when_the_file_fails(self):
        self.assertIn("addEventListener('error'", self.page)

    def test_the_clip_is_never_paused(self):
        """The card is a campaign: a frozen frame reads as a broken image.

        An earlier version paused the clip when the machine asked for reduced
        motion, and on exactly those machines the card looked static. Playback
        wins here: the element is decorative (``aria-hidden``) and muted, so a
        still frame is the only outcome nobody asked for.
        """
        self.assertNotIn("video.pause()", self.page)
        self.assertIn("video.play()", self.page)

    def test_playback_is_retried_after_a_refused_autoplay(self):
        """Muted autoplay is allowed, not guaranteed; a gesture unlocks it.

        The hooks are the whole recovery path, so all four matter: ``canplay``
        covers a late file, ``pause`` re-arms the gesture listeners after the
        browser stops the clip on its own (power saving, background tab), and
        the gesture plus ``visibilitychange`` are the two ways back in.
        """
        for hook in ("'canplay'", "'pause'", "'pointerdown'", "'visibilitychange'"):
            self.assertIn(hook, self.page, f"falta o gancho {hook} para o play")

    def test_the_preload_is_eager_so_the_loop_starts_on_frames(self):
        self.assertIn('preload="auto"', self.markup)

    def test_the_css_only_reveals_the_clip_when_live(self):
        self.assertIn('.preview-card[data-live="1"] .preview-video', self.page)
        self.assertIn('.preview-card[data-live="1"]::after', self.page)

    def test_only_the_first_card_has_a_video(self):
        self.assertEqual(self.markup.count("<video"), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
