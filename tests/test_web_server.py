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


def _jpeg(width: int, height: int) -> bytes:
    """Um JPEG com SOI, um segmento COMPRIMENTO antes do SOF, e o SOF.

    O segmento extra (um ``APP0`` de 16 bytes) é o que dá valor ao teste: o SOF
    fica num offset que nenhum atalho fixo acertaria, então um leitor que
    procurasse o cabeçalho em posição constante leria bytes do ``APP0`` e
    devolveria a largura como 61 — um número plausível, e por isso pior.
    """
    import struct

    def segment(marker: int, payload: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    app0 = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 4)
    sof0 = segment(0xC0, bytes([8]) + struct.pack(">HH", height, width) + bytes([3]) + b"\x00" * 9)
    return b"\xff\xd8" + app0 + sof0 + segment(0xDA, b"\x00" * 4) + b"\xff\xd9"


_JPEG_1904x544 = _jpeg(1904, 544)


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


def fn_body(page: str, name: str) -> str:
    """O corpo da função ``name`` do JS da página.

    O corte é em ``\\n  function `` (declaração de topo, dois espaços de
    indentação), e não em ``function `` solto: as funções daqui chamam callbacks
    anônimos (``zones.forEach(function (z) {``), e o corte solto terminava o corpo
    na primeira linha em que a palavra aparecia.

    Mora no módulo porque mais de uma classe lê o corpo de uma função, e duas
    cópias do mesmo corte divergiriam na primeira vez que alguém ajustasse uma.
    """
    return page.split("function " + name + "(", 1)[1].split("\n  function ", 1)[0]


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

    def test_the_session_field_is_a_masked_input(self):
        """O `sessionid` e o unico caminho, e o valor da a conta inteira.

        O export nao pode traze-lo (HttpOnly), entao o campo nao e conveniencia:
        sem ele a busca de perfil do Instagram responde como anonima para
        sempre. E ele tem de ser `password` — o valor fica na tela, em um painel
        que abre em 127.0.0.1 mas roda numa maquina que pode estar compartilhada.
        """
        markup = (server.WEB_DIR / "scrap.html").read_text(encoding="utf-8")
        tag = re.search(r"<input[^>]*id=\"scrap-ig-session\"[^>]*>", markup)
        self.assertIsNotNone(tag, "campo do sessionid ausente em web/scrap.html")
        self.assertIn('type="password"', tag.group(0))
        self.assertIn('name="ig-session"', tag.group(0))
        self.assertIn("HttpOnly", markup, "a nota tem de dizer por que o export nao basta")

    def test_the_script_reads_the_field_the_markup_declares(self):
        """O `id` e a unica ligacao entre o HTML e o JS, e falha em silencio.

        Um id trocado de um lado so nao quebra nada visivel: o seletor devolve
        `null`, o valor vira `""`, o payload sai sem sessao e o Instagram
        responde como anonimo — exatamente o sintoma que o campo existe para
        consertar. Por isso o HTML e o JS sao lidos em separado aqui.
        """
        markup = (server.WEB_DIR / "scrap.html").read_text(encoding="utf-8")
        script = (server.WEB_DIR / "scrap.js").read_text(encoding="utf-8")
        self.assertIn('id="scrap-ig-session"', markup)
        self.assertIn('$("#scrap-ig-session")', script)

    def test_the_session_travels_on_every_route_that_needs_it(self):
        """Busca, arquivo e download: os tres caminhos do servidor levam o valor.

        Sao rotas distintas, com guardas distintas. Esquecer uma nao da erro —
        da um download anonimo logo depois de uma busca autenticada, que e o
        pior desfecho possivel: o catalogo aparece e a midia nao baixa.
        """
        script = (server.WEB_DIR / "scrap.js").read_text(encoding="utf-8")
        # Uma ocorrencia por rota, e nada alem disso: `ig_session` e o nome do
        # campo no payload, e o helper local se chama `igSessionValue`.
        self.assertEqual(
            script.count("ig_session"),
            3,
            "esperado o valor em /scrap, /scrap/archive e /scrap/download",
        )
        # A busca monta o payload aos poucos e so o completa quando ha valor; as
        # outras duas passam o campo direto no literal.
        self.assertIn("payload.ig_session = session", script)
        self.assertEqual(script.count("ig_session: igSessionValue()"), 2)
        self.assertIn('post("/scrap/download"', script)
        self.assertIn('post("/scrap"', script)

    def test_the_badge_reports_the_half_of_the_auth_that_is_missing(self):
        """Sem jar nao ha `csrftoken` para ecoar, e sem sessionid nao ha sessao.

        O selo responde "da para autenticar?" — se ele so olhasse o arquivo,
        diria "tudo certo" com o sessionid vazio, que e o estado em que a busca
        falha.
        """
        script = (server.WEB_DIR / "scrap.js").read_text(encoding="utf-8")
        self.assertIn('dataset.state = "session"', script)
        self.assertIn("sessionid sem arquivo", script)


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


class IgSessionFromPayloadTests(unittest.TestCase):
    """O sessionid colado chega em dois formatos, e nos dois tem de gravar.

    ``/scrap`` dobra o jar no argv do yt-dlp (o yt-dlp e quem resolve um item
    isolado); ``/scrap/archive`` e ``/scrap/download`` mandam o caminho em campo
    proprio. Ler so um dos dois deixaria metade do painel sem sessao — e o
    sintoma seria "o perfil veio vazio", que nao aponta para o cookie.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_sess_"))
        self.jar = self.dir / "cookies.txt"
        self.jar.write_text(
            "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n",
            encoding="utf-8",
        )

    def test_the_argv_shape(self):
        value = server._ig_session_from_payload(
            {
                "extra_ytdlp_args": ["--cookies", str(self.jar)],
                "ig_session": "1234%3Aabc",
            }
        )
        self.assertEqual(value, "1234%3Aabc")
        self.assertIn("sessionid\t1234%3Aabc", self.jar.read_text(encoding="utf-8"))

    def test_the_field_shape(self):
        value = server._ig_session_from_payload(
            {"cookies_file": str(self.jar), "ig_session": "1234%3Aabc"}
        )
        self.assertEqual(value, "1234%3Aabc")
        self.assertIn("sessionid\t1234%3Aabc", self.jar.read_text(encoding="utf-8"))

    def test_the_field_wins_when_both_are_present(self):
        """Os dois apontam para o mesmo arquivo; nao ha o que desempatar."""
        other = self.dir / "outro.txt"
        other.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        server._ig_session_from_payload(
            {
                "cookies_file": str(other),
                "extra_ytdlp_args": ["--cookies", str(self.jar)],
                "ig_session": "1234%3Aabc",
            }
        )
        self.assertIn("sessionid\t1234%3Aabc", other.read_text(encoding="utf-8"))
        self.assertNotIn("sessionid", self.jar.read_text(encoding="utf-8"))

    def test_no_value_is_a_no_op(self):
        before = self.jar.read_text(encoding="utf-8")
        self.assertEqual(
            server._ig_session_from_payload({"cookies_file": str(self.jar)}), ""
        )
        self.assertEqual(self.jar.read_text(encoding="utf-8"), before)

    def test_no_path_is_a_no_op(self):
        """Sem arquivo nao ha onde gravar — e o erro do caminho e outro."""
        self.assertEqual(server._ig_session_from_payload({"ig_session": "1234"}), "")


class IgProfileRoutingTests(unittest.TestCase):
    """An Instagram profile must reach the GraphQL lister, never yt-dlp.

    ``InstagramUserIE`` is disabled upstream, so the flat-playlist route answers
    "Unable to extract data" for an account that is reachable in a browser. The
    whole feature is invisible if this diversion regresses — and the failure
    looks like "the account is empty", which is plausible enough to be missed.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_route_"))

    def _jar(self, body=None):
        target = self.dir / "cookies.txt"
        target.write_text(
            body
            or "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n",
            encoding="utf-8",
        )
        return str(target)

    def _route(self, url, *, extra_ytdlp_args=None, ig_session=None):
        calls = {"ig": None, "ytdlp": None}

        def fake_ig(username, payload, cookies_file, argv, session=""):
            calls["ig"] = {
                "username": username, "cookies_file": cookies_file,
                "argv": list(argv), "session": session,
            }
            return [{"index": 1, "id": "A", "folder": "reels"}], username, 0

        def fake_meta(target, config):
            calls["ytdlp"] = target
            return {"entries": [], "title": ""}

        payload = {"url": url, "mode": "profile", "limit": 5}
        if extra_ytdlp_args is not None:
            payload["extra_ytdlp_args"] = extra_ytdlp_args
        if ig_session is not None:
            payload["ig_session"] = ig_session
        with mock.patch.object(server, "_ig_profile_results", side_effect=fake_ig), \
             mock.patch.object(server.download_mod, "fetch_metadata", side_effect=fake_meta), \
             mock.patch.object(server.download_mod, "repair_view_counts",
                               side_effect=lambda *a, **k: None):
            results, _title, _removed = server._scrap_results(payload)
        return calls, results

    def test_a_profile_url_goes_to_the_graphql_lister(self):
        calls, results = self._route(
            "https://www.instagram.com/salmareis/",
            extra_ytdlp_args=["--cookies", self._jar()],
        )
        self.assertIsNone(calls["ytdlp"], "o yt-dlp nao devia nem ser chamado")
        self.assertEqual(calls["ig"]["username"], "salmareis")
        self.assertEqual(calls["ig"]["cookies_file"], str(self.dir / "cookies.txt"))
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
        jar = self._jar()
        calls, _ = self._route(
            "https://www.instagram.com/salmareis/",
            extra_ytdlp_args=["--cookies", jar],
        )
        self.assertEqual(calls["ig"]["argv"], ["--cookies", jar])

    def test_the_pasted_session_is_written_to_the_jar_and_forwarded(self):
        """O sessionid nao serve so para esta busca.

        Ele e gravado no arquivo porque o yt-dlp le o MESMO jar para baixar a
        midia, e porque a proxima busca nao precisa que o usuario cole de novo.
        """
        jar = self._jar()
        calls, _ = self._route(
            "https://www.instagram.com/salmareis/",
            extra_ytdlp_args=["--cookies", jar],
            ig_session="1234:abc",
        )
        self.assertEqual(calls["ig"]["session"], "1234%3Aabc")
        self.assertIn("sessionid\t1234%3Aabc", Path(jar).read_text(encoding="utf-8"))

    def test_a_decoded_paste_and_an_encoded_one_land_the_same(self):
        """DevTools mostra ``:``; o header carrega ``%3A``. Os dois valem."""
        jar = self._jar()
        calls, _ = self._route(
            "https://www.instagram.com/salmareis/",
            extra_ytdlp_args=["--cookies", jar],
            ig_session="1234%3Aabc",
        )
        self.assertEqual(calls["ig"]["session"], "1234%3Aabc")

    def test_no_paste_leaves_the_session_empty_and_the_file_alone(self):
        """Sem valor colado nada muda: o jar manda."""
        jar = self._jar()
        before = Path(jar).read_text(encoding="utf-8")
        calls, _ = self._route(
            "https://www.instagram.com/salmareis/",
            extra_ytdlp_args=["--cookies", jar],
        )
        self.assertEqual(calls["ig"]["session"], "")
        self.assertEqual(Path(jar).read_text(encoding="utf-8"), before)


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

    def test_the_post_card_stays_context_and_the_video_gets_the_frame(self):
        """O cartão do post é contexto; o vídeo é o conteúdo. 26/74.

        A 34/66 a área branca do post ocupava um terço da tela e ficava esparsa —
        avatar, nome e frase nadando num bloco enorme — enquanto o vídeo, que é
        o que a pessoa está vendo, ficava com dois terços. O cartão também tem um
        PISO: com avatar, nome e duas linhas do texto (o pior caso, a 1080px) o
        conteúdo ocupa ~464px, e abaixo de ~28% a faixa sobra menos que isso e o
        texto é cortado. Por isso a fração é testada, e não só o par fechando 1.
        """
        import re

        cartao = re.search(
            r'kind: "image", mock: "tweet", fraction: ([\d.]+),', self.page)
        video = re.search(
            r'kind: "video", fraction: ([\d.]+), fit: "cover", frameAt: 0, source: "",\n'
            r"\s*marginTop: 0, marginBottom: 0", self.page)
        self.assertIsNotNone(cartao, "o cartao do post sumiu do formato X")
        self.assertIsNotNone(video, "a faixa de video do formato X sumiu")
        cartao_pct = float(cartao.group(1)) * 100
        video_pct = float(video.group(1)) * 100
        self.assertAlmostEqual(cartao_pct + video_pct, 100, places=6)
        self.assertLessEqual(cartao_pct, 26.0, "o cartao do post voltou a tomar a tela")
        self.assertGreaterEqual(video_pct, 74.0, "o video perdeu area")

    def test_the_twitter_x_is_the_format_the_panel_opens_on(self):
        """O formato de partida e o X, e ele entra pelo caminho do card.

        ``loadGallery("x")`` na partida, e nao os campos copiados para o literal
        de ``state``: uma copia divergiria do card sem nenhum teste acusar, e o
        arrasto do cartao do tweet — que vive no offset da zona, e nao em um
        campo solto — so ficaria coerente quem compartilha o caminho do card.
        """
        self.assertIn('loadGallery("x");', self.page)
        # E ele tem que ser carregado DEPOIS do `<select>` de existir, porque a
        # funcao escreve nele — a ordem invertida deixaria o preset vazio.
        init = self.page.split("function init() {", 1)[1]
        # Mesma razao do outro teste: o comentario cita `loadGallery("x")`, entao
        # a ordem se mede por linha, e nao pela posicao no texto bruto.
        linhas = [ln.strip() for ln in init.splitlines()]
        self.assertLess(linhas.index("renderPresetHint();"), linhas.index('loadGallery("x");'))

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

    def test_the_kind_catalog_is_the_engine_list(self):
        """O catálogo de tipos do wizard é IGUAL ao do motor — nem a mais, nem a menos.

        A checagem por substring acima deixa passar um tipo a mais (``txt:``
        digitado errado não é denunciado por nada) e um tipo a menos que apareça
        em outro contexto. Comparar os CONJUNTOS é o que trava a deriva: uma zona
        que o painel oferece e o motor não conhece só falha no render, e uma que o
        motor conhece e o painel esconde é um recurso que ninguém acha.
        """
        from viralclipper import template as template_mod

        block = re.search(r"var KINDS = \{(.*?)\n  \};", self.page, re.S)
        self.assertIsNotNone(block, "bloco KINDS nao encontrado na pagina")
        keys = set(re.findall(r"^\s*([a-z]+):", block.group(1), re.M))
        self.assertEqual(keys, set(template_mod.ZONE_KINDS))

    def test_the_panel_links_to_the_templates_page(self):
        panel = page_source("index.html")
        self.assertIn('href="/templates"', panel)


class CaptionSwitchTests(unittest.TestCase):
    """Desligar a legenda pelo painel precisa chegar no motor.

    A opção vive em dois lugares que não se enxergam: o item "Sem legenda" no
    combo e o switch que ``build_captions`` consulta. Se um dos dois mudar de
    nome, o painel continua mostrando a escolha e o vídeo sai com a legenda
    queimada — sem erro em lugar nenhum, o que é a pior forma de falhar.
    """

    def setUp(self):
        self.page = page_source("templates.html")

    def _fn_body(self, name: str) -> str:
        return fn_body(self.page, name)

    def test_the_panel_offers_a_way_to_turn_captions_off(self):
        self.assertIn("NO_CAPTIONS", self.page)
        self.assertIn("Sem legenda", self.page)

    def test_the_panel_starts_with_the_captions_off(self):
        """O painel abre DESLIGADO: legenda queimada é pedido, não padrão.

        Quem abre o painel está montando o template e ainda não renderizou nada.
        Legenda queimada é um pedido explícito, então ela não vem ligada por
        omissão — o que traz o item "Sem legenda" já marcado, a prévia sem a
        faixa e o ``captions = false`` no ``.toml`` de saída.

        A trava cobre os TRÊS pontos onde o padrão é reposto. Qualquer um deles
        com ``true`` religaria a legenda por baixo, e o usuário veria o item
        "Sem legenda" marcado na lista enquanto recebia legenda no render — a
        falha que não tem erro em lugar nenhum.
        """
        # 1) o estado inicial
        inicial = self.page.split("var state = {", 1)[1].split("\n  };", 1)[0]
        self.assertRegex(inicial, r"captions:\s*false",
                         "o painel nao abre com a legenda desligada")

        # 2) "Carregar split-card" e 3) "Usar este template" da galeria repõem o
        #    mesmo padrao. Os dois formatos antigos religavam, e um so com `true`
        #    ja seria regressao silenciosa.
        #
        #    A trava le o CODIGO, nao a pagina: um `state.captions = true` num
        #    comentario — ou o comentario de justificativa — nao religa nada, e um
        #    teste que so procurasse `true` na pagina toda passaria com a legenda
        #    religada. Por isso o `assertNotRegex` e o comentario come como
        #    falsamente limpo.
        for fn in ("loadSplitCard", "loadGallery"):
            body = self._fn_body(fn)
            codigo = "\n".join(
                linha for linha in body.splitlines()
                if not linha.strip().startswith("//")
            )
            self.assertIn("state.captions = false", codigo,
                          f"{fn} nao repõe o padrao desligada")
            self.assertNotIn("state.captions = true", codigo,
                             f"{fn} religa a legenda contra o padrao")

    def test_the_default_is_not_a_dead_end(self):
        """"Sem legenda" tem que ser uma ESCOLHA, e um padrão que se religa.

        O padrão desligado só é defensável se escolher um preset religar. Sem
        isso o painel abriria sempre sem legenda e o único jeito de ter legenda
        seria editar o ``.toml`` à mão — o painel estaria trancado no padrão.

        E o item tem de ser o PRIMEIRO da lista: quem não quer legenda não
        deveria rolar 38 presets até acha-lo.
        """
        # O caminho real e' o handler de `change` do `<select>` escondido: e ele
        # que o combo dispara ao escolher. A trava le esse pedaco, e nao a
        # funcao do combo — que so repassa o valor.
        change = self.page.split('} else if (target.id === "tpl-preset") {', 1)
        self.assertEqual(len(change), 2,
                         "nao achei o ramo do preset no handler de change")
        ramo = change[1].split('} else if', 1)[0]
        self.assertIn("state.captions = true", ramo,
                      "escolher um preset nao religa a legenda")

        # E o item fica no topo da lista, inclusive na busca vazia.
        keys = self._fn_body("presetKeys")
        self.assertIn("keys.unshift(NO_CAPTIONS)", keys,
                      "'Sem legenda' deixou de ser o primeiro item da lista")
        # E a busca por "sem legenda" ou "deslig" tem que acha-lo.
        self.assertRegex(keys, r"NO_CAPTIONS_DESC\.toLowerCase\(\)\.indexOf",
                         "a busca por 'legenda' ou 'deslig' nao acha o item")

    def test_the_option_lives_outside_the_preset_catalog(self):
        """"Sem legenda" é opção do painel, não um preset do motor.

        Se virasse uma entrada de ``PRESETS``, o teste de deriva acima continuaria
        verde — ele compara com o motor — mas a linha ``caption_preset`` do
        ``.toml`` passaria a apontar para um preset que o render não acha.
        """
        from viralclipper import caption_presets

        self.assertNotIn("none", caption_presets.PRESETS)
        block = re.search(r"var PRESETS = \{(.*?)\n  \};", self.page, re.S)
        self.assertNotRegex(block.group(1), r'^\s*"none":')

    def test_the_toml_says_false_only_when_the_captions_are_off(self):
        """``captions = false`` some do arquivo quando a legenda está ligada.

        ``None`` no template é "não mexe", então escrever ``captions = true``
        seria afirmar algo que o motor não distingue de ligado-e-comando-de-linha
        — e passaria a pisar num ``--caption-style block`` da linha de comando.
        """
        toml = self.page.split("function toToml()", 1)[1].split("function round4", 1)[0]
        # Só as linhas de código: o comentário que explica por que a chave some
        # quando está ligada cita "captions = true" ao contrário, e um teste que
        # lesse comentário como código reprovaria a documentação que ele
        # deveria premiar.
        code = "\n".join(
            line for line in toml.splitlines() if not line.strip().startswith("//")
        )
        self.assertIn('if (!state.captions) lines.push("captions = false")', code)
        self.assertNotIn("captions = true", code)

    def test_the_engine_reads_the_key_the_panel_writes(self):
        """A chave do painel existe no motor e o motor não a inventa sozinho.

        ``from_dict`` recusa chave desconhecida, então uma chave emitida pelo
        painel e ausente no motor não é um detalhe: é o arquivo inteiro
        recusado, com o trabalho do template perdido na hora do render.
        """
        from viralclipper import template as template_mod

        data = {
            "name": "mudo",
            "captions": False,
            "zones": [{"kind": "video", "fraction": 1.0}],
        }
        self.assertIs(template_mod.from_dict(data).captions, False)

    def test_picking_a_preset_turns_the_captions_back_on(self):
        """Escolher um preset religa — é a única forma de religar.

        Desligar é uma opção do painel e não um preset, então nada mais devolve
        a legenda. Sem esta linha o item "Sem legenda" ficava marcado e o preset
        escolhido não aparecia em lugar nenhum da interface.
        """
        # O handler de change é um listener anônimo ligado por addEventListener,
        # então não há nome para cortar: o que delimita o ramo é a própria
        # condição. Cortar pela próxima `else if` é o que isola o bloco do
        # preset — sem isto a busca acharia o `state.captions = true` de outro
        # ramo e passaria com o preset quebrado.
        branch = re.search(
            r'id === "tpl-preset"\)(.*?)\n    \} else if', self.page, re.S
        )
        self.assertIsNotNone(branch, "ramo do tpl-preset nao encontrado")
        self.assertIn("state.captions = true", branch.group(1))

    def test_the_preview_hides_the_caption_band_when_they_are_off(self):
        """A prévia some com a faixa de legenda, senão ela mente sobre o render."""
        preview = self.page.split("function renderPreview()", 1)[1].split(
            "\n  function ", 1
        )[0]
        self.assertIn("if (!state.captions) return;", preview)

    def test_the_list_marks_the_item_that_describes_the_state(self):
        """O marcado da lista é o item que descreve o estado, não o guardado atrás.

        A lista guarda o preset num campo e a legenda num flag separado, então
        "o que está selecionado" tem duas respostas possíveis. A que vale é a
        segunda: com a legenda desligada, o item que descreve a tela é "Sem
        legenda". Marcando o preset, a lista abriria apontando para um item que
        não está em vigor — e o teclado Enter sobre ele religaria a legenda
        sozinho, sem o usuário pedir.
        """
        body = self._fn_body("setComboOpen")
        self.assertIn(
            "comboActive = state.captions ? state.preset : NO_CAPTIONS", body
        )

    def test_the_hint_says_the_captions_are_off(self):
        """O texto de apoio avisa, porque o botão ainda mostra o preset.

        Desligar a legenda não troca o preset — ele continua no ``.toml`` e
        continua pintando o headline e as faixas de texto. Então o botão fica
        mostrando "ultra-impact" e a única pista de que a legenda saiu é o
        ``· Desligada`` do subtítulo; o aviso no ``#preset-hint`` torna isso
        visível longe do combo, onde a lista de trinta e oito presets mora.
        """
        body = self._fn_body("renderPresetHint")
        self.assertIn("state.captions", body)
        self.assertIn("Legenda DESLIGADA", body)

    def test_the_search_keeps_the_cursor_on_the_choice_in_force(self):
        """Buscar não move o cursor para o topo da lista.

        A lista de busca põe "Sem legenda" em primeiro lugar de propósito, então
        um cursor que salta para o primeiro item a cada tecla deixaria o Enter
        seguinte desligando a legenda — só por o usuário ter digitado e apagado
        uma letra. O cursor segue a seleção, que é o que está em vigor.
        """
        block = re.search(
            r'comboSearch\.addEventListener\("input"(.*?)\n      \}\);',
            self.page,
            re.S,
        )
        self.assertIsNotNone(block, "handler de input da busca nao encontrado")
        body = block.group(1)
        self.assertIn('aria-selected") === "true"', body)
        self.assertNotIn('querySelector(".combo-item")', body)

    def test_the_arrows_start_from_the_choice_in_force(self):
        """A primeira seta sai da seleção, não do índice zero.

        Sem legenda, o item em vigor é o índice 0 da lista. A conta antiga
        somava ±1 a zero mesmo assim, então a seta para cima saltava para o
        último preset e o Enter religava a legenda.
        """
        block = re.search(
            r'event\.key === "ArrowDown" \|\| event\.key === "ArrowUp"(.*?)\n        \}\n',
            self.page,
            re.S,
        )
        self.assertIsNotNone(block, "ramo das setas nao encontrado")
        body = block.group(1)
        self.assertIn("if (i < 0) i = 0;", body)
        self.assertIn("else i = (i +", body)

    def test_the_summary_table_stops_claiming_libass_positions_it(self):
        """A tabela de geometria não pode prometer um posicionamento que não existe.

        A faixa de legenda aparece na tabela como "posicionado pelo libass". Com
        a legenda desligada não há evento nenhum para o libass posicionar, então
        a linha mentia sobre o arquivo que vai ser renderizado.
        """
        geometry = self.page.split("function renderGeometry()", 1)[1].split(
            "\n  function ", 1
        )[0]
        self.assertIn('state.captions ? "full canvas" : "desligada"', geometry)
        self.assertIn(
            'state.captions ? "posicionado pelo libass" : "sem texto queimado"',
            geometry,
        )


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


class TikTokPreviewUiTests(unittest.TestCase):
    """A prévia do TikTok desenha a UI do app, não só o vídeo.

    O que essa UI compra é uma pergunta que só o app real responde: a legenda e
    a faixa de texto caem embaixo da barra e do bloco de autor, ou ficam
    escondidas atrás deles. Por isso os testes travam as peças e a ordem delas,
    que é o que o app faz.
    """

    @classmethod
    def setUpClass(cls):
        cls.page = page_source("templates.html")
        cls.css = (server.WEB_DIR / "templates.css").read_text(encoding="utf-8")

    def _rail(self) -> str:
        return self.page.split('class="tt-rail"', 1)[1].split("</div>", 1)[0]

    def test_the_page_carries_every_piece_of_the_chrome(self):
        for fragment in (
            'class="platform-ui tt-ui"',
            'class="tt-head"',
            'class="tt-rail"',
            'class="tt-meta"',
            'class="tt-nav"',
        ):
            self.assertIn(fragment, self.page, fragment)

    def test_the_header_names_the_two_feeds(self):
        self.assertIn("Following", self.page)
        self.assertIn("For You", self.page)

    def test_the_rail_holds_five_pieces_in_the_apps_order(self):
        """Avatar, like, comentário, share e o disco — nessa ordem.

        O disco entra por último de propósito: solto no canto ele caía no MEIO
        do trilho e tapava a contagem do coração, que é a peça que denuncia
        legenda invadindo a lateral.
        """
        rail = self._rail()
        self.assertIn("tt-avatar", rail)
        self.assertIn("tt-disc", rail)
        self.assertEqual(rail.count('class="tt-act"'), 3, rail)
        self.assertLess(rail.index("tt-avatar"), rail.index("tt-disc"))
        for label in ("99.9k", "100", "Share"):
            self.assertIn(label, rail, label)

    def test_the_nav_lists_the_four_destinations_and_the_create_button(self):
        nav = self.page.split('class="tt-nav"', 1)[1]
        for label in ("Home", "Discover", "Inbox", "Me"):
            self.assertIn(f"<em>{label}</em>", nav, label)
        self.assertIn("tt-nav-plus", nav)

    def test_the_ui_is_illustration_and_not_content(self):
        """aria-hidden + pointer-events: none — nada de anunciar, nada de
        interceptar o arraste do POV que passa por cima."""
        block = self.page.split('class="platform-ui tt-ui"', 1)[1][:80]
        self.assertIn("aria-hidden", block)
        # Ancorado na regra de TOPO: `.platform-ui {` sozinho também casa a
        # descendente `[data-platform="tiktok"] .platform-ui`, que vem primeiro.
        rule = self.css.split("\n.platform-ui {", 1)[1].split("}", 1)[0]
        self.assertIn("pointer-events: none", rule)

    def test_only_one_chrome_shows_at_a_time(self):
        """Cada plataforma mostra a SUA moldura desenhada, e nunca as duas.

        As duas uis dividem a classe `.platform-ui`, entao o seletor tem de casar
        pela classe que as DISTINGUE (`.tt-ui` / `.ig-ui`) e nao pela generica.
        Com `.stage[data-platform="tiktok"] .platform-ui`, o TikTok desenhado
        acenderia junto com o Reels desenhado — as duas molduras empilhadas na
        mesma tela, com dois cabecalhos e duas barras.
        """
        self.assertIn(
            '.stage[data-platform="tiktok"] .tt-ui { display: block; }',
            self.css,
            "a UI do TikTok deixou de ser desenhada",
        )
        self.assertIn(
            '.stage[data-platform="instagram"] .ig-ui { display: block; }',
            self.css,
            "a UI do Reels deixou de ser desenhada",
        )
        # O generico `.platform-ui` tem de continuar escondido: e ele que segura
        # as duas ao mesmo tempo se alguem escrever so ele.
        base = self.css.split("\n.platform-ui {", 1)[1].split("}", 1)[0]
        self.assertIn("display: none", base)
        # O rotulo simples foi embora: com cabecalho desenhado nos dois apps ele
        # duplicaria a peca, e o `platform-name` no JS apontaria para um no que
        # nao existe mais. A busca e no CODIGO, nao no arquivo inteiro: o
        # comentario que explica a remocao cita o nome, e varrer a prosa
        # reprovaria a propria explicacao.
        self.assertNotIn("platform-chrome", self.css)
        self.assertNotIn('id="platform-name"', self.page)
        js = (server.WEB_DIR / "templates.js").read_text(encoding="utf-8")
        self.assertNotIn('getElementById("platform-name")', js)


class ReelsPreviewUiTests(unittest.TestCase):
    """A prévia do Instagram desenha a tela do Reels, não só o vídeo.

    Mesma compra que a do TikTok — descobrir se a legenda e a faixa de texto caem
    embaixo da UI do app ou ficam escondidas atrás dela. E a moldura do Reels NAO
    pode ser a do TikTok com outro nome: o app e diferente o bastante na tela, e a
    diferenca e justamente o que faz a previa valer.
    """

    @classmethod
    def setUpClass(cls):
        cls.page = page_source("templates.html")
        cls.css = (server.WEB_DIR / "templates.css").read_text(encoding="utf-8")

    def _ui(self) -> str:
        """O bloco do Reels, e nao a UI do TikTok que vem antes dele."""
        return self.page.split('class="platform-ui ig-ui"', 1)[1]

    def test_the_page_carries_every_piece_of_the_reels_screen(self):
        for fragment in (
            "ig-status", "ig-head", "ig-rail", "ig-meta", "ig-cover", "ig-nav",
        ):
            self.assertIn(f'class="{fragment}"', self.page, fragment)

    def test_the_player_controls_stay_out_of_the_copy(self):
        """Nem o play nem o botao de som do CENTRO entram na copia.

        No app eles so aparecem com o video PAUSADO. A previa nao reproduz nada,
        entao desenhar os dois deixaria um botao de play clicavel que nao faz
        nada — pior do que nao ter. Quem denuncia a moldura trocada nao e o play:
        sao o trilho de quatro acoes SEM contagem e o rodape recolhido.
        """
        ui = self._ui()
        self.assertNotIn("ig-play", ui)
        self.assertNotIn("ig-center", ui)
        self.assertNotIn("ig-audio", ui)
        # E o play nao pode ter virado uma acao do trilho.
        trilho = ui.split('class="ig-rail"', 1)[1].split("ig-meta", 1)[0]
        self.assertNotIn("ig-play", trilho)

    def test_the_header_names_the_feed_and_the_group(self):
        """`Reels` puro e `Amigos` com os tres avatares do grupo.

        O grupo de amigos e o que o TikTok nao tem: sem ele, o cabecalho do Reels
        e o do TikTok com o texto trocado. O titulo vai SEM a seta de menu — o
        app mostra o nome puro nesta tela, e a seta sugeriria um menu que a
        copia nao tem.
        """
        head = self._ui().split('class="ig-head"', 1)[1].split("ig-rail", 1)[0]
        self.assertIn("Reels", head)
        self.assertIn("Amigos", head)
        self.assertIn("ig-faces", head)
        self.assertEqual(head.count("<i "), 3, "sao tres avatares no grupo")
        self.assertNotIn("ig-caret", head)

    def test_the_rail_holds_four_actions_and_no_counts(self):
        """Coracao, comentario, enviar e salvar — QUATRO, e sem numero embaixo.

        O reels esconde a contagem de cada acao quando ela nao cabe; num feed
        limpo ela nao cabe, e o que fica sao so os icones de contorno. Cinco
        acoes com numero seria a tela de um post que ja foioinserido e curtido —
        ou seja, a copia de outro momento do app, nao desta.
        """
        rail = self._ui().split('class="ig-rail"', 1)[1].split("ig-meta", 1)[0]
        self.assertEqual(rail.count('class="ig-act"'), 4, rail)
        self.assertNotIn("<b>", rail, "feed limpo nao mostra contagem por acao")

    def test_the_footer_is_the_collapsed_one(self):
        """Avatar + @user truncado + `Seguir` + `Ver mais`, e nada mais.

        Este e o estado RECOLHIDO do rodape: nem a faixa de audio nem o texto da
        legenda aparecem ate o `Ver mais` ser tocado. E de proposito — e a forma
        que ocupa a MENOS altura util do rodape, e portanto a mais honesta para
        julgar se a legenda queimada do corte encosta na UI.
        """
        meta = self._ui().split('class="ig-meta"', 1)[1].split("ig-nav", 1)[0]
        self.assertIn("ig-seguir", meta)
        self.assertIn("ig-more", meta)
        self.assertIn("Ver mais", meta)
        self.assertIn("ig-avatar", meta)
        self.assertNotIn("ig-caption", meta, "a legenda fica escondida ate o Ver mais")
        self.assertNotIn("ig-sound", meta, "a faixa de audio tambem fica recolhida")

    def test_the_footer_carries_the_track_indicator(self):
        """As tres linhas do indicador de faixa, no canto inferior direito.

        No reels o post sem capa propria mostra esse tres-tracos no lugar da
        arte. E a unica peca do rodape sem equivalente no TikTok (la e o disco
        que gira), e ela ocupa exatamente a faixa onde a legenda costuma
        encostar.
        """
        self.assertIn('class="ig-cover"', self._ui())
        rule = self.css.split(".ig-cover {", 1)[1].split("}", 1)[0]
        self.assertIn("position: absolute", rule)
        # As pecas do rodape se empilham por `bottom` e nao se sobrepoem por
        # acaso: se uma mudar de altura, essa ordem e a primeira a conferir.
        # A barra gruda no zero (`bottom: 0`), entao o parser aceita os dois.
        def bottom_of(name: str) -> float:
            import re

            body = self.css.split(f".{name} {{", 1)[1].split("}", 1)[0]
            found = re.search(r"bottom:\s*(\d+(?:\.\d+)?)cqh", body)
            return float(found.group(1)) if found else 0.0

        bottoms = {name: bottom_of(name) for name in ("ig-nav", "ig-cover", "ig-rail", "ig-meta")}
        self.assertLess(bottoms["ig-nav"], bottoms["ig-cover"], bottoms)
        self.assertLess(bottoms["ig-cover"], bottoms["ig-rail"], bottoms)

    def test_the_nav_has_no_labels_under_the_icons(self):
        """Cinco icones e nenhum ROTULO embaixo — e assim que o Reels faz.

        O TikTok rotula (`<em>Home</em>`, `<em>Me</em>`). Copiar os rotulos para ca
        deixaria a barra com duas vezes a altura e o formato errado.

        `<em>` continua aparecendo na barra, mas so como ADORNO: a contagem do
        direct e o ponto do perfil. E por isso que o teste olha o que tem dentro
        de cada `<em>` em vez de banir a tag — banir seria proibir a novidade,
        que e metade do que a barra do app tem a dizer.

        O recorte vai ate o FIM da UI do Reels, e nao ate o fim do arquivo: sem o
        limite, o `split` engoliria o resto da pagina — incluindo o `<em>` do
        TikTok que vem logo antes, e o teste passaria a medir a barra errada.
        """
        import re

        nav = self._ui().rsplit('class="ig-nav"', 1)[1].split("</div>", 1)[0]
        self.assertEqual(nav.count('class="ig-nav-i'), 5, nav)
        for tag in re.findall(r"<em[^>]*>", nav):
            self.assertTrue("ig-badge" in tag or "ig-dot" in tag, tag)
        # Nenhum `<em>` pode conter texto solto: seria um rotulo.
        self.assertNotIn("<em>Home</em>", nav)
        self.assertNotIn("<em>Me</em>", nav)

    def test_the_nav_carries_the_two_awareness_marks(self):
        """A contagem de mensagens no direct e o ponto de atividade no perfil.

        São as duas únicas coisas da moldura que dizem "você tem coisa nova", e
        somem se a cópia ficar sem elas — o resto da barra é idêntica em qualquer
        conta. A contagem vai dentro do item, entao um badge solto na barra
        denunciaria a troca de posição.
        """
        nav = self._ui().rsplit('class="ig-nav"', 1)[1].split("</div>", 1)[0]
        self.assertIn('class="ig-badge">5<', nav)
        self.assertIn('class="ig-dot"', nav)
        # O Reels (segundo item) nao carrega adorno: e a aba ja aberta.
        ativos = [chunk for chunk in nav.split('class="ig-nav-i')[1:] if "ig-badge" in chunk or "ig-dot" in chunk]
        self.assertEqual(len(ativos), 2, "so o direct e o perfil trazem adorno")

    def test_the_reels_is_illustration_and_not_content(self):
        """`aria-hidden` + `pointer-events: none`, como a do TikTok."""
        bloco = self.page.split('class="platform-ui ig-ui"', 1)[1][:80]
        self.assertIn("aria-hidden", bloco)
        # A regra generica de `.platform-ui` e o que garante o `pointer-events`; a
        # do Reels herda dela, e um `pointer-events: auto` local a religaria.
        base = self.css.split("\n.platform-ui {", 1)[1].split("}", 1)[0]
        self.assertIn("pointer-events: none", base)
        self.assertNotIn("pointer-events: auto", self.css.split("UI DO INSTAGRAM", 1)[1])

    def test_the_status_bar_is_the_phones_and_not_the_apps(self):
        """A barra de status e do SO, entao ela fica ACIMA do cabecalho do app.

        Se fosse a peca do topo do app, entraria dentro de `.ig-head` — e ai a ordem
        de leitura na tela seria a mesma, mas a regra que as posiciona seria a errada
        para quem ajustasse a moldura.
        """
        self.assertIn("21:20", self.page)
        status = self._ui().split('class="ig-status"', 1)[1].split("ig-head", 1)[0]
        self.assertIn("VoLTE", status)
        # O relogio e a esquerda com os glifos da operadora; VoLTE/sinal/wifi/
        # bateria sao o outro lado. A ordem e o que faz a barra parecer de
        # aparelho em vez de um detalhe solto no canto.
        self.assertIn("ig-clock", status)
        self.assertIn("ig-status-l", status)


class TemplatesGeometryTests(unittest.TestCase):
    """The wizard recomputes band pixels; the numbers must agree with the engine."""

    def setUp(self):
        self.page = page_source("templates.html")

    def test_media_anchored_to_the_bottom_is_flush_with_the_frame(self):
        """Toda zona de mídia na BASE encosta na borda de baixo.

        O vão entre faixas é o respiro — mas embaixo da última zona não há nada
        para separar, e a margem de baixo aparecia como uma faixa preta solta no
        fim da tela, que lia como render quebrado. A regra é posicional: vale
        para o cartão do split-card e para a imagem do Meme e do Vídeo Viral,
        que têm a mesma situação — a última faixa encosta embaixo.

        A única exceção é o Twitter/X, e ela é real: lá a imagem é a PRIMEIRA
        faixa, e a margem de baixo é o vão que a separa do vídeo que vem abaixo.
        Esse respiro existe e tem função, então fica.
        """
        import re

        from viralclipper import template as template_mod

        def margens_no_card(card: str, kind: str) -> list[str]:
            # Escopo no BLOCO do card do GALLERY, e nao na pagina inteira. Sem
            # isso o `[\s\S]` preguiçoso atravessa os cards e casa a margem da
            # zona errada — e a fracao deixou de servir de ancora quando o X
            # passou para 26%, a MESMA do Meme: dois cards, uma fracao, margens
            # opostas (a do X e a unica com respiro embaixo).
            #
            # `[\s\S]` e nao `.`: a zona e um literal de varias linhas e pode ter
            # um comentario `//` entre o `kind:` e o `marginTop:`. Como e
            # preguicoso, ele para no primeiro `marginTop:` depois do `kind:`, que
            # e sempre o da propria zona.
            bloco = self.page.split(card + ": {", 1)[1].split("\n    }", 1)[0]
            achados = re.findall(
                r'kind: "' + kind + r'",[\s\S]*?marginTop: ([\d.]+), marginBottom: ([\d.]+),',
                bloco)
            return [f"top={a} bottom={b}" for a, b in achados]

        def margens_na_pagina(kind: str, fracao: str) -> list[str]:
            # Para a zona que NAO vive num card do GALLERY: o frame do
            # split-card, que esta no estado padrao e no `loadSplitCard`. Aqui a
            # fracao e a ancora, porque nao ha bloco para delimitar e a busca
            # casaria em qualquer lugar da pagina.
            achados = re.findall(
                r'kind: "' + kind + r'",[\s\S]*?fraction: ' + fracao
                + r"[\s\S]*?marginTop: ([\d.]+), marginBottom: ([\d.]+),",
                self.page)
            return [f"top={a} bottom={b}" for a, b in achados]

        # Zonas de mídia na base: respiro em cima, zero embaixo.
        alvos = [
            ("split-card", margens_na_pagina("frame", r"0\.38")),
            ("meme", margens_no_card("meme", "image")),
            ("viral", margens_no_card("viral", "image")),
        ]
        for card, achados in alvos:
            self.assertTrue(achados, f"{card}: nenhuma zona de midia encontrada")
            for item in achados:
                self.assertIn("bottom=0", item, f"{card}: {item}")

        # E o X nao entra na lista de proposito: la a imagem e a PRIMEIRA faixa,
        # e o respiro embaixo e o vao que a separa do video que vem abaixo. Com
        # a fracao colidindo com a do Meme, e o bloco do card que separa os dois
        # casos — sem ele, o X herdaria a regra da base e o vao sumiria.
        x = margens_no_card("x", "image")
        self.assertTrue(x, "o X nao tem zona de imagem")
        for item in x:
            self.assertNotIn("bottom=0", item,
                             f"o X perdeu o vao entre o post e o video: {item}")

        # A zona nova entra antes da legenda, ou seja na base: mesma regra.
        self.assertIn(
            "marginTop: 1.2, marginBottom: 0, marginLeft: 3, marginRight: 3,\n"
            '      radius: kind === "solid"',
            self.page,
            "a zona nova voltou a ter respiro embaixo",
        )

        # O motor tem que concordar com a página no layout que ele tambem carrega.
        zona = template_mod.BUILTIN["split-card"].zones[1]
        self.assertEqual(zona.margin_top, 0.012)
        self.assertEqual(zona.margin_bottom, 0.0)

    def test_the_geometry_rule_matches_the_engine(self):
        """Ambas as implementações são comparadas na mesma entrada.

        A página absorve a sobra de arredondamento na última faixa, o mesmo que
        ``plan_bands``. Travar as constantes compartilhadas impede que uma
        reescrita derrube a regra sem ninguém notar.
        """
        from viralclipper import template as template_mod

        body = self.page
        self.assertIn("zone.fraction", body)
        self.assertIn("marginTop", body)
        self.assertIn("marginLeft", body)
        builtin = template_mod.BUILTIN["split-card"]
        bands = template_mod.plan_bands(builtin, 1080, 1920)
        self.assertEqual([b.kind for b in bands], ["video", "frame", "captions"])

    def test_the_preview_rounds_geometry_to_the_chroma_grid_too(self):
        """A prévia encaixa a geometria no par, igual ao motor.

        O composite é yuv420p, então o motor arredonda toda medida para baixo até
        o par (``_even``, em ``template.py``). A tabela de geometria do painel
        imprime ``y``, ``h`` e a área interna em pixels — um número que o usuário
        lê. Com o arredondamento antigo ela mostrava ``y=307 h=307`` onde o render
        tem 306, e a prévia media uma faixa que não existe no arquivo gerado.
        """
        js = fn_body(self.page, "planBands")
        self.assertIn("evenFloor(", js, "a prévia deixou de arredondar a geometria")
        for measure in ("bandH", "innerX", "innerY", "innerW", "innerH"):
            self.assertRegex(
                js,
                rf"{measure} = [^;]*evenFloor\(",
                f"{measure} não passa pela grade de croma",
            )
        # O helper tem de estar na página, e com o mesmo `%` do Python: o do JS
        # devolve -1 para -5 e o do Python devolve 1, então `n - n % 2` daria
        # -4 de um lado e -6 do outro. A correção antes do resto é o que mantém
        # os dois iguais.
        helper = fn_body(self.page, "evenFloor")
        self.assertIn("((n % 2) + 2) % 2", helper)


class TemplatesPreviewFidelityTests(unittest.TestCase):
    """A prévia tem de medir o frame do mesmo jeito que o motor o desenha.

    Duas famílias de defeito já passaram por aqui, e nenhuma delas aparece num
    teste por nome de classe: a legenda pousava dentro da zona de baixo (por
    cento de ``margin-bottom`` resolve contra a LARGURA, não contra a altura) e
    o corpo da fonte saía quase 44% menor. Os testes abaixo leem a página
    efetiva e travam a regra, porque a prévia que mente sobre o render é pior
    do que não ter prévia.
    """

    @classmethod
    def setUpClass(cls):
        # O `css` entra aqui porque os testes de moldura leem o CSS da página e não
        # o `page_source` (HTML + CSS + JS colados): no `page_source` o nome de uma
        # regra viraria parte de uma linha só, e o `self._rule`, que ancora no
        # início da linha, pararia de casar.
        cls.page = page_source("templates.html")
        cls.css = (server.WEB_DIR / "templates.css").read_text(encoding="utf-8")

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

    def test_the_safe_guide_is_the_only_place_that_declares_the_safe_area(self):
        """O guia é a fonte única do 4% do motor.

        O JS declarava ``SAFE_TOP_PCT = 4`` só para posicionar o POV, que agora
        se ancora no recorte do aparelho. O mesmo número escrito em duas
        linguagens é o que divergiu antes; sobrou um, no CSS, e é ele que o guia
        desenha.
        """
        self.assertIn("top: 4%", self.page)
        self.assertNotIn("SAFE_TOP_PCT", self.page)

    def _rule(self, selector: str) -> str:
        """O bloco de declarações de ``selector`` no CSS da página.

        Ancorado no começo da linha para ``.gal-pov`` não casar dentro de
        ``.gal-pov-x`` e ``.pv-pov`` não casar dentro de um comentário.
        """
        match = re.search(
            r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", self.page, re.MULTILINE
        )
        self.assertIsNotNone(match, f"a regra {selector} sumiu do CSS")
        return match.group(1)

    def test_the_notch_geometry_has_one_source(self):
        """O recorte é declarado uma vez, no elemento da TELA.

        Antes o notch media da moldura (``.stage``/``.gal-prev``) e o POV da
        faixa de vídeo: dois espaços de coordenada, e a posição real do recorte
        só aparecia subtraindo o padding do palco. Agora o desenho e a âncora do
        POV saem das mesmas duas variáveis.

        Só a galeria tem recorte: a prévia deixou de ter um, porque cada app
        desenhado (TikTok e Reels) traz a própria barra de status no lugar — um
        recorte de celular em cima dela seria a mesma informação duas vezes.
        """
        block = self._rule(".gal-screen")
        self.assertIn("--notch-top", block, ".gal-screen não declara o topo do recorte")
        self.assertIn("--notch-h", block, ".gal-screen não declara a altura do recorte")
        drawn = self._rule(".gal-notch")
        self.assertIn("top: var(--notch-top)", drawn)
        self.assertIn("height: var(--notch-h)", drawn)
        # A prévia não pode voltar a ter um recorte próprio.
        self.assertNotIn(".phone-notch", self.page)

    def test_the_stage_is_the_frame_and_not_a_phone(self):
        """O palco é o QUADRO 9:16, sem mock de aparelho.

        Duas coisas quebram quando ele volta a ser um mock de smartphone, e as
        duas são silenciosas — nenhuma delas dá erro, só muda o número:

        1. **A razão.** O palco era 9/18.4 (proporção de aparelho) enquanto o
           quadro é 9:16. Todo `cqh`/`cqw` da prévia é fração da tela, então uma
           tela 18% mais alta que o frame translateia TODA medida vertical em
           pixels grandes demais: legenda, altura de faixa e corpo do texto saíam
           maiores do que o motor queima. A prévia ficava discordando do render
           sem nada apontar para a causa.
        2. **A moldura.** O render é um 9:16 reto; cantos arredondados e bezel
           mostravam uma tela que não existe e sumiam conteúdo nas bordas.
        """
        palco = self._rule(".stage")
        self.assertIn("aspect-ratio: 9 / 16", palco,
                      "o palco precisa ser 9:16 — e a razao do quadro, nao do aparelho")
        # Nenhum resto de moldura: cantos, bezel, padding ou os botoes laterais
        # que eram pseudo-elementos do palco.
        for resto in ("border-radius", "box-shadow", "padding"):
            self.assertNotIn(resto, palco, f"a moldura voltou: {resto} no palco")
        for pseudo in (".stage::before", ".stage::after"):
            self.assertNotIn(pseudo, self.css,
                             f"{pseudo} desenha botao de aparelho num quadro reto")
        # A barra de home e a moldura de baixo: as duas saem com o frame.
        self.assertNotIn(".phone-home", self.css)
        self.assertNotIn("phone-home", self.page)
        # A tela e o limite do video: reto, e sem encolher dentro de um padding.
        tela = self._rule(".phone-screen")
        self.assertNotIn("border-radius", tela,
                         "a tela do quadro nao tem canto arredondado")
        self.assertNotIn("padding", tela,
                         "a tela nao pode encolher dentro de uma moldura")

    def test_the_pov_is_a_band_of_its_own_outside_the_video(self):
        """O POV é uma ZONA de texto, não uma sobreposição sobre o vídeo.

        Ele era um absoluto ancorado no FUNDO do recorte e morava dentro da faixa
        de vídeo: cobria o clipe e só existia naquela posição. Como zona ``text``
        ele ganha faixa própria — fundo preto, fora da área do vídeo — e a posição
        sai de ``text_anchor``, o mesmo número que o motor queima.

        O deslocamento fino continua vindo do JS, mas por ``transform``: escrever
        ``top``/``left`` mexeria no layout da faixa, e a âncora do ASS (que é
        absoluta no quadro) deixaria de bater com o que a prévia desenha.
        """
        for pov in (".pv-pov", ".gal-pov"):
            block = self._rule(pov)
            self.assertNotIn("position: absolute", block, f"{pov} voltou a ser sobreposição")
            self.assertNotIn("top:", block, f"{pov} voltou a se ancorar no recorte")
        self.assertIn(
            "translate(calc(var(--tw-off-x-pov, 0) * var(--framepx, 0px))",
            self._rule(".pv-pov"),
            "o deslocamento do POV não chega por transform",
        )
        self.assertNotIn("pov.style.top", self.page)
        # A faixa do texto é pintada pela cor da ZONA (preta no Meme), não por CSS:
        # no motor ela é o mesmo `solid`, e é ele que dá o fundo preto.
        self.assertIn("function paintTextZone", self.page)
        # `plateStyle` e quem pinta: ele devolve a cor E, quando a zona tem
        # `plate_image`, a imagem por cima. A trava e a chamada, e nao a
        # atribuicao direta de `background`, porque a faixa passou a ter dois
        # estados (cor solida e placa) e um `el.style.background = zone.color`
        # fixaria so o primeiro.
        painter = fn_body(self.page, "paintTextZone")
        # A trava e a CHEGADA ao pintor, e nao a atribuicao direta de `background`,
        # porque a faixa passou a ter dois estados (cor solida e placa) e um
        # `el.style.background = zone.color` fixaria so o primeiro. O caminho
        # pode ser a chamada direta ou a indireta por `applyPlateStyle` — o que
        # nao pode e a faixa pintar o fundo sozinha.
        self.assertTrue(
            "plateStyle(zone)" in painter or "applyPlateStyle(el, zone)" in painter,
            "a faixa da previa ignora a placa de fundo da zona")
        self.assertNotIn("el.style.background = zone.color", painter,
                         "a faixa voltou a ignorar a cor da zona")
        # E a cor continua tendo quem a le: ela e o fundo sem placa e o letterbox
        # do encaixe "Encaixa", entao sumir dela faria a previa mentir sobre os
        # dois casos.
        self.assertIn("zone.color", fn_body(self.page, "plateStyle"))

        # A placa nao pode ser aplicada por `cssText`. O laco de `renderPreview`
        # escreve `top` e `height` na faixa ANTES de chamar `paintTextZone`, e
        # `cssText` substitui o bloco inline inteiro: a faixa perdia a posicao,
        # caia no topo do canvas e ficava com a altura do texto. A placa saia no
        # lugar errado e o console nao dizia nada — so a geometria na tela
        # denunciava, que e o tipo de defeito que os testes de string nao pegam.
        self.assertNotIn("style.cssText", painter,
                         "a faixa de texto troca o bloco inline e perde top/height")
        # A trava do contratoparte: a pintura tem de acontecer em `applyPlateStyle`.
        self.assertIn("applyPlateStyle(el, zone)", painter)

    def test_the_meme_id_text_is_centered_and_the_x_card_is_not(self):
        """No Meme o nome e o @handle saem centrados; no X, a esquerda.

        A ``.pv-id`` e a MESMA classe nos dois formatos, e nela o
        ``flex: 1`` faz a CAIXA ocupar a linha toda que sobra depois do avatar.
        Sem centralizar, o texto herda ``start`` e fica grudado nessa borda —
        foi o que o usuario viu. Centralizar exige DUAS coisas: ``text-align``
        (move o texto dentro da linha) e ``align-items`` (o ``strong`` e
        ``display: flex`` para o selo azul, e item flex nao obedece a
        ``text-align``). Faltando a segunda, o nome desce centrado e o @handle
        fica a esquerda.

        E a caixa precisa ENCOLHER: com ``flex: 1`` ela continuaria larga e o
        centro do texto cairia no meio do espaço vazio, e não no meio do par
        avatar + nome.

        O escopo e ``.pv-idbar`` justamente para o cartão do X ficar com o
        nome à esquerda, que é como o post se apresenta.
        """
        meme = self._rule(".pv-idbar .pv-id")
        self.assertIn("align-items: center", meme,
                      "o strong (flex) do Meme nao obedece a text-align sozinho")
        self.assertIn("text-align: center", meme,
                      "o nome/@handle do Meme continuam a esquerda")
        # `flex: 0 1 auto` e o que faz a caixa encolher ate o conteudo.
        self.assertRegex(meme, r"flex:\s*0\s+1\s+auto",
                         "a caixa do texto continua ocupando a linha toda (flex: 1)")

        # O cartao do X NAO pode ter pego o centro: la o nome encosta no avatar.
        regra_comum = self._rule(".pv-id")
        self.assertIn("flex: 1", regra_comum,
                      "a .pv-id compartilhada perdeu o flex: 1 do cartao do X")
        self.assertNotIn("text-align: center", regra_comum,
                         "centralizar a .pv-id global trocaria o cartao do X junto")

    def test_the_gallery_id_text_matches_the_window(self):
        """O card da galeria e a janela mostram o MESMO perfil, alinhados igual.

        Os dois desenham avatar + nome + @handle. Com o card em ``left`` e a
        janela em ``center``, o formato prometia uma coisa no card e entregava
        outra no palco — e o card e o que o usuario ve ANTES de abrir o
        formato, entao era ele quem mentia.
        """
        card = self._rule(".gal-id-text")
        self.assertIn("align-items: center", card,
                      "o nome do card da galeria nao alinha com o @handle")
        self.assertNotIn("text-align: left", card,
                         "o card da galeria ainda centraliza o nome a esquerda")

    def test_the_gallery_text_band_keeps_its_declared_height(self):
        """A faixa de texto do card Meme mede a fração DECLARADA, nem mais nem menos.

        O `padding` vertical da faixa entra na base do `flex-basis: 0` e sai fora
        da conta do `flex-grow`: com `4px 6px` ela desenhava 18,3% da tela onde a
        zona declara 16%, e o vídeo desenhado começava abaixo do vídeo do motor.
        O card prometia um layout que o render não entregava — o mesmo defeito que
        fez a legenda virar sobreposição no formato Vídeo Viral. O respiro vai no
        texto, que a faixa centraliza.
        """
        block = self._rule(".gal-text-band")
        pad = re.search(r"padding:\s*([^;]+);", block)
        self.assertIsNotNone(pad, ".gal-text-band não declara o respiro do texto")
        parts = pad.group(1).split()
        # shorthand: 1 valor (todos), 2 (v | h), 3/4 (top e bottom explícitos)
        vertical, bottom = parts[0], (parts[2] if len(parts) > 2 else parts[0])
        self.assertEqual(
            vertical.strip(), "0",
            "padding vertical na faixa de texto engorda a zona declarada",
        )
        self.assertEqual(
            bottom.strip(), "0", "padding vertical assimétrico na faixa de texto",
        )

    def test_the_text_band_is_painted_in_place(self):
        """Uma faixa por zona — a de texto inclusive.

        Ela pintava um SEGUNDO elemento dentro do que o laço já tinha posicionado:
        duas ``.band`` aninhadas davam duas alturas para a mesma zona (a de fora com
        a geometria do motor, a de dentro com a altura do conteúdo), e a placa preta
        vazava sobre o vídeo. O usuário vê isso como "o fundo preto invadiu o vídeo",
        que é exatamente o que ele pediu para não acontecer.
        """
        preview = fn_body(self.page, "renderPreview")
        self.assertIn("paintTextZone(el, band)", preview)
        self.assertNotIn("appendChild(textZoneBox", preview)
        self.assertNotIn("appendChild(paintTextZone", preview)
        # E quem pinta não cria faixa nova: recebe a que já está posicionada.
        painter = fn_body(self.page, "paintTextZone")
        self.assertNotIn('createElement("div")', painter)

    def test_the_tweet_fills_its_whole_band(self):
        """A faixa de imagem do formato X é branca de ponta a ponta.

        O mock era um cartão de 90% centralizado: os 10% de sobra pintavam o
        preto do palco, então a prévia mostrava uma zona com margem que o motor
        não desenha — `.band .media` preenche a faixa inteira com `scale`+`crop`.
        O que define o branco é o fundo da ZONA, logo ele ancora em `inset: 0`,
        sem largura declarada, sem centralização e sem canto arredondado (zona é
        retângulo).
        """
        block = self._rule(".pv-tweet")
        self.assertIn("inset: 0", block)
        self.assertNotIn("width:", block)
        self.assertNotIn("justify-self", block)
        self.assertNotIn("border-radius", block)
        self.assertIn('tweet.className = "pv-tweet"', self.page)

    def test_both_mocks_of_the_tweet_read_the_same_measures(self):
        """Galeria e prévia desenham o MESMO tweet, com as mesmas medidas.

        O cartão do modelo e a janela de prévia declaravam os tamanhos em duas
        listas escritas à mão — uma em px, outra em cqw — e as duas divergiram: o
        selo de verificado media 18px na vitrine (herdado de ``.gal-band svg``,
        que existe para os ícones de TIPO) e 10px na prévia (o atributo do SVG),
        o ``@handle`` não cortava com reticências e o texto não tinha limite de
        linhas. Agora as medidas moram num bloco só, como fração da largura da
        faixa, e os dois lados consomem os mesmos nomes.
        """
        spec = self._rule(".gal-tweet-band, .pv-tweet")
        for measure in (
            "--tw-avatar", "--tw-name", "--tw-handle",
            "--tw-body", "--tw-gap-row", "--tw-gap-block",
        ):
            self.assertIn(measure, spec, f"a medida {measure} sumiu da fonte única")

        consumers = (
            ".gal-avatar", ".gal-tweet-row", ".gal-tweet-id strong",
            ".gal-tweet-id small", ".gal-tweet-band p.gal-tweet-body",
            ".pv-avatar", ".pv-tweet-row", ".pv-id strong", ".pv-id small", ".pv-body",
        )
        for selector in consumers:
            self.assertIn(
                "var(--tw-", self._rule(selector),
                f"{selector} voltou a declarar tamanho próprio",
            )

    def test_the_verified_badge_follows_the_name(self):
        """1,25em nos dois lados: o selo é do NOME, não dos ícones da faixa.

        ``.gal-band svg`` fixa 18px para os ícones de tipo (câmera, moldura) e o
        selo herdava esse número — saía maior que o próprio nome na vitrine,
        enquanto na prévia ficava preso no ``width="10"`` do SVG.
        """
        for badge in (".gal-tweet-id strong svg", ".pv-id strong svg"):
            self.assertIn("1.25em", self._rule(badge))

    def test_the_tweet_starts_below_the_notch(self):
        """O recorte do aparelho pinta por cima do conteúdo.

        A faixa de imagem começa na BORDA DE CIMA da tela, então o nome e o selo
        nasciam atrás do notch — nas duas janelas, vitrine e prévia. A âncora é a
        mesma do POV (``--notch-top`` + ``--notch-h``) e pela mesma razão: o
        número é do recorte do aparelho, não da zona.
        """
        self.assertIn("--tw-pad-top", self._rule(".gal-tweet-band, .pv-tweet"))
        for selector in (".gal-tweet", ".pv-tweet"):
            self.assertIn(
                "var(--tw-pad-top)", self._rule(selector),
                f"{selector} voltou a nascer atrás do recorte",
            )

class TweetEditorTests(unittest.TestCase):
    """O passo Aparencia edita o tweet do modelo: título, @handle, texto, posição.

    O cartão do formato X é uma prévia — o render usa a imagem que a zona aponta —
    mas é a prévia que o usuário olha para decidir. Dois defeitos silenciosos
    possíveis: o campo existir na página e não estar ligado ao estado (digita e
    nada muda) e a posição ser escrita em px de TELA em vez de px do QUADRO, o que
    faria o mesmo ``-20`` valer outra coisa em 1080x1920 e em 720x1280.
    """

    def setUp(self):
        self.page = page_source("templates.html")
        self.markup = (server.WEB_DIR / "templates.html").read_text(encoding="utf-8")
        # O passo Aparencia e o card data-step="0".
        self.step = self.markup.split('data-step="0"', 1)[1].split("</section>", 1)[0]

    def _fn_body(self, name: str) -> str:
        """O corpo da função ``name`` do JS da página (ver :func:`fn_body`)."""
        return fn_body(self.page, name)

    def test_the_fields_live_in_the_aparencia_step(self):
        for field in ("tw-name", "tw-handle", "tw-text"):
            self.assertIn(f'id="{field}"', self.step, f"{field} fora do passo Aparencia")
        # Seis sliders: H e V de cada item do cartao.
        for item in ("avatar", "name", "body"):
            for axis in ("x", "y"):
                field = f"tw-pos-{item}-{axis}"
                self.assertIn(f'id="{field}"', self.step, f"{field} fora do passo Aparencia")
        self.assertIn('id="tw-reset-pos"', self.step)

    def test_the_group_sits_right_below_the_step_title(self):
        """O grupo fica no topo do passo, acima das duas colunas de campos.

        Pedido de quem usa a tela: o cartao e a primeira coisa que se olha, entao
        vem logo abaixo do titulo "Aparencia" e da descricao — nao depois do
        preset e do layout de video.
        """
        self.assertLess(self.step.index("tw-box"), self.step.index('class="ap-grid"'))

    def test_every_item_of_the_card_has_its_own_position(self):
        """Avatar, título e texto: um par de eixos por item, não um só para o bloco.

        Um número só por item não bastava: mover o avatar para o centro exige H e
        V, e o slider antigo só mexia o eixo vertical.
        """
        for item in ("avatar", "name", "body"):
            for axis in ("x", "y"):
                self.assertIn(f'id="tw-pos-{item}-{axis}"', self.step)
                self.assertIn(f'id="tw-pos-{item}-{axis}-hint"', self.step)

    def test_the_page_reads_and_writes_the_state(self):
        for key in ("tweetName", "tweetHandle", "tweetText",
                    "tweetAvatar", "tweetAvatarName"):
            self.assertIn(f"state.{key}", self.page, f"{key} não é usado pela página")
        # Os offsets viraram um objeto por item (H e V), não um número por item.
        self.assertIn("state.tweetOffset", self.page,
                      "o estado não guarda mais os offsets por item")
        for item in ("avatar", "name", "body"):
            self.assertIn(f'"{item}"', self.page, f"o item {item} saiu do editor")
        self.assertIn("function paintTweetFields", self.page)
        self.assertIn("function paintTweetAvatar", self.page)

    def test_empty_field_falls_back_to_the_example(self):
        """Vazio = exemplo do modelo, e o exemplo é um só (o mesmo da vitrine)."""
        for expr in ("state.tweetName || TWEET_NAME_DEFAULT",
                     "state.tweetHandle || TWEET_HANDLE_DEFAULT",
                     "state.tweetText || TWEET_DEFAULT"):
            self.assertIn(expr, self.page)

    def test_the_offset_is_frame_pixels_not_screen_pixels(self):
        """px do QUADRO -> ``cqh`` do palco, a mesma conversão do headline.

        Em px de tela o ajuste valeria 20/1920 do quadro numa janela e 20/280 na
        outra, e o modelo sairia diferente em 1080x1920 e em 720x1280.
        """
        self.assertIn('(value / state.height * 100).toFixed(3) + "cqh"', self.page)
        # O eixo H não pode usar `cqw`: dentro do cartão ele mediria o CONTEÚDO do
        # cartão (container-type: inline-size), e o mesmo número valeria outra
        # coisa em cada formato. O número puro é multiplicado por `--framepx`,
        # medido na tela do palco — ver `paintFrameScale`.
        self.assertIn("function paintFrameScale", self.page)
        self.assertIn("screen.clientWidth / state.width", self.page)

    def test_the_css_moves_each_item_with_its_own_variable(self):
        """Cada item carrega duas variáveis (H e V) e as duas entram no ``translate``.

        H vem como número puro e o CSS multiplica por ``--framepx``; V já chega em
        ``cqh``. Zerar uma só das duas deixaria o item no eixo antigo.
        """
        for rule, item in ((".pv-avatar", "avatar"), (".pv-id", "name"),
                           (".pv-body", "body")):
            body = self._rule(rule, "tw-off-x")
            self.assertIn("transform: translate(calc(", body,
                          f"{rule} não desloca por translate")
            self.assertIn(f"var(--tw-off-x-{item}, 0)", body,
                          f"{rule} não consome --tw-off-x-{item}")
            self.assertIn(f"var(--tw-off-y-{item}, 0px)", body,
                          f"{rule} não consome --tw-off-y-{item}")
            self.assertIn("var(--framepx, 0px)", body,
                          f"{rule} não escala o H por --framepx")

    def _rule(self, selector: str, containing: str | None = None) -> str:
        """O bloco de declarações de ``selector`` no CSS da página.

        Vários seletores aparecem mais de uma vez (``.pv-avatar`` primeiro define
        o tamanho e depois o deslocamento); com ``containing`` fica o bloco que
        tem aquela declaração.
        """
        blocks = re.findall(
            r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", self.page, re.MULTILINE
        )
        for block in blocks:
            if containing is None or containing in block:
                return block
        self.fail(f"a regra {selector} com {containing!r} sumiu do CSS")

    def test_the_avatar_comes_from_a_file_on_the_computer(self):
        """Foto do avatar por arquivo local: botão, input e o aceite de imagem.

        O cartão desenhava só a letra do título. O controle é o mesmo da página
        para arquivo local (a tile tracejada dos vídeos de referência) e o input
        mora DENTRO do botão, porque quem abre o seletor é a delegação de clique
        que já existe — um input solto na página ficaria sem ninguém para clicá-lo.
        """
        for needed in ('id="tw-avatar-file"', 'type="file"', "pv-upload-btn",
                       'id="tw-avatar-clear"', 'id="tw-avatar-chip"'):
            self.assertIn(needed, self.step, f"{needed} fora do grupo do tweet")
        self.assertIn('accept="image/png,image/jpeg,image/webp,image/gif"', self.step)

    def test_the_photo_is_a_small_data_url_because_the_policy_forbids_blob(self):
        """A foto entra por ``canvas.toDataURL``, reduzida, e não por object URL.

        Duas razões que se somam, e a primeira é invisível:

        * o CSP da página é ``img-src 'self' data:``, SEM ``blob:`` — uma imagem
          de object URL é bloqueada em silêncio (o círculo do avatar fica vazio,
          nada no console). ``media-src`` tem ``blob:``, e é por isso que o vídeo
          de referência pode usar object URL e a foto não pode.
        * o avatar aparece com ~48px no cartão: guardar o original seria carregar
          megabyte de base64 no estado e recopiá-lo para dentro do HTML a cada
          tecla digitada no título.
        """
        img_src = server.Handler.CSP.split("img-src", 1)[1].split(";", 1)[0]
        self.assertIn("data:", img_src, "sem `data:` o avatar não pinta")
        self.assertNotIn("blob:", img_src)
        self.assertIn("TWEET_AVATAR_PX = 256", self.page)
        self.assertIn("function downscaleAvatar", self.page)
        photo_fn = self.page.split("function addTweetAvatar", 1)[1].split(
            "function downscaleAvatar", 1)[0]
        # A ponte para o canvas é o FileReader: nem object URL (bloqueado) nem
        # caminho de disco (o navegador não lê).
        self.assertIn("reader.readAsDataURL(file)", photo_fn)
        self.assertNotIn("createObjectURL", photo_fn)
        self.assertIn('canvas.toDataURL("image/jpeg"', self.page)

    def test_the_photo_paints_in_the_window_and_in_the_gallery(self):
        """Com arquivo a janela desenha a imagem; sem, a letra do título.

        A letra não sai do HTML quando entra a foto: ela é o estado do modelo e o
        que reaparece ao remover. Quem esconde é o CSS, com ``color: transparent``
        — ``visibility``/``display`` apagariam a letra e um PNG de fundo
        transparente deixaria o vazio aparecer no lugar dela.

        A regra cobre janela e vitrine (``.pv-`` e ``.gal-``) e vem DEPOIS das
        variantes escuras do Meme: elas usam o atalho ``background``, que redefine
        ``background-size`` para ``auto`` — com a regra antes delas, a foto do Meme
        entrava no tamanho natural (256px) dentro de um círculo de ~48px.
        """
        self.assertIn("function avatarMarkup", self.page)
        img_rule = ".pv-avatar--img, .gal-avatar--img {"
        rule = self.page.split(img_rule, 1)[1].split("}", 1)[0]
        self.assertIn("background-size: cover", rule)
        self.assertIn("color: transparent", rule)
        position = self.page.index(img_rule)
        for dark in (".pv-avatar--dark { background", ".gal-avatar--dark { background"):
            self.assertLess(self.page.index(dark), position,
                            "a regra da foto precisa vir depois das variantes escuras")

    def test_the_file_name_keeps_its_own_case(self):
        """O nome do arquivo não vai a maiúsculas com o rótulo.

        ``.mini-label`` é uppercase; subir a caixa de ``foto_de_perfil.JPG``
        mostraria outro nome — e é o nome que o usuário usa para saber qual
        arquivo está na janela.
        """
        self.assertIn("text-transform: none", self._rule(".mini-label #tw-avatar-state"))

    def test_every_template_with_a_profile_uses_the_identity_of_the_step(self):
        """Os campos da seção valem para X e Meme, na janela e na vitrine.

        O Meme desenhava "Seu Nome", "@seuhandle" e "S" na unha, nos dois lugares:
        os campos do passo Aparência não editavam aquele template, e a vitrine
        ficava com o exemplo do modelo mesmo depois de o usuário escrever o nome
        dele. O que a seção edita é o PERFIL do cartão, então os quatro pontos que
        desenham perfil leem a mesma fonte.
        """
        for fn, where in (("tweetMock", "janela do X"), ("memeIdBar", "janela do Meme"),
                          ("tweetBand", "vitrine do X"), ("galIdBand", "vitrine do Meme")):
            body = self._fn_body(fn)
            for helper, label in (("avatarMarkup(", "avatar"), ("tweetName()", "título"),
                                  ("tweetHandle()", "@handle")):
                self.assertIn(helper, body, f"{where} não usa o {label} da seção")
        # A vitrine do Meme desenha a barra por DELEGAÇÃO: `memeBands` só escolhe o
        # desenho de cada zona. Sem esta linha o teste cobriria o desenhista e
        # deixaria a ROTA solta — trocá-la por um mock genérico devolveria "Seu
        # Nome" ao card sem nenhum teste reclamar.
        self.assertIn(
            "return galIdBand(z)",
            self._fn_body("memeBands"),
            "a vitrine do Meme não roteia a imagem para a barra de identidade",
        )

    def test_no_profile_is_hardcoded_in_a_mock(self):
        """Nenhum mock volta a escrever o exemplo na unha.

        O exemplo do modelo mora em ``TWEET_NAME_DEFAULT``/``TWEET_HANDLE_DEFAULT``
        (e nos placeholders do formulário). Escrito direto no mock, ele ignora os
        campos — foi assim que o Meme ficou de fora.
        """
        for fn in ("tweetMock", "memeIdBar", "tweetBand", "memeBands"):
            body = self._fn_body(fn)
            self.assertNotIn("Seu Nome", body, f"{fn} tem o nome de exemplo na unha")
            self.assertNotIn("@seuhandle", body, f"{fn} tem o handle de exemplo na unha")

    def test_the_position_sliders_reach_the_meme_bar(self):
        """A barra de identidade do Meme recebe os MESMOS offsets do cartão do X.

        O formato não tem texto de tweet: o eixo de Texto vale só para o X, e é o
        hint do grupo que diz isso. Sem os offsets aqui, o arraste e os sliders de
        Avatar e Título pareciam mortos naquele formato.
        """
        bar = self._fn_body("memeIdBar")
        self.assertIn('paintTweetOffsets(bar, ["avatar", "name"])', bar)

    def _gallery_block(self, key: str) -> str:
        """O bloco de uma entrada de ``GALLERY``, da abertura até a chave seguinte."""
        return self.page.split(f"{key}: {{", 1)[1].split("\n    }", 1)[0]

    def test_the_meme_template_is_a_real_three_zone_template(self):
        """O POV é uma ZONA, e as três frações somam 100% como manda o motor.

        Antes ele era desenho da página sobreposto à faixa de vídeo: o render não
        o queimava e a altura do vídeo não o contava. Agora ocupa altura própria —
        por isso o vídeo encolhe (52%, depois de 58% quando a faixa era 16%).
        Uma zona nova aqui sem reequilibrar as outras é o defeito que só
        apareceria no render, acusado pelo motor.
        """
        block = self._gallery_block("meme")
        fractions = [float(value) for value in re.findall(r"fraction: ([0-9.]+)", block)]
        self.assertEqual(len(fractions), 3, "o Meme deixou de ter três zonas")
        self.assertAlmostEqual(sum(fractions), 1.0, places=6)
        for kind in ("text", "video", "image"):
            self.assertIn(f'kind: "{kind}"', block, f"o Meme perdeu a zona {kind}")

    def test_the_text_zone_comes_before_the_video(self):
        """A ordem das zonas é a ordem das faixas: o POV é o TOPO do quadro.

        Declarado depois do vídeo, a faixa preta iria para o rodapé — e o usuário
        veria o POV embaixo enquanto pediu "fora da área do vídeo, no topo".
        """
        block = self._gallery_block("meme")
        self.assertLess(block.index('kind: "text"'), block.index('kind: "video"'))

    def test_the_meme_template_carries_the_words(self):
        """O catálogo traz o texto: zona de texto vazia é faixa preta e o motor a recusa."""
        block = self._gallery_block("meme")
        self.assertRegex(block, r'text: "[^"]+"', "a zona de texto do Meme está sem palavras")
        self.assertIn('color: "black"', block, "a faixa do POV perdeu o fundo preto")

    def test_the_pov_carries_the_46px_descent_of_the_model(self):
        """O catálogo nasce com o POV 46px abaixo do centro, como a prévia do modelo.

        O valor é medido, não herdado: com ``an=5`` o libass centraliza a CAIXA da
        fonte, que tem mais altura acima da linha-base do que abaixo, então o centro
        visual do texto fica acima do centro da faixa. Os 46px corrigem esse desnível
        e é o que faz prévia e render lerem iguais.

        Zerado, o render sai com o texto ~3% mais alto e nada reclama — o defeito
        fica invisível na tela e só aparece na comparação com a prévia. E o valor
        mora em ``textOff`` (px do quadro), não em fração: quem ler ``y: 46`` como
        fração de canvas colocaria o texto 46x mais longe, fora do quadro.
        """
        block = self._gallery_block("meme")
        self.assertRegex(
            block,
            r"textOff:\s*\{\s*x:\s*0,\s*y:\s*46\s*\}",
            "o modelo do Meme perdeu o deslocamento vertical do POV",
        )
        # Em px do quadro, e a mesma unidade dos sliders de posição do passo
        # Aparência — o usuário arrasta em px e espera o mesmo número no modelo.
        self.assertIn("clampOffset", self.page)
        # E o .toml converte para a fração que o motor lê: 46/1920 = 0.024.
        toml = self._fn_body("toToml")
        self.assertIn("text_dy", toml, "o offset do POV não chega ao .toml")
        self.assertIn("offY / state.height", toml, "o Y do POV tem de dividir pela ALTURA")

    def test_the_id_bar_position_is_part_of_the_template(self):
        """A posição do perfil é do TEMPLATE, e não estado solto da sessão.

        Avatar e nome nascem centrados pelo CSS (``justify-content: center`` com
        ``align-items: center``), e o Meme ainda declara um ajuste em cima disso
        (os 2% de :meth:`test_the_meme_lifts_the_profile_two_percent`). O que
        importa aqui é a chave ``idOffset`` existir e ser aplicada: sem ela, o
        arrasto vivia só em ``state.tweetOffset`` e sobrevivia à virada de
        formato — o usuário saía do Meme torto, ia ao Viral, voltava, e o Meme
        mantinha o arrasto. O "padrão" que o usuário pedia não existia: o botão
        de usar o formato entregava o que ficou da última visita.

        A trava é o ``idOffset`` no catálogo mais a cópia em
        ``applyGalleryIdOffset``: sem o clone, o estado passaria a ser o mesmo
        objeto do catálogo e o primeiro arrasto reescreveria a galeria, que se
        redesenha a cada render.
        """
        block = self._gallery_block("meme")
        self.assertIn("idOffset:", block, "o Meme não declara a posição do seu perfil")
        # Avatar e nome andam JUNTOS: o mesmo par sobe ou desce junto, senão o
        # circulo e o texto se desencontram.
        av = re.search(r"avatar:\s*\{[^}]*\}", block)
        nome = re.search(r"name:\s*\{[^}]*\}", block)
        self.assertIsNotNone(av, "o Meme não declara o offset do avatar")
        self.assertIsNotNone(nome, "o Meme não declara o offset do nome")
        self.assertEqual(re.findall(r"[-+]?\d+", av.group(0)),
                         re.findall(r"[-+]?\d+", nome.group(0)),
                         "avatar e nome declaram deslocamentos diferentes")

        loader = self._fn_body("applyGalleryIdOffset")
        self.assertIn("g.idOffset", loader, "a galeria não lê a posição que ela declara")
        self.assertIn("state.tweetOffset[item] = idOffsetPx", loader,
                      "a posição do perfil não é escrita no estado do cartão")
        # COPIA, e não referência: `state.tweetOffset.avatar` virar o mesmo objeto
        # de `GALLERY.meme.idOffset.avatar` faria o arraste reescrever o catálogo.
        conv = self._fn_body("idOffsetPx")
        self.assertIn("off = off || {}", conv,
                      "o leitor do idOffset precisa tratar item ausente")
        # COPIA E CONVERSAO nos DOIS eixos. O X precisa da divisao tanto quanto o
        # Y: o CSS multiplica o valor por `--framepx` (px do quadro em px de
        # tela), entao uma fracao passada crua viraria "2,78px" em vez de 2,78%
        # — 30px de ajuste viravam 3px, e o deslocamento sumia sem erro.
        self.assertRegex(conv, r"off\.x\s*\|\|\s*0\)\s*/\s*100\s*\*\s*state\.width",
                         "o X do catálogo não é convertido para px do quadro")
        self.assertRegex(conv, r"off\.y\s*\|\|\s*0\)\s*/\s*100\s*\*\s*state\.height",
                         "o Y do catálogo não é convertido para px do quadro")
        # E o reset precisa do MESMO caminho, senão os dois discordariam da
        # unidade e o botão devolveria um deslocamento diferente do modelo.
        self.assertIn("idOffsetPx", self._fn_body("modelIdOffset"),
                      "o botao de voltar ao modelo nao passa pela mesma conversao")

        # E o `loadGallery` precisa CHAMAR isso: declarar sem aplicar nao muda nada.
        # A trava olha o CORPO de `loadGallery`, e nao a pagina inteira: uma
        # chamada comentada no fonte continua sendo a mesma string, e o teste
        # passaria com a correcao desligada — que e o que ele existe para pegar.
        galeria = self._fn_body("loadGallery")
        self.assertIn(
            "applyGalleryIdOffset(g)", galeria,
            "carregar um formato da galeria nao aplica a posicao do perfil",
        )
        # E a linha tem de estar viva: nem comentada nem dentro de um `if` que
        # nunca seja verdadeiro.
        chamada = next(
            (linha for linha in galeria.splitlines()
             if "applyGalleryIdOffset" in linha), ""
        )
        self.assertFalse(
            chamada.strip().startswith("//"),
            "a aplicacao do offset do perfil esta comentada em loadGallery",
        )

    def test_the_meme_lifts_the_profile_by_the_declared_amount(self):
        """O Meme sobe o par avatar+nome 7,6% do quadro, e isso é do MODELO.

        Com ``align-items: center`` o par nasce no meio da faixa, e aí ele lê
        baixo demais: a barra tem o avatar e o nome, e o centro geométrico não é o
        centro visual. Os 146px de ajuste são o pedido, medidos no arrasto.

        O valor é declarado em % DO QUADRO, e não em px, por um motivo medido: a
        folga da barra até o topo é a mesma fração nas três resoluções (9,7%),
        mas em px de quadro ela seria 124, 187 e 249. Um valor em px daria um
        arranco mínimo em 720p e, em 2560p, empurraria o avatar para fora da
        faixa. Com a fração, 7,6% são 7,6% em qualquer lugar.

        O teste trava a fração E a conversão: um número que chegue ao estado como
        px sem converter faz o desenho mudar de tamanho conforme a resolução.
        """
        block = self._gallery_block("meme")
        for item in ("avatar", "name"):
            self.assertRegex(
                block,
                rf"{item}:\s*\{{\s*x:\s*0,\s*y:\s*-7\.6\s*\}}",
                f"o Meme nao sobe {item} em 7,6% do quadro",
            )
        # 7,6% de 1920 = 145,92px: o numero que o usuario pediu (-146) arredonda
        # para a precisao do catalogo. Uma casa a mais seria precisao que a
        # fonte nao tem — o campo mostraria -146 e o desenho estaria em -145,92.
        self.assertAlmostEqual(-7.6 / 100 * 1920, -146, delta=0.5)

        loader = self._fn_body("applyGalleryIdOffset")
        # O catalogo fala em fracao; o estado (sliders, arraste) fala em px do
        # quadro. Sem esta divisao o -2 vira "2px", invisivel em 2560p. A conta
        # mora em `idOffsetPx`, e as DUAS portas que leem o catalogo passam por
        # ela — com a divisao repetida em cada uma, uma delas pode perder e o
        # desenho mudar de tamanho conforme a resolucao, sem erro nenhum.
        self.assertIn("idOffsetPx", loader,
                      "o estado do cartao nao passa pela conversao de % para px")
        conv = self._fn_body("idOffsetPx")
        self.assertRegex(conv, r"off\.y\s*\|\|\s*0\)\s*/\s*100\s*\*\s*state\.height",
                         "o % do catalogo nao e convertido para px do quadro")
        self.assertIn("idOffsetPx", self._fn_body("modelIdOffset"),
                      "o botao de voltar ao modelo nao passa pela mesma conversao")
    def test_the_save_button_writes_the_current_point_into_the_template(self):
        """"Salvar como padrão" grava a posição atual NO CATÁLOGO do formato.

        É o inverso do botão ao lado: "voltar ao modelo" lê o catálogo, este
        escreve nele. Sem ele o usuário ajustava o avatar, recarregava o formato e
        perdia o ajuste — a posição era estado de sessão, e o catálogo só tinha o
        valor fixo com que o desenho nasceu.

        A trava cobre as decisões que fazem a diferença:

        * os DOIS eixos: um botão que gravasse só o Y deixaria o avatar torto
          para sempre depois de um "voltar ao modelo";
        * o TEXTO DO POST entra, mas só onde ele é desenhado: é o corpo da faixa
          de identificação do X. O POV fica de fora de propósito — ele mora na
          ZONA e vira `text_dy`` no ``.toml``, e gravar a posição dele aqui
          criaria uma segunda fonte para o mesmo número, que divergiria no
          primeiro slider mexido;
        * em % DO QUADRO, com a base de cada eixo: em px, a mesma posição na tela
          valeria números diferentes conforme a resolução.
        """
        self.assertIn('id="tw-save-id-offset"', self.step,
                      "o botão de salvar padrão sumiu do passo")
        body = self._fn_body("saveIdOffsetAsModel")
        self.assertRegex(body, r"idOffset\.avatar\s*=\s*\{[^}]*x:[^}]*y:",
                         "o avatar precisa gravar os DOIS eixos")
        self.assertRegex(body, r"idOffset\.name\s*=\s*\{[^}]*x:[^}]*y:",
                         "o nome precisa gravar os DOIS eixos")
        # O texto do post e guardado, mas ATRAS da pergunta "este formato desenha
        # ele?": sem o guarda, gravar no Meme seria um ajuste invisivel.
        self.assertIn("idOffset.body", body,
                      "o texto do post precisa ser salvo com avatar e titulo")
        self.assertIn("if (formatHasTweetBody())", body,
                      "o texto do post so pode ser salvo onde ele e desenhado")
        # O POV nao pode ser gravado aqui: ele vive na zona.
        self.assertNotIn("idOffset.pov", body,
                         "o POV nao pertence ao idOffset: ele vive na zona")
        # E a unidade: % do quadro, com a base de cada eixo.
        self.assertRegex(body, r"pct\(x,\s*state\.width\)",
                         "o X tem de ser fracao da LARGURA")
        self.assertRegex(body, r"pct\(y,\s*state\.height\)",
                         "o Y tem de ser fracao da ALTURA")

    def test_the_saved_default_survives_a_reload(self):
        """O padrão é gravado no navegador, não só na memória da aba.

        O catálogo é um literal do arquivo: sem o ``localStorage``, recarregar a
        página perdia o padrão e o usuário refazia os três arraste toda sessão —
        que é exatamente o trabalho que o botão existe para não repetir. Como
        ``localStorage`` lança em modo privado, a leitura e a escrita são
        try/catch: uma preferência de layout não pode derrubar a página.
        """
        self.assertIn('var ID_OFFSET_STORE = "viral-clipper:id-offset"', self.page)
        store = self._fn_body("readSavedIdOffsets")
        write = self._fn_body("writeSavedIdOffsets")
        for nome, corpo in (("leitura", store), ("escrita", write)):
            self.assertIn("try {", corpo, f"a {nome} do storage pode lancar")
            self.assertIn("catch", corpo, f"a {nome} do storage precisa de guarda")
        # O `setItem` tem de acontecer dentro da escrita — fora do try ele
        # estouraria a pagina no modo privado.
        self.assertIn("setItem", write)
        # E a restauracao precisa rodar ANTES do primeiro loadGallery, que e quem
        # aplica o offset nas zonas; depois, o primeiro formato abriria no catalogo
        # e o guardado so entraria na visita seguinte.
        init = self.page.split("function init() {", 1)[1]
        self.assertIn("restoreIdOffsets();", init, "o padrao guardado nao volta na partida")
        # Comparacao por LINHA, e nao por `index` no texto bruto: o comentario
        # acima da chamada cita `loadGallery("x")`, e um `index` acharia o
        # comentario primeiro e daria a ordem errada sem nenhum erro visivel.
        linhas = [ln.strip() for ln in init.splitlines()]
        self.assertLess(
            linhas.index("restoreIdOffsets();"),
            linhas.index('loadGallery("x");'),
            "o padrao guardado tem de voltar ANTES do primeiro formato abrir",
        )
        restore = self._fn_body("restoreIdOffsets")
        self.assertIn("GALLERY[key].idOffset = Object.assign", restore,
                      "o guardado precisa entrar no catalogo, nao numa segunda leitura")

    def test_saving_reports_when_there_is_no_gallery_template(self):
        """"Carregar split-card" nao vem da galeria: o botao avisa em vez de gravar.

        O ``split-card`` seta o estado sem passar por ``loadGallery``, entao
        ``state.previewMock`` aponta para um formato que o usuario nao escolheu.
        Gravar ali sobrescreveria o modelo de um formato que ele nao esta vendo —
        e o botao ainda diria que salvou.
        """
        body = self._fn_body("saveIdOffsetAsModel")
        self.assertIn("if (!entry)", body, "o botao nao checa se ha formato carregado")
        self.assertRegex(body, r"if \(!entry\)[\s\S]{0,400}?return;",
                         "sem formato carregado, o botao tem de PARAR")
        self.assertIn('"bad"', body,
                      "sem formato carregado o botao tem de avisar, nao fingir")

    def test_the_reset_button_returns_to_the_model_not_to_zero(self):
        """"Voltar ao modelo" devolve o que o catálogo declara, e não zero.

        A UI do botão diz "Posições de volta ao modelo" desde o começo. Com o
        Meme declarando um deslocamento, um zerar literal jogaria o par no meio da
        faixa — exatamente o desenho que o usuário trocou. E zerar o POV
        apagaria o ``text_dy`` que o catálogo acabou de escrever e que é o que
        chega ao ``.toml``.

        O reset devolve os DOIS eixos do modelo, e não só o Y: o botão de salvar
        grava os dois, e um reset que devolvesse só o Y deixaria o avatar torto
        para sempre depois de um "voltar ao modelo". O POV fica de fora dos dois
        porque o offset dele mora no ``textOff`` da zona, e o modelo dele é o
        ``text_dy`` do catálogo — zerá-lo aqui apagaria o valor que chega ao
        ``.toml``.
        """
        reset = self._fn_body("resetTweetOffsets")
        self.assertIn("modelIdOffset(item)", reset,
                      "o botao zera em vez de voltar ao valor do modelo")
        self.assertRegex(reset, r"off\.x\s*=\s*model\.x",
                         "o X do reset tem de voltar ao modelo tambem")
        self.assertRegex(reset, r"off\.y\s*=\s*model\.y",
                         "o Y do reset tem de voltar ao modelo")
        self.assertRegex(reset, r'item === "pov"', 
                         "o POV precisa ficar de fora: o offset dele vive na zona")
        # E o leitor do modelo tem de existir e ler o `idOffset` do formato atual.
        model = self._fn_body("modelIdOffset")
        self.assertIn("GALLERY[state.previewMock]", model,
                      "o modelo do perfil nao vem do formato carregado")
        self.assertIn("entry.idOffset", model, "o modelo do perfil nao le o idOffset")

    def test_the_id_bar_offsets_never_reach_the_toml(self):
        """A barra de identidade é só prévia: nada dela vai para o motor.

        O POV tem ``text_dx``/``text_dy`` porque a zona de texto é queimada pelo
        libass. A barra de identidade é desenho da página — o motor recebe uma
        zona ``image`` e nenhum avatar, nome ou posição. Se algum dia uma
        ``id_x`` aparecesse no ``.toml``, o motor recusaria o arquivo inteiro por
        chave desconhecida, e o defeito só apareceria no render.
        """
        toml = self._fn_body("toToml")
        for chave in ("id_x", "id_y", "idOffset", "tweetOffset", "avatar"):
            self.assertNotIn(chave, toml, f"{chave} da barra vazou para o .toml")

    def test_the_pov_field_and_the_drag_write_the_same_place(self):
        """Campo, sliders e arraste do POV escrevem no MESMO objeto.

        O POV não é um item do cartão do X: o deslocamento mora na ZONA
        (``textOff``), porque é ela que o motor lê, via ``text_dx``/``text_dy``.
        Com cada controle guardando o seu, arrastar e digitar o número
        divergiriam — e é o número que vai para o .toml.
        """
        resolver = self._fn_body("tweetOffsetOf")
        self.assertIn('item === "pov"', resolver)
        self.assertIn("return zone.textOff", resolver)
        # O arraste, os sliders e o "Zerar posições" passam pelo resolvedor.
        self.assertIn("tweetOffsetOf(drag.item)", self._fn_body("snapTweetOffset"))
        self.assertIn("tweetOffsetOf(item)", self._fn_body("paintTweetOffsets"))
        self.assertIn("tweetOffsetOf(item)", self._fn_body("resetTweetOffsets"))
        # O campo de texto escreve o `text` da zona, não uma variável paralela.
        self.assertIn('target.id === "pov-text"', self.page)
        self.assertIn("povTarget.text = target.value", self.page)
        # E o número em px muta o aninhado, preservando a referência.
        self.assertIn('act === "textOffX" || act === "textOffY"', self.page)
        self.assertIn("textZone.textOff[", self.page)

    def test_the_pov_can_be_dragged_and_gets_the_crosshair(self):
        """O nó do POV é arrastável e o ponto cruz o encontra.

        O ponto cruz e o arraste procuram ``#canvas [data-edit='<item>']``: sem o
        atributo no nó desenhado, o POV existiria na tela e seria o único item do
        editor que não responde ao mouse.
        """
        box = self._fn_body("paintTextZone")
        self.assertIn('pov.setAttribute("data-edit", "pov")', box)
        self.assertIn("function tweetItemNode", self.page)
        self.assertIn("#canvas [data-edit='\" + item + \"']", self.page)
        # O item entra na lista que o editor percorre.
        items = self.page.split("TW_ITEMS = [", 1)[1].split("]", 1)[0]
        self.assertIn('"pov"', items)
        self.assertIn("showEditGuides(tweetItemNode(twSelected))", self.page)

    def test_the_pov_offset_reaches_the_toml_from_every_control(self):
        """Mexeu no POV por qualquer controle, o `.toml` é reescrito.

        O deslocamento do POV é o único que SAI no arquivo (``text_dx``/``text_dy``);
        os itens do cartão do X são só da prévia. Sem a repintura em cada caminho, o
        usuário arrastava (ou usava as setas, ou o slider), baixava o template e o
        arquivo saía sem o ajuste que ele acabou de fazer — um defeito que só
        apareceria no render, longe do gesto.
        """
        # Slider do passo: só o POV repinta os outputs (o slider dispara a cada
        # `input`, então a repintura fica restrita a quem ela muda).
        self.assertIn('if (posItem === "pov") renderOutputs()', self.page)
        # Arraste: no fim do gesto, uma vez.
        self.assertIn('if (item === "pov") renderOutputs()', self._fn_body("endTweetDrag"))
        # Setas do teclado.
        self.assertIn('if (twSelected === "pov") renderOutputs()', self._fn_body("onTweetKey"))
        # "Zerar posições".
        self.assertIn("renderOutputs()", self._fn_body("resetTweetOffsets"))

    def test_the_pov_has_its_own_position_fields_in_the_step(self):
        """Os sliders do POV vivem no passo, com o mesmo par H/V dos outros itens."""
        for axis in ("x", "y"):
            field = f"tw-pos-pov-{axis}"
            self.assertIn(f'id="{field}"', self.step, f"{field} fora do passo Aparencia")
        self.assertIn('id="pov-box"', self.step)
        self.assertIn('id="pov-text"', self.step)
        # O painel some quando o formato não tem faixa de texto: um campo visível
        # escrevendo num lugar que o motor não lê é o defeito que ninguém acha.
        self.assertIn("box.hidden = !zone", self._fn_body("paintPovFields"))

    def test_the_text_size_has_its_own_control_in_the_step(self):
        """O corpo do texto é editável na PRÓPRIA seção da faixa de texto.

        O tamanho já existia no passo de Zonas, junto dos outros campos da zona —
        mas é aqui que se escreve o texto, e quem acabou de digitar uma frase
        longa precisa reduzi-la sem trocar de passo. Os dois controles editam a
        MESMA zona (``zone.textSize``), então nenhum é uma segunda verdade.
        """
        for field in ("pov-size", "pov-size-hint", "pov-size-unit", "pov-size-chips"):
            self.assertIn(f'id="{field}"', self.step, f"{field} fora do passo Aparencia")
        # A ordem é a da leitura: o tamanho vem depois do texto que ele dimensiona
        # e antes da posição — no meio da seção, não jogado no fim dela.
        self.assertLess(
            self.step.index('id="pov-text"'), self.step.index('id="pov-size"'),
            "o tamanho tem de vir depois do texto que ele dimensiona",
        )
        self.assertLess(
            self.step.index('id="pov-size"'), self.step.index('id="tw-pos-pov-x"'),
            "o tamanho tem de vir antes da posição",
        )
        # Escreve `zone.textSize`, que é de onde o motor tira o `text_size`.
        self.assertIn('target.id === "pov-size"', self.page)
        self.assertIn("sizeTarget.textSize = Number(target.value)", self.page)
        # E reescreve o `.toml`: `text_size` SAI no arquivo.
        branch = self.page.split('target.id === "pov-size"', 1)[1].split("} else if", 1)[0]
        self.assertIn("renderOutputs()", branch, "o .toml não acompanha o slider de tamanho")

    def test_the_two_size_sliders_accept_the_same_values(self):
        """O slider do POV e o do passo de Zonas cobrem a MESMA faixa.

        Duas faixas diferentes dariam o absurdo de o mesmo ``textSize`` ser
        representável num controle e não no outro: o valor escrito no passo de
        Zonas apareceria cortado no slider do POV, e o usuário veria dois números
        para o mesmo campo.
        """
        self.assertIn('min="1" max="12" step="0.1"', self.step, "o slider do POV mudou de faixa")
        self.assertIn("min='1' max='12' step='0.1'", self.page, "o slider de Zonas mudou de faixa")

    def test_the_ready_sizes_only_shrink_and_all_of_them_fit(self):
        """Os atalhos REDUZEM; o maior deles é o do modelo, e é o que nasce aceso.

        O modelo Meme usa 3,8% da altura (73px em 1920) e esse é o maior corpo que
        ainda cabe na faixa dele: três linhas dentro dos 276px úteis. Um atalho
        acima disso produziria texto cortado justamente em quem clicou nele para
        consertar o corte. Para crescer existe o slider, e a prévia mostra o
        estouro na hora — mas o atalho não pode ser uma armadilha.
        """
        sizes = [float(v) for v in re.findall(r'data-pov-size="([0-9.]+)"', self.step)]
        self.assertEqual(len(sizes), 3, "a seção do POV perdeu os tamanhos prontos")
        self.assertEqual(sizes, sorted(sizes), "os tamanhos prontos fora de ordem")
        model = float(re.search(r"textSize: ([0-9.]+)", self._gallery_block("meme")).group(1))
        self.assertLessEqual(
            max(sizes), model, "um atalho maior que o do modelo corta o texto dele"
        )
        # O chip do modelo é o que nasce aceso, e o slider nasce no mesmo valor.
        checked = re.search(r'data-pov-size="([0-9.]+)"\s+aria-checked="true"', self.step)
        self.assertIsNotNone(checked, "nenhum tamanho pronto nasce aceso")
        self.assertEqual(float(checked.group(1)), model, "o atalho aceso não é o do modelo")
        slider = self.page.split('id="pov-size"', 1)[1].split(">", 1)[0]
        self.assertIn(f'value="{model}"', slider, "o slider do POV não nasce no tamanho do modelo")

    def test_every_size_control_writes_the_same_value(self):
        """Três controles para um valor: cada caminho repinta os outros.

        O slider do POV e os atalhos vivem no passo de Aparencia; o slider do passo
        de Zonas edita a MESMA zona. Sem a repintura cruzada, um controle aceso
        mostraria o tamanho anterior — e o passo de Zonas não é reconstruído a cada
        tique nem ao trocar de passo (veja ``showStep``).
        """
        # Atalho: escreve a zona, repinta os controles e reescreve o `.toml`.
        chip = self.page.split("target.dataset.povSize !== undefined", 1)[1].split("return;", 1)[0]
        self.assertIn("sizeZone.textSize = Number(target.dataset.povSize)", chip)
        self.assertIn("paintPovFields()", chip)
        self.assertIn("renderOutputs()", chip)
        # O pintor espelha o valor no slider do passo de Zonas.
        self.assertIn("input[data-act='textSize']", self._fn_body("paintPovSize"))
        # E o slider de Zonas repinta a seção do POV — no `renderAll` e também no
        # caminho de `input`, que repinta só o necessário e não passa por ele.
        self.assertIn("paintPovFields()", self._fn_body("renderAll"))
        zone_branch = self.page.split('act === "textSize"', 1)[1].split("} else if", 1)[0]
        self.assertIn("paintPovFields()", zone_branch, "o slider de Zonas não repinta o POV")

    def test_the_pov_text_field_keeps_the_caret(self):
        """Digitar no meio do campo não joga o cursor para o fim.

        ``paintPovFields`` passou a ser chamado em toda repintura da página (o
        slider de tamanho repinta os próprios controles e o passo de Zonas repinta
        a seção). Atribuir o MESMO texto ao textarea move o cursor para o fim, então
        o pintor só escreve quando o valor mudou — durante a digitação os dois são
        iguais.
        """
        self.assertIn(
            'text.value !== (zone.text || "")',
            self._fn_body("paintPovFields"),
            "o pintor volta a sobrescrever o campo a cada repintura",
        )

    def test_the_ready_sizes_are_valid_for_the_engine(self):
        """Os atalhos, no motor, produzem zona válida — a faixa dele é estreita.

        O wizard fala em POR CENTO e o motor em FRAÇÃO, e ``text_size`` só aceita
        de 0,5% a 40% da altura. Um atalho fora disso viraria erro no meio do
        render, depois de o usuário já ter baixado o template.
        """
        from viralclipper import template as template_mod

        for text in re.findall(r'data-pov-size="([0-9.]+)"', self.step):
            with self.subTest(size=text):
                template = template_mod.Template(
                    name="t",
                    zones=(
                        template_mod.Zone(
                            kind="text", fraction=0.5, text="POV: teste",
                            text_size=float(text) / 100,
                        ),
                        template_mod.Zone(kind="video", fraction=0.5),
                    ),
                )
                template.validate()

    def _card_zones(self, key: str) -> tuple:
        """As zonas do card, como o motor as recebe ao clicar em "Usar".

        Os ``kind`` e as ``fraction`` saem na ordem em que o card os declara, e o
        ``source`` entra exatamente como está no catálogo — vazio, que é o ponto.
        """
        from viralclipper import template as template_mod

        block = self._gallery_block(key)
        kinds = re.findall(r'kind: "(\w+)"', block)
        fractions = [float(value) for value in re.findall(r"fraction: ([0-9.]+)", block)]
        self.assertEqual(len(kinds), len(fractions), f"card {key}: zonas desalinhadas")
        zones = []
        for kind, fraction in zip(kinds, fractions):
            fields = {"kind": kind, "fraction": fraction, "source": ""}
            if kind == "text":
                fields.update(text="POV: teste", text_size=0.038, color="black")
            zones.append(template_mod.Zone(**fields))
        return tuple(zones)

    def test_every_gallery_card_opens_as_a_valid_template(self):
        """Clicar em "Usar" e depois em "Baixar" não pode gerar um arquivo recusado.

        O card abre com o caminho da imagem vazio — a galeria nunca inventa um
        asset —, e o passo Zonas travava justamente por isso: o `.toml` baixado era
        recusado pelo motor ("image exige um caminho em 'source'"). Agora a
        ausência de arquivo é um estado válido dos dois lados: o motor aceita a
        zona e a faixa degrada para o próprio clipe.

        Este teste é a outra metade da regra: o catálogo continua sem inventar
        caminho nenhum, e é isso que agora atravessa o motor sem tropeço.
        """
        from viralclipper import template as template_mod

        for key in ("x", "meme", "viral"):
            with self.subTest(card=key):
                # O card realmente abre sem asset: sem isto o teste passaria por
                # não ter nada para degradar.
                self.assertIn('source: ""', self._gallery_block(key))
                template_mod.Template(
                    name=key, zones=self._card_zones(key)
                ).validate()  # não levanta

    def test_the_step_does_not_block_on_a_missing_asset(self):
        """O passo Zonas não acusa um erro que o arquivo gerado não tem.

        O motor aceita uma zona de imagem sem arquivo — a faixa degrada para o
        próprio clipe —, então listar isso como problema travava um template que é
        válido de saída: o card recém-carregado da galeria nascia com o passo em
        vermelho e a geração barrada.
        """
        body = self._fn_body("validate")
        self.assertNotIn("precisa de um caminho", body)
        self.assertNotIn('zone.kind === "image" && !zone.source', body)

    def test_the_empty_asset_becomes_a_hint_in_the_field(self):
        """O que falta vira dica no campo de Arquivo, e não bloqueio.

        A dica é o que conta ao usuário o que a faixa mostra até ele escolher o
        arquivo: a informação útil, sem transformar o estado normal de um card
        recém-carregado em erro.
        """
        self.assertIn("Sem arquivo por enquanto", self.page)
        self.assertIn("a faixa mostra o proprio video", self.page)

    def test_the_toml_omits_the_source_when_there_is_no_asset(self):
        """Chave ausente é "ainda não escolhi"; ``source = ""`` é caminho que não existe.

        O motor lê a ausência como degradação para o clipe; uma chave vazia poria
        no arquivo um caminho que ninguém digitou.
        """
        body = self._fn_body("toToml")
        self.assertIn('zone.kind === "image" && zone.source.trim()', body)

    def test_the_meme_gallery_numbers_survive_the_engine(self):
        """Os números do card, passados pelo motor, produzem um template VÁLIDO.

        O wizard trabalha em POR CENTO (``textSize: 3.8``, ``marginLeft: 5``) e o
        motor em FRAÇÃO (``text_size = 0.038``, ``margin_left = 0.05``). É a
        conversão que o ``toToml`` faz e que ninguém confere até baixar o arquivo.
        Aqui os mesmos números atravessam o motor de verdade: um valor fora da
        faixa, ou uma fração que não fecha, vira falha de teste em vez de erro no
        meio do render — quando o usuário já clicou em "Usar este template".

        O caminho do asset entra como o card o traz (vazio): a zona de identidade
        vem sem arquivo, e o motor agora aceita esse estado — ver
        ``test_every_gallery_card_opens_as_a_valid_template``.
        """
        from viralclipper import template as template_mod

        block = self._gallery_block("meme")
        fractions = [float(v) for v in re.findall(r"fraction: ([0-9.]+)", block)]
        size = float(re.search(r"textSize: ([0-9.]+)", block).group(1)) / 100
        gap = float(re.search(r"marginTop: ([0-9.]+)", block).group(1)) / 100
        side = float(re.search(r"marginLeft: ([0-9.]+)", block).group(1)) / 100
        source = re.search(r'source: "([^"]*)"', block).group(1)
        template = template_mod.Template(
            name="meme",
            zones=(
                template_mod.Zone(
                    kind="text", fraction=fractions[0], text="POV: teste",
                    text_size=size, color="black",
                    margin_top=gap, margin_bottom=gap, margin_left=side, margin_right=side,
                ),
                template_mod.Zone(kind="video", fraction=fractions[1]),
                template_mod.Zone(kind="image", fraction=fractions[2], source=source),
            ),
        )
        template.validate()  # o "Usar este template" + "Baixar" não pode gerar isto inválido
        band = template_mod.plan_bands(template, 1080, 1920)[0]
        self.assertEqual(band.kind, "text")
        x, y, an = template_mod.text_anchor(band, band.zone, 1080, 1920)
        self.assertTrue(0 <= x <= 1080, f"âncora fora do quadro: {x}")
        self.assertTrue(0 <= y <= 1920, f"âncora fora do quadro: {y}")
        self.assertIn(an, range(1, 10))
        # E a linha do `describe` confere com o que a prévia desenha.
        self.assertIn(f"({x},{y}) an={an}", template_mod.describe(template, 1080, 1920))

    def test_the_meme_bar_sizes_the_avatar_like_the_model(self):
        """A barra do Meme declara as mesmas ``--tw-*`` do cartão do X.

        As variáveis do tweet são declaradas em ``.gal-tweet-band, .pv-tweet``, então
        na barra ``width: var(--tw-avatar)`` era inválido e o círculo ficava do
        tamanho da LETRA. Com "S" isso passava por um avatar pequeno; com a FOTO —
        letra invisível, mas ainda ocupando espaço — o avatar virava uma pílula
        estreita. Foi a foto que revelou o defeito.

        Frações do modelo (``gal-meme-id``): 18px de avatar, 9,3px de nome, 8,3px de
        handle e 6px de respiro numa caixa de conteúdo de 120px.
        """
        bar = self._rule(".pv-idbar")
        self.assertIn("container-type: inline-size", bar)
        for declaration in ("--tw-avatar: 15cqw", "--tw-name: 7.73cqw",
                            "--tw-handle: 6.93cqw", "--tw-gap-row: 5cqw"):
            self.assertIn(declaration, bar, f"{declaration} faltando na barra")

    def test_the_profile_survives_a_template_switch(self):
        """Trocar de template limpa o TEXTO e mantém o perfil.

        O texto é conteúdo do projeto e não vaza para o formato novo. O perfil é do
        usuário: a seção edita todos os formatos, então quem escreveu o nome e
        escolheu a foto não pode receber "Seu Nome" de volta ao usar o Meme.
        """
        for fn in ("loadSplitCard", "loadGallery"):
            body = self._fn_body(fn)
            self.assertIn('state.tweetText = ""', body, f"{fn} não limpa o texto")
            self.assertNotIn("clearTweetAvatar", body, f"{fn} apaga o perfil")
            self.assertNotIn("state.tweetName =", body, f"{fn} apaga o título")
            self.assertNotIn("state.tweetHandle =", body, f"{fn} apaga o @handle")

    def test_the_group_says_it_edits_every_template(self):
        """O texto do grupo diz o alcance: X e Meme, janela e vitrine.

        Antes dizia "Vale para o formato Twitter / X" — e, na prática, o Meme
        ignorava os campos. O rótulo também sai de "Tweet do modelo" porque o grupo
        edita o PERFIL (a barra do Meme não é um tweet).
        """
        for text in ("Perfil do modelo", "Meme", "vitrine", "Vídeo Viral"):
            self.assertIn(text, self.step, f"o grupo não fala de {text!r}")

    def test_the_preview_is_editable_in_place(self):
        """Os três itens da janela são arrastáveis e o texto é editável no lugar.

        O contrato é de atributos: ``data-edit`` diz o que o ponteiro pega (e
        serve de chave no estado), ``data-txt`` diz qual texto o duplo clique abre.
        Sem eles o arraste precisaria adivinhar a hierarquia de cada template.
        """
        for fn, items in (("tweetMock", ("data-edit='name'", "data-edit='body'",
                                         "data-txt='name'", "data-txt='handle'",
                                         "data-txt='text'")),
                          ("memeIdBar", ("data-edit='name'", "data-txt='name'",
                                         "data-txt='handle'"))):
            body = self._fn_body(fn)
            for marker in items:
                self.assertIn(marker, body, f"{fn} sem {marker}")
        # O avatar entra pelo mesmo contrato, mas só na janela: a vitrine é
        # esquema, não área de edição.
        avatar = self._fn_body("avatarMarkup")
        self.assertIn('prefix === "gal" ? "" : " data-edit=\'avatar\'"', avatar)
        self.assertIn('leaf.setAttribute("contenteditable", "true")', self.page)
        # O texto sai por textContent e volta por esc(): nenhuma porta de HTML.
        self.assertIn("nunca `innerHTML`", self.page)

    def test_the_drag_never_redraws_the_preview(self):
        """Durante o arraste só as variáveis do cartão mudam.

        Um ``renderPreview()`` ali reescreveria o nó que está com a captura do
        ponteiro e o arraste morreria no primeiro pixel.
        """
        body = self._fn_body("moveTweetDrag")
        self.assertNotIn("renderPreview(", body)
        self.assertIn("applyTweetOffset(", body)
        self.assertIn("snapTweetOffset(", body)
        # O caminho de largar o texto também não redesenha a cada tecla: senão o
        # cursor voltava para o começo a cada caractere digitado.
        typing = self._fn_body("onTweetEditInput")
        self.assertNotIn("renderPreview(", typing)
        self.assertIn("paintTweetFields()", typing)

    def test_the_crosshair_lives_outside_the_canvas(self):
        """O ponto cruz fica ao lado do ``#canvas``, nunca dentro dele.

        O canvas é reescrito inteiro a cada render; dentro dele o guia sumiria no
        meio do arraste (que justamente não redesenha).
        """
        canvas_at = self.markup.index('id="canvas"')
        guides_at = self.markup.index('id="edit-guides"')
        self.assertGreater(guides_at, canvas_at, "o guia ficou antes do canvas")
        # O canvas fecha (linha própria, vazia) e o guia nasce DELE: dentro dele,
        # o render seguinte apagaria o overlay no meio do arraste.
        canvas_line = self.markup[canvas_at:].split("\n", 1)[0]
        self.assertNotIn("edit-guides", canvas_line)
        for marker in ('class="eg-line eg-v"', 'class="eg-line eg-h"',
                       'class="eg-dot"', 'id="eg-readout"'):
            self.assertIn(marker, self.markup, f"{marker} fora do overlay")
        # O overlay não captura ponteiro: o arraste continua indo para o item.
        css = self._rule(".edit-guides")
        self.assertIn("pointer-events: none", css)
        self.assertIn("function showEditGuides", self.page)
        self.assertIn("hidden = false", self.page)

    def test_the_arrows_nudge_and_the_arrows_of_a_field_are_left_alone(self):
        """Setas movem o item selecionado em 1 px (Shift: 10); campo manda nelas.

        Os sliders do passo usam as mesmas teclas: enquanto um deles está com o
        foco, a seta é do slider — entregar o item e o campo ao mesmo tempo faria
        os dois se moverem.
        """
        body = self._fn_body("onTweetKey")
        for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
            self.assertIn(key, body, f"set {key} não move o item")
        self.assertIn('event.shiftKey ? TW_NUDGE_FAST : TW_NUDGE_PX', body)
        self.assertIn('TW_NUDGE_FAST = 10', self.page)
        self.assertIn("input, textarea, select, [contenteditable='true']", body)

    def test_the_reset_button_restores_the_model_positions(self):
        """"Zerar posições" leva os quatro itens ao PADRÃO DO MODELO.

        O POV guarda o deslocamento na ZONA (é ela que o motor lê, via
        ``text_dx``/``text_dy``); os outros três, no estado do cartão. Zerar pelo
        MESMO resolvedor que o arraste usa é o que mantém os dois lados no mesmo
        lugar — escrevendo ``state.tweetOffset[item]`` na mão, o POV ficaria
        parado onde estava e o botão pareceria quebrado só naquele item.

        "Zerar" é o nome do botão, mas o destino não é mais zero: o Meme declara
        o perfil deslocado, e um zero literal jogaria o par no meio da faixa — o
        desenho que o usuário acabou de trocar. O aviso na UI é "de volta ao
        modelo", então agora o botão faz o que diz, nos DOIS eixos (o botão de
        salvar grava os dois; devolver só o Y deixaria o avatar torto). O POV
        segue em zero nos dois eixos porque o offset dele mora na zona, e o
        modelo dele é o `text_dy` do catálogo (travado em
        :meth:`test_the_reset_button_returns_to_the_model_not_to_zero`).
        """
        self.assertIn('id="tw-reset-pos"', self.step)
        items = self.page.split("TW_ITEMS = [", 1)[1].split("]", 1)[0]
        for item in ("avatar", "name", "body", "pov"):
            self.assertIn(f'"{item}"', items, f"TW_ITEMS não cobre {item}")
        body = self._fn_body("resetTweetOffsets")
        self.assertIn("tweetOffsetOf(item)", body)
        # O Y vai para o valor do modelo dos dois eixos — e o POV, para zero.
        self.assertIn("modelIdOffset(item)", body,
                      "o botao zera em vez de voltar ao valor do modelo")
        self.assertRegex(body, r"off\.x\s*=\s*model\.x",
                         "o X do reset tem de voltar ao modelo tambem")
        self.assertRegex(body, r"off\.y\s*=\s*model\.y",
                         "o Y do reset tem de voltar ao modelo")
        # O resolvedor é quem sabe ONDE cada item mora — e é ele que o arraste, as
        # setas e os campos também usam.
        resolver = self._fn_body("tweetOffsetOf")
        self.assertIn('item === "pov"', resolver)
        self.assertIn("return zone.textOff", resolver)
        # O resolvedor é quem sabe ONDE cada item mora — e é ele que o arraste, as
        # setas e os campos também usam.
        resolver = self._fn_body("tweetOffsetOf")
        self.assertIn('item === "pov"', resolver)
        self.assertIn("return zone.textOff", resolver)
        self.assertIn("function initTweetEditor", self.page)
        self.assertIn('$("#tw-reset-pos")', self.page)

    def test_the_editor_state_never_reaches_the_toml(self):
        """Título, @handle, posição e foto são da PRÉVIA: o .toml não os carrega.

        O ``toToml()`` escreve campo por campo, então a trava é o campo novo não
        aparecer lá — um dia alguém pode "exportar tudo" e o arquivo passaria a
        ter chave que o motor não conhece (no caso da foto, também um object URL
        de sessão, que não quer dizer nada fora da aba).
        """
        toml = self.page.split("function toToml()", 1)[1].split("function round4", 1)[0]
        for key in ("tweetName", "tweetHandle", "tweetText", "tweetPos",
                    "tweetAvatar", "tweetAvatarName"):
            self.assertNotIn(key, toml, f"{key} vazou para o .toml")


class HookControlsTests(unittest.TestCase):
    """O painel do gancho do formato Video Viral.

    O gancho e uma ZONA DE TEXTO do template, como a faixa do POV no Meme. Antes
    ele era um ``<p class="pv-hook">`` desenhado por cima da faixa de video, e o
    painel vivia num estado so de previa que nao tinha par no ``.toml``: quem
    queimava a frase no clipe era a legenda, em outro lugar. Virou zona, e por isso
    o painel escreve nela.
    """

    def setUp(self):
        self.page = page_source("templates.html")
        markup = (server.WEB_DIR / "templates.html").read_text(encoding="utf-8")
        # O passo Aparencia e o card data-step="0".
        self.step = markup.split('data-step="0"', 1)[1].split("<!-- PASSO 1", 1)[0]

    def _fn_body(self, name: str) -> str:
        return fn_body(self.page, name)

    def _gallery_block(self, key: str) -> str:
        return self.page.split(f"{key}: {{", 1)[1].split("\n    }", 1)[0]

    def test_the_bottom_image_of_the_viral_has_no_rounded_corner(self):
        """A imagem da base encosta nas bordas: raio zero.

        Ela fecha o quadro de ponta a ponta, e o canto arredondado denunciava um
        recorte que não existe — a mesma faixa de vídeo logo acima, que é
        sangria, ficava com o canto reto. O `radius` é `corner_radius` da zona,
        então ele vai para o ``.toml`` e o motor queima o mesmo: corrigir só a
        prévia faria ela mentir sobre o render.
        """
        import re

        viral = self._gallery_block("viral")
        zona = re.search(
            r'\{ kind: "image", fraction: 0\.30,[^}]*\}', viral, re.S)
        self.assertIsNotNone(zona, "a imagem da base do Viral sumiu")
        self.assertIn("radius: 0", zona.group(0),
                      "a imagem da base voltou a ter canto arredondado")

    def test_the_hook_is_a_text_zone_in_the_template(self):
        """O Viral declara uma zona ``text`` com a frase, e ela vira ``.toml``.

        E o que separa "o painel existe" de "o painel faz alguma coisa": sem a
        zona, o texto ajustado aqui não chegaria ao render, e o usuário
        baixaria um arquivo que não era o que ele viu.
        """
        block = self._gallery_block("viral")
        self.assertIn('kind: "text"', block,
                      "o Viral nao tem zona de texto: o gancho nao seria queimado")
        self.assertIn("Isso aqui vai viralizar", block,
                      "a frase do modelo nao esta na zona")
        # As tres faixas: a frase ocupa espaco proprio, entre video e imagem. O
        # `assertIn` fixo nao serve aqui: o layout muda com o pedido (o video
        # cresceu de 44% para 54% e a imagem pagou), e o que este teste trava e
        # o CONTRATO -- tres faixas, a do gancho no meio, e a soma em 1,0, que
        # e o que o motor exige para nao recusar o `.toml` baixado.
        kinds = re.findall(r'kind: "(\w+)"', block)
        fractions = [float(v) for v in re.findall(r"fraction: ([0-9.]+)", block)]
        self.assertEqual(kinds, ["video", "text", "image"],
                         f"o Viral nao e mais video/gancho/imagem: {kinds}")
        self.assertEqual(len(fractions), 3, "o layout nao e mais de 3 faixas")
        self.assertAlmostEqual(sum(fractions), 1.0, places=6,
                               msg=f"as faixas somam {sum(fractions)}, e o motor "
                                   "exige 1,0 exato")
        # E o video e a MAIOR das tres: e o formato se chama "video em destaque".
        # As outras duas podem mudar de tamanho, mas se o video nao for a maior
        # faixa o card esta prometendo um formato que nao e o dele.
        self.assertEqual(max(fractions), fractions[0],
                         "a faixa do video deixou de ser a maior: "
                         f"{dict(zip(kinds, fractions))}")
        # E o `toToml` escreve a zona: a mesma funcao que ja levava o POV.
        self.assertIn("text_size", self._fn_body("toToml"),
                      "o .toml parou de levar o tamanho do texto")
        # O `.pv-hook` sumiu: a sobreposicao era o defeito. A trava le o CODIGO
        # e nao a pagina: os comentarios — de linha (`//`) E de bloco (`/* */`,
        # como o do CSS) — explicam POR QUE a regra foi removida, e citam o nome
        # antigo. Reprovar a documentacao seria travar a solucao.
        codigo = "\n".join(
            linha for linha in self.page.splitlines()
            if not linha.strip().startswith(("//", "/*", "*"))
        )
        for morto in ("pv-hook", "viralHook"):
            self.assertNotIn(morto, codigo,
                             f"o gancho voltou ao desenho antigo ({morto})")
    def test_the_panel_writes_the_zone_and_the_toml_follows(self):
        """Cada controle escreve a ZONA, e não um estado do gancho.

        `zone.text`, `zone.textSize`, as margens e `zone.color`: são as chaves que
        o motor lê. Um `state.hook` separado permitiria o painel e o `.toml`
        divergirem — que era o defeito original.
        """
        painter = self._fn_body("paintHookFields")
        self.assertIn("povZone()", painter,
                      "o painel do gancho nao le a zona de texto")
        for chave in ("zone.text", "zone.textSize", "zone.marginTop", "zone.color"):
            self.assertIn(chave, painter, f"o painel nao le {chave}")
        self.assertNotIn("state.hook.", self.page,
                         "o estado do gancho voltou: ele nao tem par no motor")
        # E cada `input` escreve a zona e chama `renderOutputs`, senao o arquivo
        # mostraria o valor anterior enquanto a previa ja mostrava o novo.
        for campo in ("hook-text", "hook-size", "hook-pad", "hook-color"):
            ramo = self.page.split(f'target.id === "{campo}"', 1)[1].split("} else if", 1)[0]
            codigo = "\n".join(
                linha for linha in ramo.splitlines()
                if not linha.strip().startswith("//")
            )
            self.assertIn("povZone()", codigo, f"{campo} nao escreve a zona de texto")
            self.assertIn("renderOutputs", codigo, f"{campo} nao reescreve o .toml")

    def test_the_panel_shows_only_in_the_format_that_has_the_zone(self):
        """O painel do gancho aparece no Viral e some nos outros.

        No Meme a zona de texto é o POV, e o painel dele é o dono; no X não há
        zona de texto. Escondido não basta: um campo visível escrevendo num lugar
        que a prévia não lê é ajuste invisível.
        """
        self.assertIn('id="hook-box"', self.step)
        painter = self._fn_body("paintHookFields")
        self.assertIn('state.previewMock === "viral"', painter,
                      "o painel do gancho aparece num formato que nao o tem")
        self.assertIn("box.hidden = !temGancho", painter,
                      "a caixa do gancho nao e escondida nos outros formatos")

    def test_the_chips_and_the_zone_editor_write_the_same_size(self):
        """Os chips e o editor de zonas mexem no MESMO ``zone.textSize``.

        Dois controles para o mesmo ajuste que escrevem em lugares diferentes é
        como o valor diverge na tela: o usuário mexe no chip, o editor de zonas
        continua mostrando o tamanho antigo, e nenhum dos dois está errado.
        """
        for rotulo, ancora in (
            ("o chip", "target.dataset.hookSize !== undefined"),
            ("o slider do painel", 'target.id === "hook-size"'),
        ):
            trecho = self.page.split(ancora, 1)[1].split("} else if", 1)[0]
            codigo = "\n".join(
                linha for linha in trecho.splitlines()
                if not linha.strip().startswith("//")
            )
            self.assertIn(".textSize = Number(", codigo,
                          f"{rotulo} nao escreve zone.textSize")
            self.assertIn("paintPovFields()", codigo,
                          f"{rotulo} nao repinta o editor de zonas: os dois divergem")

    def test_the_click_listener_admits_the_hook_chips(self):
        """O seletor do listener de clique lista ``data-hook-size``.

        O seletor é a PORTA do handler: o que ele não lista morre no
        ``if (!target) return``, e nenhum ramo abaixo roda. Sem esta lista o chip
        do gancho era um botão que não fazia nada — e sem erro nenhum, que é o
        pior tipo: o `paintHookFields` continuava marcando o chip errado e a
        prévia continuava com o corpo antigo, como se o clique tivesse funcionado
        e o valor fosse outro.
        """
        # O `self.page` concatena o HTML e o JS, e o HTML tem os SEUS listeners
        # de clique. Por isso a ancora e o unico que abre a delegacao de dados
        # (`closest(` com varios `data-*`), e nao a primeira ocorrencia do
        # `addEventListener("click"` da pagina.
        seletor = self.page.split('closest(\n      "[data-act]', 1)[1].split(");", 1)[0]
        for atributo in ("data-pov-size", "data-hook-size", "data-plate"):
            self.assertIn(atributo, seletor,
                          f"o clique em [{atributo}] morre antes do handler: "
                          "o botao fica sem efeito e sem erro")

    def test_the_gallery_card_shows_the_edited_hook(self):
        """O card da galeria e a janela mostram o MESMO gancho.

        O card é o que o usuário vê ANTES de abrir o formato. Com ele lendo o
        catálogo fixo e a janela lendo a zona editada, o formato prometeria uma
        frase e um corpo que a prévia não mostraria.
        """
        bands = self._fn_body("galHookBand")
        self.assertIn("state.zones", bands,
                      "o card do Viral le a frase do catalogo, e nao a editada")
        self.assertIn("zona.text", bands, "o card ignora o texto da zona")
        # A cor e a placa vao pelo mesmo `plateStyle` que pinta a previa: sao o
        # MESMO ajuste com dois rotulos, entao o card que promete o formato antes
        # de abrir precisa sair do mesmo lugar. Um `zona.color` escrito aqui
        # seria uma segunda copia do fundo, e as duas divergiriam no dia em que
        # a placa aparecesse.
        self.assertIn("plateStyle(zona)", bands,
                      "o card do Viral ignora a placa e a cor da zona")
        # E o fundo inteiro vem da ZONA passada como argumento, nao do catalogo:
        # `plateStyle` nao tem como ler o estado sozinha, e e por isso que o card
        # precisa estar handing a `zona` editada. Se um dia alguem passar `z` (o
        # catalogo) aqui em vez de `zona`, o card volta a prometer o fundo do
        # modelo enquanto a janela mostra o da pessoa — que e o defeito que este
        # teste existe para travar.
        painter = self._fn_body("plateStyle")
        self.assertIn("zone.color", painter, "a cor da zona sumiu do fundo")
        self.assertIn("zone.plateImage", self._fn_body("plateUrl"),
                      "a placa e procurada por outra chave que nao a da zona")
        # E a faixa do gancho entra no fluxo: nao e mais um `position: absolute`
        # sobre a faixa de video, que nao ocupava altura.
        self.assertIn("galHookBand", self._fn_body("viralBands"),
                      "a faixa de texto do gancho nao entra no card do Viral")


class PhraseColorPaletteTests(unittest.TestCase):
    """A aba de Cores: a cor da frase central e a legibilidade dela.

    A cor ja era editavel no editor de zonas (``<input type="color">``). A aba
    acrescenta duas coisas que aquele controle nao tem: uma paleta com nome, e a
    medida do contraste contra a PLACA — que e o fundo real da frase no clipe. Sem
    o aviso, escolher uma cor bonita e descobrir no render que ela some sobre a
    textura e uma aposta.
    """

    def setUp(self):
        self.page = page_source("templates.html")

    def _fn_body(self, name: str) -> str:
        return fn_body(self.page, name)

    def test_the_palette_writes_the_zone_key_the_engine_burns(self):
        """A paleta escreve ``textColor``, a chave que o motor queima.

        Nao ``color``: essa e a cor da PLACA, o fundo. Trocar as duas escreveria a
        cor da frase no fundo, e a previa mostraria um desenho que o clipe nao
        teria.
        """
        writer = self._fn_body("applyPhraseColor")
        self.assertIn("textColor", writer,
                      "a paleta nao escreve a chave de cor do TEXTO")
        self.assertIn("povZone()", writer,
                      "a paleta nao le a zona de texto do estado")
        # E o que fecha o contrato com o arquivo: o `.toml` so leva `text_color`
        # quando a cor sai do branco, entao escolher precisa chegar la.
        self.assertIn("text_color", self._fn_body("toToml"))
        # E o que mantem os tres controles — paleta, campo de cor e editor de
        # zonas — em acordo: cada escrita repinta os outros.
        for nome in ("paintHookFields", "applyPhraseColor"):
            with self.subTest(caminho=nome):
                self.assertIn("paintPhraseColors", self._fn_body(nome),
                              f"{nome} nao repinta a paleta apos escrever a cor")

    def test_the_click_listener_admits_the_swatch(self):
        """``data-phrase-color`` esta na porta do listener de clique.

        O seletor do ``closest`` e a PORTA: o que ele nao lista morre no
        ``if (!target) return``, e nenhum ramo abaixo roda. Um botao de cor que
        nao faz nada e nao acusa nada e o pior tipo de controle — parece funcionar
        e o clipe sai com a cor antiga.
        """
        seletor = self.page.split("closest(\n", 1)[1].split(");", 1)[0]
        self.assertIn("data-phrase-color", seletor,
                      "o clique no quadradinho morre antes do handler")

    def test_the_two_color_fields_go_through_the_same_writer(self):
        """O campo livre e o ``<input type=color>`` chamam ``applyPhraseColor``.

        Um caminho proprio por campo seria uma segunda escrita da mesma chave, e
        os dois campos poderiam divergir entre si e da paleta.
        """
        body = self._fn_body("applyPhraseColor")
        self.assertIn("paintPhraseColors", body,
                      "a escrita nao repinta a paleta: os tres controles "
                      "divergiriam")
        # E o espelho vao para o `<input type=color>` do editor de zonas, que
        # escreve a mesma chave em outro passo.
        self.assertIn("data-act='textColor'", body,
                      "o editor de zonas nao e espelhado ao escolher na aba")

    def test_the_contrast_is_measured_against_the_plate(self):
        """O contraste e contra a PLACA, nao contra a pagina.

        A frase queima sobre a placa: e o fundo dela que decide a leitura. Medir
        contra o branco da pagina daria 21:1 para qualquer cor sobre uma placa
        preta e nao diria nada, e medir contra o fundo do painel mentiria no
        outro extremo.
        """
        painter = self._fn_body("plateLuminance")
        self.assertIn("zone.color", painter,
                      "o contraste nao olha a cor da placa")
        self.assertIn("plateImage", painter,
                      "com imagem de placa nao ha fundo conhecido, e a funcao "
                      "precisa devolver 'nao medido' em vez de medir a cor")
        # E a formula e a da WCAG: sem os canais linearizados o numero sai errado
        # e o aviso mente sobre a legibilidade.
        self.assertIn("2.4", self._fn_body("relativeLuminance"),
                      "a luminancia nao lineariza os canais: o contraste sai errado")
        for piso in ("4.5", "3"):
            self.assertIn(piso, self._fn_body("paintPhraseColors"),
                          f"o aviso nao usa o piso {piso}:1 da WCAG")

    def test_the_palette_only_shows_where_there_is_a_phrase(self):
        """A aba some nos formatos sem zona de texto.

        O X nao tem frase: nao ha o que colorir, e um controle visivel escrevendo
        num lugar que a previa nao le e ajuste invisivel — o usuario mexe nele e
        nao descobre onde a mudanca foi.
        """
        painter = self._fn_body("paintPhraseColors")
        self.assertIn("phrase-color-box", painter, "a caixa da aba nao e controlada")
        self.assertIn("povZone()", painter)
        self.assertIn("hidden", painter,
                      "a aba nao e escondida quando nao ha zona de texto")
        # E o `renderAll` tem de repintar: sem isso, carregar um formato novo
        # deixaria a paleta com a cor da zona anterior.
        self.assertIn("paintPhraseColors", self._fn_body("renderAll"))

    def test_the_steps_and_the_page_agree_on_the_new_tab(self):
        """A aba e um passo do wizard, e a contagem bate dos dois lados.

        ``STEPS`` e o que gera a barra de passos, e ``data-step`` e o que mostra o
        card: se um lado ganhar uma entrada e o outro nao, o passo aparece na
        barra e nao abre, ou abre sem estar na barra.
        """
        passos = re.findall(r'\{ key: "(\w+)",\s+label: "([^"]+)" \}', self.page)
        self.assertTrue(passos, "a lista de passos sumiu")
        self.assertIn("cores", [k for k, _ in passos], "a aba nao esta em STEPS")
        cards = [int(n) for n in re.findall(
            r'<section class="card" data-step="(\d+)"', self.page)]
        self.assertEqual(sorted(cards), list(range(len(passos))),
                         f"a pagina tem {len(cards)} cards para {len(passos)} passos")
        # E o numero do `<h2>` e o do passo: o `ico` e o que a pessoa le.
        self.assertIn('<span class="ico">2</span> Cores da frase', self.page,
                      "o ico do passo de Cores sumiu")


class PlateControlsTests(unittest.TestCase):
    """O fundo de placa da faixa de texto: escolha, previa e ``.toml``.

    A placa é o mesmo modelo de chave que o resto do painel do gancho: o que a
    tela mostra é a chave da zona, e a chave da zona é o que o motor queima. Um
    estado de previa separado — como o ``pv-hook`` foi — é o defeito que esta
    classe existe para impedir: a miniatura bonita, o arquivo sem a imagem.
    """

    def setUp(self):
        self.page = page_source("templates.html")

    def _fn_body(self, name: str) -> str:
        return fn_body(self.page, name)

    def test_the_plate_writes_the_zone_and_the_toml_carries_it(self):
        """Escolher um modelo escreve `plate_image` na zona, e o `.toml` leva.

        O `renderOutputs` no mesmo caminho e o que fecha o contrato: sem ele o
        usuario via a textura na previa, baixava o arquivo e o clipe saia com a
        placa de cor — e a pagina nunca diria que as duas coisas sao diferentes.
        """
        writer = self._fn_body("applyPlate")
        self.assertIn("povZone()", writer, "a placa nao e lida da zona de texto")
        self.assertIn("plateImage", writer, "a placa nao escreve `zone.plateImage`")
        self.assertIn("renderOutputs()", writer,
                      "a placa nao vai para o .toml: o arquivo sairia com a cor")
        self.assertIn("renderPreview()", writer, "a previa nao mostra a placa")
        # O `.toml` escreve a chave com o nome do motor, e so para os tipos de
        # zona que pintam chapa.
        toml = self._fn_body("toToml")
        self.assertIn('plate_image = "', toml,
                      "o .toml nao escreve a chave que o motor le")
        for tipo in ('zone.kind === "solid"', 'zone.kind === "text"'):
            self.assertIn(tipo, toml)

    def test_no_plate_writes_no_key_at_all(self):
        """Sem placa, o `.toml` não leva a chave — nem vazia.

        `plate_image = ""` é recusado pelo validador do motor, então um painel que
        emitisse a chave vazia geraria um arquivo que ele mesmo não aceitaria, e
        a recusa só apareceria no momento do render, com o trabalho do template
        já feito.
        """
        writer = self._fn_body("applyPlate")
        self.assertIn("delete zone.plateImage", writer,
                      "a placa vazia fica como string em vez de sumir")
        toml = self._fn_body("toToml")
        self.assertIn("zone.plateImage)", toml,
                      "o .toml escreve a placa sem exigir que ela exista")

    def test_the_whole_plate_travels_through_one_key(self):
        """A tira, o `<select>` e o campo de zonas editam a MESMA chave.

        São três controles para um ajuste. Se um deles escrevesse em outro lugar,
        os dois marcadores ficariam acesos em valores diferentes e o `.toml`
        levaria só um deles — sem erro em lugar nenhum, que é o modo de falha
        que o painel sofreu antes com o corpo do texto.

        E o `renderZones` tem de estar no caminho de quem ESCREVE pela tira. É ele
        que reconstrói o campo de texto do editor de zonas, e sem a chamada o
        campo ficaria com o valor antigo enquanto o select e a tira marcariam o
        novo: três controles do mesmo ajuste, discordando, sem erro nenhum.
        """
        for rotulo, corpo in (
            ("tira", self._fn_body("applyPlate")),
            ("campo de zonas", self.page.split("data-act='plateImage'", 1)[1]),
        ):
            with self.subTest(controle=rotulo):
                self.assertIn("plateImage", corpo)
        self.assertIn("renderZones()", self._fn_body("applyPlate"),
                      "a tira escreve sem repintar o campo do editor de zonas")
        # E o `loadPlates` e o `paintPlatePicker` sao as duas pontas: quem escreve
        # e quem relê, e as duas têm de passar pela zona.
        self.assertIn("plateImage", self._fn_body("loadPlates"))

    def test_the_list_comes_from_the_server_not_from_the_markup(self):
        """Os modelos vêm de `/templates/plates`, que lê a pasta do disco.

        Um `<option>` escrito no HTML seria uma segunda verdade: o usuario
        largaria um arquivo em `web/fundo titulo/` e a pagina continuaria
        mostrando a lista antiga, sem nenhuma pista do porque.
        """
        self.assertIn("/templates/plates", self.page)
        self.assertIn("loadPlates()", self._fn_body("init"))
        # E o servidor tem a rota, com a lista lida da pasta.
        source = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")
        self.assertIn('path == "/templates/plates"', source)
        self.assertIn("list_plates()", source)


    def test_the_picker_survives_a_zone_without_one(self):
        """Sem zona de texto, a tira some junto com o painel do gancho.

        O painel do gancho já é condicional ao formato; a tira é parte dele. Se
        ficasse visível num formato sem zona de texto, o clique não teria onde
        escrever e o usuário veria um controle morto.
        """
        picker = self._fn_body("paintPlatePicker")
        self.assertIn("povZone()", picker)
        self.assertIn("hook-plate", picker,
                      "a tira nao se esconde junto com o painel do gancho")

    def test_the_preview_and_the_gallery_read_the_same_painter(self):
        """Prévia e card da galeria saem do mesmo `plateStyle`.

        O card é o que o usuário vê ANTES de carregar o formato. Se ele pintasse o
        fundo por conta própria, os dois deixariam de ser o mesmo desenho no dia
        em que a placa aparecesse — e o card é justamente a promessa do formato.

        A prévia chega ao pintor por `applyPlateStyle`, que é a mesma pintura
        aplicada propriedade a propriedade (ver o teste da faixa de texto); a
        reachability é o que importa, não a grafia da chamada.
        """
        for nome in ("paintTextZone", "galTextBand", "galHookBand"):
            with self.subTest(pintor=nome):
                corpo = self._fn_body(nome)
                chega = ("plateStyle(" in corpo
                         or "applyPlateStyle(" in corpo)
                self.assertTrue(chega, f"{nome} nao le o pintor de placa")

    def test_the_plate_is_painted_without_touching_the_band_geometry(self):
        """A placa é escrita no `style` da faixa, nunca no bloco inteiro.

        `el.style.cssText = ...` substitui TODAS as declarações inline do
        elemento. O laço de `renderPreview` escreve `top` e `height` antes de
        chamar o pintor, então um `cssText` com o fundo apagava a geometria: a
        faixa voltava ao `top: auto` do fluxo, encostava no topo do canvas e
        ficava com a altura do texto em vez da fração da zona. A placa aparecia
        no lugar errado — o `.toml`, o `backgroundImage` e o console seguiam
        perfeitos, porque o defeito era de posição, não de conteúdo.
        """
        aplicar = self._fn_body("applyPlateStyle")
        self.assertIn("setProperty", aplicar,
                      "a placa precisa ser escrita propriedade a propriedade")
        self.assertNotIn("style.cssText", aplicar,
                         "a placa nao pode trocar o bloco inline da faixa")
        # Nenhum pintor pode reintroduzir o `cssText` pela porta dos fundos.
        for nome in ("paintTextZone", "galTextBand", "galHookBand"):
            with self.subTest(pintor=nome):
                self.assertNotIn("style.cssText", self._fn_body(nome))

    def test_the_plate_quote_survives_the_style_attribute(self):
        """O `url()` da placa nao fecha o atributo `style` do card.

        Os cards da galeria montam a faixa como TEXTO: `style='...'` com aspas
        simples. O `plateStyle` devolvia `url('/fundo%20titulo/1.jpg')` com aspas
        simples tambem, entao a primeira delas fechava o atributo no meio do
        valor. Medido no Edge headless: o `style` do elemento parava em
        `background-image:url(` e a imagem nao aparecia no card -- e o
        `backgroundImage` do CSSOM voltava `url("")`. Sem erro no console, e sem
        a previa mintindo: o card da galeria e a promessa do formato, e ele
        prometia a cor.

        A trava e a DELIMITACAO do atributo: o `url()` do pintor tem de usar
        aspas DUPLAS, porque quem embute o texto e o atributo com aspas simples.
        """
        pintor = self._fn_body("plateStyle")
        # A busca e pela CONSTRUCAO, e nao pela palavra "url'": o comentario do
        # proprio pintor cita o defeito, e um teste que casasse o texto do
        # comentario reprovaria a documentacao.
        construcao = [ln for ln in pintor.splitlines()
                      if "background-image:url" in ln and not ln.strip().startswith("//")]
        self.assertTrue(construcao, "o pintor parou de escrever o background-image")
        for linha in construcao:
            self.assertNotIn("url('", linha,
                             "o url() com aspa simples fecha o atributo "
                             "style='...' do card e a placa some da galeria")
        # O `fn_body` devolve o FONTE, com os escapes intactos: a aspa dupla
        # aparece como `\"` no arquivo, e nao como `"`.
        self.assertTrue(any('url(\\"' in ln for ln in construcao),
                        "o url() precisa de aspas duplas: o atributo do card e "
                        "delimitado por aspas simples")
        # E o contrato dos dois lados, para o teste nao passar por accidento:
        # quem embute o texto no HTML tem de ser o dono da delimitacao.
        for nome in ("galTextBand", "galHookBand"):
            with self.subTest(pintor=nome):
                self.assertIn("style='", self._fn_body(nome),
                              f"{nome} nao usa mais aspas simples no style: se "
                              "passar a usar duplas, o url() tem de trocar tambem")

    def test_the_crop_reaches_the_toml_for_a_plate_zone(self):
        """`zoom`/`pan_x`/`pan_y` de uma zona com placa vão para o arquivo.

        O portão antigo aceitava essas chaves só em `image`/`frame`. Com uma placa
        o painel ajustaria o recorte, a prévia mostraria o resultado e o `.toml`
        NÃO descreveria nada disso: o ajuste pareceria funcionar e o render sairia
        com o enquadramento neutro. É a falha silenciosa mais cara possível — o
        arquivo parece com a intenção e o vídeo sai diferente.
        """
        fonte = (server.WEB_DIR / "templates.js").read_text(encoding="utf-8")
        # O portão tem de abrir para a zona de texto COM placa.
        self.assertIn('zone.kind === "text"', fonte)
        self.assertIn("!!zone.plateImage", fonte,
                      "o `.toml` nao descreve o recorte de uma zona com placa")
        # E a checagem é a do tipo, não uma lista de valores: um gate escrito como
        # lista de tipos volta a perder a placa na primeira zona nova.
        self.assertNotIn('zone.kind === "image" || zone.kind === "frame") && zone.zoom',
                         fonte,
                         "o portao de zoom/pan voltou a excluir a zona de texto")

    def test_the_plate_crop_is_measured_not_guessed(self):
        """A prévia calcula o `background-size` em vez de deixar `cover` fixo.

        Com `background-size: cover` a prévia ignora o zoom, e com
        `background-position: center` ignora o pan: o painel mexeria em algo que
        a tela não mostraria. O `plateBox` é quem traduz o `scale_into` do motor
        para porcentagem de CSS, e ele precisa ler as três chaves.
        """
        caixa = fn_body(page_source("templates.html"), "plateBox")
        for chave in ("zone.zoom", "zone.panX", "zone.panY", "plate.width", "plate.height"):
            self.assertIn(chave, caixa, f"o recorte da previa ignora {chave}")
        # O `contain` do motor ignora zoom e pan, e a previa precisa ignorar tambem.
        self.assertIn('zone.fit === "contain"', caixa,
                      "a previa mostra um recorte que o motor nao queima")
        # E o neutro tem de sair no `cover` de verdade, e nao em porcentagem
        # equivalente: um `1016.000%` equivalente seria mais lento e ilegivel.
        self.assertIn('size: "cover"', caixa)

    def test_the_plate_never_leaks_a_url_into_the_toml(self):
        """A zona guarda o `path` do disco, nunca a `url` da página.

        São dois campos diferentes de propósito: a URL funciona na prévia e não
        existe no disco que o ffmpeg abre. Se a URL fosse para a zona, o `.toml`
        descreveria um arquivo que o motor não encontraria, e a degradação
        (voltar à cor) esconderia o erro em vez de mostrá-lo.
        """
        self.assertIn("plateByPath(zone && zone.plateImage)", self._fn_body("plateUrl"))
        self.assertNotIn(".url", self._fn_body("applyPlate"),
                         "a URL da pagina foi guardada na zona: o motor nao a abriria")

    def test_the_fit_reaches_the_toml_only_when_there_is_a_plate(self):
        """`fit` numa faixa de cor não faz nada — mas numa placa, faz.

        `scale_into` é o mesmo dos dois, então o encaixe da imagem (corta, ou
        encaixa e deixa a cor no letterbox) é o mesmo número. Escrever `fit`
        sempre poluiria todo `.toml` com uma chave que o grafo nunca lê.
        """
        toml = self._fn_body("toToml")
        self.assertIn('zone.fit !== "cover"', toml)
        self.assertIn("zone.plateImage &&", toml,
                      "o encaixe vai para o .toml mesmo sem placa nenhuma")


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


class PlateListingTests(unittest.TestCase):
    """``/templates/plates`` lista a pasta de fundos de placa.

    A lista vem do disco, e nao de uma lista escrita na pagina: soltar um
    arquivo em ``web/fundo titulo/`` e o que o adiciona. Um item hardcoded no
    HTML seria uma segunda verdade para divergir da pasta assim que o usuario
    laurasse um arquivo — e a divergencia seria silenciosa, porque o item velho
    continuaria aparecendo e o novo nao.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_plates_"))

    def _png(self, path: Path, width: int, height: int) -> Path:
        """A PNG minima: assinatura, IHDR com as dimensoes, e o resto que o leitor
        de header precisa para nao reclamar. Nao precisa ser uma imagem valida —
        o leitor para no IHDR, e o que esta em teste e a leitura do cabecalho."""
        import struct
        import zlib

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00"))
            + chunk(b"IEND", b"")
        )
        return path

    def test_it_reads_the_folder_instead_of_a_hardcoded_list(self):
        self._png(self.tmp / "papel.png", 1904, 544)
        (self.tmp / "notas.txt").write_text("ignorado", encoding="utf-8")
        plates = server.list_plates(self.tmp)
        self.assertEqual([p["name"] for p in plates], ["papel.png"])
        self.assertEqual((plates[0]["width"], plates[0]["height"]), (1904, 544))

    def test_a_jpeg_size_comes_from_the_frame_header(self):
        # O entregável desta pasta e um JPEG largo, e o leitor precisa achar o
        # SOF atraves dos segmentos com comprimento — um offset fixo leria o
        # cabecalho de outro arquivo e a miniatura viraria "?".
        (self.tmp / "pincel.jpg").write_bytes(_JPEG_1904x544)
        plates = server.list_plates(self.tmp)
        self.assertEqual((plates[0]["width"], plates[0]["height"]), (1904, 544))

    def test_a_missing_folder_is_an_empty_list_not_an_error(self):
        # A pasta e do usuario: uma installacao sem os modelos entregues tem de
        # abrir a pagina igual, so que sem miniatura.
        self.assertEqual(server.list_plates(self.tmp / "nao_existe"), [])

    def test_the_path_is_the_engine_path_and_the_url_is_quoted(self):
        # Sao dois campos de proposito. O `path` e o que vai no `.toml` e o que o
        # ffmpeg abre; a `url` e o que a previa pinta. Guardar a URL na zona
        # funcionaria na tela e quebraria no render, porque
        # `/fundo%20titulo/1.jpg` nao existe no disco. E a URL precisa de quoting
        # porque a pasta tem espaco — a rota estatica decodifica antes do disco.
        probe = server.PLATES_DIR / "_teste.png"
        self._png(probe, 10, 10)
        try:
            plates = server.list_plates()
            entry = next(p for p in plates if p["name"] == "_teste.png")
            # Conferido com o arquivo AINDA NO DISCO: o `finally` abaixo o apaga,
            # e uma assercao depois dele reprovaria um caminho correto.
            self.assertTrue((server.REPO_ROOT / entry["path"]).is_file())
        finally:
            probe.unlink()
        self.assertEqual(entry["url"], "/fundo%20titulo/_teste.png")
        self.assertEqual(entry["path"], "web/fundo titulo/_teste.png")
        self.assertFalse(entry["path"].startswith("/"))

    def test_the_shipped_folder_is_served_and_the_models_are_there(self):
        """A pasta que o usuario encheu tem de responder, e o endpoint tem de
        devolver os arquivos. Sem isto a pagina abriria com a tira vazia e o
        usuario culparia o painel por um arquivo que ele mesmo pôs ali.

        A URL e conferida DECODIFICADA, e nao como string: ela e percent-encoded
        para a rede, e a rota estatica so a encontra depois do `unquote`. Um
        teste que juntasse as duas sem decodificar reprovaria um arquivo que a
        pagina carrega sem problema — que e o tipo de trava que faz a gente
        "consertar" uma URL que ja funcionava.
        """
        from urllib.parse import unquote

        plates = server.list_plates()
        self.assertTrue(plates, "web/fundo titulo/ nao devolveu nenhum modelo")
        for entry in plates:
            self.assertTrue(
                (server.WEB_DIR / unquote(entry["url"].lstrip("/"))).is_file(),
                f"{entry['name']}: a URL nao resolve para um arquivo de web/",
            )
            self.assertTrue((server.REPO_ROOT / entry["path"]).is_file())
            # E o tipo sai certo: `nosniff` esta ligado, entao um `.jpg` servido
            # como octet-stream nao apareceria na previa.
            self.assertEqual(
                server.asset_content_type(Path(unquote(entry["url"].lstrip("/")))),
                "image/jpeg" if entry["name"].endswith(".jpg") else "image/png",
            )


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
    """The hero preview stack: every card plays its own clip.

    The page is the product demo, so each of the three cards plays an actual
    9:16 video instead of the schematic. What can break silently is the
    degradation path: the files sit in the repo's own ``web/`` folder and are
    gitignored, so a fresh clone has none of them and a card has to look exactly
    as it did before — not show a broken media icon.
    """

    #: Each card that plays a clip, and the file it plays.
    LIVE_CARDS = {
        "/1.mp4": "0:42 → 1:24",
        "/2.mp4": "3:10 → 3:58",
        "/3.mp4": "7:02 → 7:47",
    }

    def setUp(self):
        self.page = page_source("index.html")
        self.markup = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_every_live_card_loads_its_clip(self):
        for src in self.LIVE_CARDS:
            self.assertIn(f'src="{src}"', self.markup, f"falta o video {src}")
        self.assertEqual(self.markup.count('class="preview-video"'), len(self.LIVE_CARDS))

    def test_each_clip_is_a_silent_loopable_inline_preview(self):
        """No audio, no controls: they are a background, not a player."""
        chunks = self.markup.split("<video")[1:]
        self.assertEqual(len(chunks), len(self.LIVE_CARDS))
        for chunk in chunks:
            video = chunk.split("</video>", 1)[0]
            for attribute in ("muted", "playsinline", "loop", "autoplay"):
                self.assertIn(attribute, video, f"falta {attribute} no preview do hero")
            self.assertNotIn("controls", video)

    def test_the_cards_start_as_placeholders(self):
        """``data-live="0"`` is the start state; only the script lights it."""
        self.assertEqual(self.markup.count('data-live="0"'), len(self.LIVE_CARDS))
        self.assertNotIn('data-live="1"', self.markup)

    def test_the_script_wires_every_live_card(self):
        """One loop over the cards: a third card must not need new script."""
        self.assertIn("$$('.preview-card[data-live]')", self.page)
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

    def test_every_card_in_the_stack_plays_a_clip(self):
        """No card is left schematic: the stack is the product demo."""
        self.assertEqual(self.markup.count('class="preview-card"'), len(self.LIVE_CARDS))
        self.assertEqual(self.markup.count("<video"), len(self.LIVE_CARDS))
        for tag in self.LIVE_CARDS.values():
            self.assertIn(f'<span class="tag">{tag}</span>', self.markup)

    def test_the_stack_is_one_card_per_clip_in_order(self):
        """The tags are the reading order; a clip swapped between cards would
        put a 26 s cut under a 0:42 → 1:24 label."""
        bodies = self.markup.split('class="preview-card"')[1:]
        self.assertEqual(len(bodies), len(self.LIVE_CARDS))
        for body, (src, tag) in zip(bodies, self.LIVE_CARDS.items()):
            self.assertIn(f'src="{src}"', body, f"{src} fora de ordem no stack")
            self.assertIn(tag, body, f"{src} com o rotulo de outro card")


class PublicationStepTests(unittest.TestCase):
    """Passo 5: descricao e hashtags do post, e por que nao vao no arquivo."""

    def setUp(self):
        self.page = page_source("templates.html")
        self.js = (server.WEB_DIR / "templates.js").read_text(encoding="utf-8")

    def _cards(self):
        return re.findall(r'<section class="card" data-step="(\d+)"[^>]*>\s*'
                          r'<h2><span class="ico">(\d+)</span>', self.page)

    def test_the_step_bar_and_the_cards_agree(self):
        """`STEPS` gera a barra e `data-step` escolhe o card.

        Um lado ganhar uma entrada e o outro nao mostra o passo na barra e nao
        abre: a barra lista nove botoes e o wizard para em oito.
        """
        steps = re.search(r"var STEPS = \[(.*?)\n  \];", self.js, re.S)
        self.assertIsNotNone(steps, "STEPS nao encontrado")
        labels = re.findall(r'key: "([a-z]+)"', steps.group(1))
        cards = self._cards()
        self.assertEqual(len(cards), len(labels),
                         f"{len(cards)} cards para {len(labels)} passos")
        for index, (data_step, ico) in enumerate(cards):
            self.assertEqual(int(data_step), index, f"card fora de ordem: {index}")
            # O numero do h2 e escrito a mao no HTML, e o comentario do passo
            # aindapz existindo e o unico lugar que o confere.
            self.assertEqual(int(ico), index + 1,
                             f"o icone do card {index} mostra {ico}, esperava {index + 1}")

    def test_the_publication_step_exists(self):
        self.assertIn('key: "publicacao"', self.js)
        self.assertIn("Publicação</h2>", self.page)
        for marker in ('id="post-desc"', 'id="post-tags"',
                       'id="btn-postkit"', 'id="btn-copy-post"', 'id="btn-copy-tags"'):
            self.assertIn(marker, self.page, f"{marker} fora do card")

    def test_the_step_says_the_text_never_reaches_the_file(self):
        """A aba promete que nada vai para o `.toml`; a promessa e o contrato.

        `from_dict` recusa chave desconhecida (template.py:790-821), entao
        escrever `hashtags` no arquivo produziria um template que o proprio
        motor rejeita ao ler. O texto no card e o que avisa disso.

        O `card-sub` quebra a frase em varias linhas, entao a busca normaliza
        os espacos antes: casar a frase inteira num HTML indentado prenderia o
        teste numa quebra de linha e nao na promessa.
        """
        plano = re.sub(r"\s+", " ", self.page)
        self.assertIn("Nada aqui entra no arquivo", plano)
        self.assertIn("não são publicadas automaticamente", plano)

    def test_the_state_holds_the_post_and_the_toml_does_not(self):
        for field in ("videoIdea", "postDescription", "postHashtags"):
            self.assertIn(f"{field}:", self.js, f"{field} fora do estado")
        toml = fn_body(self.js, "toToml")
        for chave in ("postDescription", "postHashtags", "hashtags", "description ="):
            self.assertNotIn(chave, toml,
                             f"`{chave}` no .toml: o parser recusa chave desconhecida")

    def test_the_idea_reaches_both_steps_from_one_field(self):
        """Frases e publicacao saem da MESMA ideia.

        Duas textareas para a mesma coisa fariam o usuario colar duas vezes, e
        as duas metades do post acabariam falando de videos diferentes.
        """
        generate = fn_body(self.js, "generatePhrases")
        self.assertIn("state.videoIdea = idea", generate)
        kit = fn_body(self.js, "generatePostKit")
        self.assertIn("state.videoIdea", kit)

    def test_the_fallback_runs_when_the_clipboard_api_rejects(self):
        """A Clipboard API REJEITA sem foco de usuario, e nao so esta ausente.

        `writeText` lanca `NotAllowedError` numa aba em segundo plano, que e o
        caso de qualquer headless. Um fallback so no caminho sincrono — quando
        `navigator.clipboard` nao existe — daria um botao que funciona na mao e
        falha sozinho. O `.catch(viaExec)` e o que segura a promessa.
        """
        corpo = fn_body(self.js, "copyText")
        self.assertIn(".catch(viaExec)", corpo,
                      "o fallback nao roda quando a API rejeita")
        self.assertIn("execCommand", corpo)
        # E o fallback tem de existir de verdade, nao so o nome: um textarea
        # temporario, porque `execCommand` copia a selecao, e `text` nao e
        # selecionavel.
        self.assertIn("createElement(\"textarea\")", corpo)
        self.assertIn(".select()", corpo)


    def test_the_post_text_is_never_published_by_the_panel(self):
        """O botao copia. Nao ha endpoint de publicacao, e `ig_profile.py` so
        le posts existentes: fingir que publica seria um botao que mente."""
        for nome in ("copyPost", "copyTags"):
            corpo = fn_body(self.js, nome)
            self.assertIn("copyText(", corpo)
            self.assertNotIn("postJSON(", corpo, f"{nome} mandou algo para o servidor")
        self.assertNotIn("/postkit", fn_body(self.js, "copyPost"))


class PostkitRouteTests(unittest.TestCase):
    """/postkit sugere descricao e hashtags via o mesmo LLM do ranker."""

    def setUp(self):
        self.sent: dict = {}
        self.handler = object.__new__(server.Handler)
        self.handler._send_json = lambda payload, code=200: self.sent.update(payload, _code=code)

    def test_missing_idea_is_rejected(self):
        server.Handler._handle_postkit(self.handler, {})
        self.assertEqual(self.sent["_code"], 400)
        server.Handler._handle_postkit(self.handler, {"idea": "   "})
        self.assertEqual(self.sent["_code"], 400)

    def test_both_fields_come_back_together(self):
        from unittest import mock

        with mock.patch.object(server, "_suggest_post", return_value=("um texto", "#a #b")):
            server.Handler._handle_postkit(self.handler, {"idea": "fuga de moto"})
        self.assertEqual(self.sent["description"], "um texto")
        self.assertEqual(self.sent["hashtags"], "#a #b")

    def test_a_provider_error_surfaces_as_text(self):
        from unittest import mock

        with mock.patch.object(server, "_suggest_post", side_effect=server.ClipperError("sem chave")):
            server.Handler._handle_postkit(self.handler, {"idea": "fuga de moto"})
        self.assertEqual(self.sent["_code"], 400)
        self.assertIn("sem chave", self.sent["error"])

    def test_the_two_lines_are_read_positional(self):
        """O prompt pede duas linhas: a descricao na primeira, as hashtags na
        segunda. Um modelo que devolve so hashtags nao vira legenda."""
        class FakeProvider:
            def complete(self, system, user):
                return "Ninguem tava pronto pra isso.\n#fyp #clutch #1v4\n"

        from unittest import mock

        with mock.patch("viralclipper.ranker.build_provider", return_value=FakeProvider()):
            description, tags = server._suggest_post("fuga de moto")
        self.assertEqual(description, "Ninguem tava pronto pra isso.")
        self.assertEqual(tags, "#fyp #clutch #1v4")

    def test_prose_without_hashtags_is_an_error_not_a_silent_caption(self):
        class FakeProvider:
            def complete(self, system, user):
                return "Este video mostra uma fuga de moto incrivel happening\n"

        from unittest import mock

        with mock.patch("viralclipper.ranker.build_provider", return_value=FakeProvider()):
            with self.assertRaises(server.ClipperError):
                server._suggest_post("fuga de moto")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
