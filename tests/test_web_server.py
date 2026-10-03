"""Unit tests for the web UI server path resolution.

The gallery serves clips whose file names carry accents; the browser sends
them percent-encoded, and :mod:`web.server` must decode before it touches the
filesystem. These tests lock the pure path logic; the HTTP layer is exercised
by running the real server against the rendered output.
"""

from __future__ import annotations

import io
import os
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


class GridTrackAndPollingTests(unittest.TestCase):
    """O grid que encolhe e a sondagem que para.

    Duas correcoes com a mesma forma: o `index` usava a forma que o `scrap`
    ja tinha abandonado. O `scrap-grid` faz ``minmax(0, 400px) minmax(0, 1fr)``
    e o `main-grid` do index fazia ``1fr 360px``; o `poll` do scrap tem
    `stop`/`follow` e o do index era um `setInterval` solto. As duas diferencas
    sao o mesmo defeito: a primeira deixa a coluna vazar, a segunda deixa a
    requisicao correr.
    """

    def body(self, nome: str) -> str:
        return (server.WEB_DIR / nome).read_text(encoding="utf-8")

    def test_the_main_grid_track_can_shrink_below_its_content(self):
        """`minmax(0, …)` nos dois tracks do `.main-grid`.

        Filho de grid tem `min-width: auto` por padrao, ou seja, nunca encolhe
        abaixo do seu conteudo. A coluna da esquerda e o formulario, com o
        `<textarea>` de transcricao e o `<select>` de 36 presets: qualquer um
        deles com largura minima intrinseca maior que a coluna empurra o track
        e o `1fr` cresce alem da tela. O `.scrap-grid` ja usa `minmax(0, …)`
        pelos dois motivos; aqui faltava.
        """
        css = self.body("index.css")
        bloco = re.search(r"\.main-grid\s*\{([^}]*)\}", css)
        self.assertIsNotNone(bloco, "o .main-grid sumiu")
        tracks = re.search(r"grid-template-columns:\s*([^;}]+);", bloco.group(1))
        self.assertIsNotNone(tracks, "o .main-grid nao declara grid-template-columns")
        colunas = tracks.group(1)
        self.assertEqual(
            colunas.count("minmax(0,"), 2,
            f"as duas colunas precisam de minmax(0, …): {colunas}")
        # `1fr` puro e o que volta a vazar: `minmax(0, 1fr)` e a forma que
        # colapsa. A trava e no `minmax`, nao no `1fr`.
        self.assertNotIn(
            "grid-template-columns: 1fr 360px", css,
            "o .main-grid voltou para `1fr 360px`, que encolhe pelo conteudo")

    def test_the_scrap_grid_keeps_its_minmax(self):
        """A referência nao regride: o `.scrap-grid` continua com os dois.

        O teste do index so passaria se alguem copiasse a forma boa para ca. O
        inverso tambem importa: a forma boa do scrap nao pode ser trocada por
        `1fr 1fr` enquanto o index e corrigido, porque a correcao de uma pagina
        nao pode ser a perda da outra.
        """
        css = self.body("scrap.css")
        bloco = re.search(r"\.scrap-grid\s*\{([^}]*)\}", css)
        self.assertIsNotNone(bloco)
        colunas = re.search(r"grid-template-columns:\s*([^;}]+);", bloco.group(1))
        self.assertEqual(
            colunas.group(1).count("minmax(0,"), 2,
            f"o .scrap-grid perdeu um minmax(0, …): {colunas.group(1)}")

    def test_the_polling_stops_when_the_tab_is_hidden(self):
        """A sondagem para com a aba escondida, e volta a pedir ao voltar.

        `setInterval(poll, 4000)` solto e uma requisicao a cada 4s para sempre,
        mesmo com a aba em segundo plano. O `scrap` ja resolve os timers dele
        com `stop`/`follow` e `clearInterval`; aqui o padrao e o mesmo, com o
        gancho do `visibilitychange` — que tambem e o que o hero ja escuta,
        entao sao DOIS listeners no mesmo evento, cada um no seu caso.
        """
        js = self.body("index.js")
        # O `setInterval` SOLTO e o defeito: nao ha como parar um intervalo que
        # ninguem guarda a referencia. A busca e pela ATRIBUICAO — `pollTimer =`
        # a pegaria e passaria, porque e a forma correta.
        #
        # E a busca precisa do ponto-e-virgula: sem ele, a propria linha deste
        # teste conteria o texto procurado e o `assertNotIn` reprovaria a si
        # mesmo. Foi o que aconteceu na primeira versao.
        solto = re.search(r"^\s*setInterval\(poll,\s*4000\);", js, re.M)
        self.assertIsNone(
            solto,
            "a sondagem voltou a ser um setInterval solto (sem guardar a "
            "referencia) — nao ha como parar um intervalo assim")
        # E o par stop/start, com `clearInterval` dentro do stop e a variavel
        # zerada: e o que garante que o timer antigo morre antes do novo.
        self.assertIn("clearInterval(pollTimer)", js,
                      "a sondagem nao e parada em lugar nenhum")
        self.assertIn("pollTimer = null", js,
                      "a referencia do timer nao e limpa: dois timers "
                      "sobrevivem a um start/follow duplo")
        # E o gancho do estado da aba, com os dois lados: esconder PARA,
        # mostrar RETOMA. Só um dos lados deixaria a sondagem morta para sempre
        # depois do primeiro `hidden`.
        self.assertIn("document.addEventListener('visibilitychange'", js)
        self.assertIn("if (document.hidden) stopPolling()", js,
                      "esconder a aba nao para a sondagem")
        self.assertIn("startPolling()", js,
                      "nao ha como retomar a sondagem depois de parar")

    def test_the_scrap_polling_keeps_its_stop_follow_pair(self):
        """A referencia do scrap nao regride: os tres timers continuam parando."""
        js = self.body("scrap.js")
        for nome in ("stopPolling", "stopArchivePolling"):
            with self.subTest(timer=nome):
                self.assertIn(f"function {nome}", js,
                              f"o {nome} do scrap sumiu")
                self.assertIn("clearInterval", js)
        # Os dois chamadores existem, que e o que liga o stop ao ciclo de vida.
        self.assertIn("stopPolling();", js)
        self.assertIn("stopArchivePolling();", js)


class SharedEscaperTests(unittest.TestCase):
    """Um `esc` só, e o forte.

    Havia duas implementações e elas não eram iguais. A do `index.js` usava um
    `<div>` descartável, escrevia o valor em `textContent` e lia o `innerHTML`
    de volta: escapa ``&``, ``<`` e ``>``, e **não escapa aspas** — o
    `innerHTML` de um nó de texto não precisa disso, porque não há aspa para
    fechar. A do `scrap.js` fazia cinco `replace` e escapava também as duas
    aspas.

    A do scrap é a que serve, e a razão está no uso: o scrap passa `esc(href)`
    dentro de um atributo (``href="..."``, ``title="..."``), e ali uma aspa
    sem escapar fecha o atributo e o resto da string vira markup. O
    ``server.py`` escapa com ``quote=True`` pelo mesmo motivo, e o comentário
    de lá descreve o ataque. O index só usava ``esc`` em conteúdo de texto, de
    modo que a versão fraca nunca chegou a falhar ali — mas seria um bug no
    dia em que alguém precisasse de um atributo, e a falha seria silenciosa.
    """

    def arquivo(self, nome: str) -> str:
        return (server.WEB_DIR / nome).read_text(encoding="utf-8")

    def test_only_the_common_file_defines_the_escaper(self):
        """Nenhuma página define `esc` por conta própria.

        Esta é a regra que trava a unificação. Duas definições podem coexistir
        dias sem doer: a mais fraca continua sendo a que o index usa, o
        scrap segue correto, e a duplicata só aparece quando alguém edita a
        errada.
        """
        for nome in ("index.js", "scrap.js"):
            with self.subTest(arquivo=nome):
                self.assertNotRegex(
                    self.arquivo(nome), r"function\s+esc\s*\(",
                    f"{nome} define o esc por conta propria: ha duas versoes "
                    "e a mais fraca pode voltar a ser a que roda")

    def test_the_common_escaper_is_loaded_before_the_page_scripts(self):
        """O `comum.js` entra ANTES do script da página, nas duas.

        Os dois scripts de página são IIFE com `'use strict'`: não veem nada do
        escopo de outro arquivo, e o `esc` chega pelo global. Se o comum
        carregasse depois, a página rodaria com `esc` indefinido — e isso
        aparece só no clique que monta o primeiro card, não no carregamento.
        """
        for pagina, script in (("index.html", "index.js"),
                               ("scrap.html", "scrap.js")):
            with self.subTest(pagina=pagina):
                html = self.arquivo(pagina)
                # O `src` carrega cache-busting (`/comum.js?v=studio-20261001`):
                # a query sai antes de comparar, senao nenhum `src` casa e o
                # teste reprova paginas que carregam tudo na ordem certa.
                ordem = [
                    src.split("?", 1)[0]
                    for src in re.findall(r'<script[^>]+src="/([^"]+\.js)[^"]*"', html)
                ]
                self.assertIn("comum.js", ordem,
                              f"{pagina} nao carrega o comum.js")
                self.assertLess(ordem.index("comum.js"), ordem.index(script),
                                f"{pagina} carrega o comum.js depois de "
                                f"{script}: o esc nao estara definido quando a "
                                "pagina rodar")

    def test_the_common_escaper_covers_the_quotes(self):
        """A versão comum escapa as DUAS aspas, e o ``&`` vai primeiro.

        A aspa é o que separa esta implementação da antiga, e a ordem dos
        ``replace`` é o detalhe que ninguém nota: escapar ``&`` depois de
        ``<`` e ``>`` transformaria o ``&`` de ``&lt;`` em ``&amp;lt;``, e o
        browser mostraria ``&lt;`` como texto em vez de ``<``.
        """
        esc = self.arquivo("comum.js")
        pedacos = [
            (r"/&/g", "&amp;", "falta escapar o &"),
            (r"/</g", "&lt;", "falta escapar o <"),
            (r"/>/g", "&gt;", "falta escapar o >"),
            (r'/"/g', "&quot;", "falta escapar a aspa dupla"),
            (r"/'/g", "&#39;", "falta escapar a aspa simples"),
        ]
        for busca, substitui, porque in pedacos:
            with self.subTest(escape=substitui):
                self.assertIn(busca, esc, porque)
                self.assertIn(substitui, esc,
                              f"o {busca} nao substitui por {substitui}")

        # A ordem vale no CORPO DA FUNCAO, e nao no arquivo: o comentario do
        # cabecalho cita `&lt;` para explicar o perigo, e citá-lo ali e correto.
        # Medir no arquivo inteiro acusava a propria documentacao.
        #
        # O corpo termina na chave de fechamento da funcao, e nao no primeiro
        # `;`: o `;` do `if` que vem logo depois do `return` cortava a sequencia
        # pela metade e os dois ultimos escapes sumiam da medicao.
        inicio = esc.index("return String(")
        corpo = esc[inicio:esc.index("\n  }", inicio)]
        pos_amp = corpo.index("&amp;")
        for _, entidade, _ in pedacos[1:]:
            with self.subTest(ordem=entidade):
                self.assertIn(entidade, corpo,
                              f"{entidade} sumiu do corpo do esc")
                self.assertLess(
                    pos_amp, corpo.index(entidade),
                    f"{entidade} vem antes de &amp;: um & gerado por um escape "
                    "anterior viraria &amp;lt; e o browser mostraria o texto "
                    "cru em vez do caractere")

    def test_the_pages_escape_through_the_shared_helper(self):
        """As duas páginas usam `esc(...)` nos pontos onde o valor é de fora.

        A checagem é de uso, não de definição: garante que a extração não
        deixou markup montado à mão no lugar onde o texto de terceiro entra.
        """
        for nome in ("index.js", "scrap.js"):
            with self.subTest(arquivo=nome):
                js = self.arquivo(nome)
                usos = len(re.findall(r"\besc\(", js))
                self.assertGreater(usos, 0,
                                   f"{nome} parou de usar esc: o texto de "
                                   "terceiro esta entrando cru no innerHTML")


class SharedStyleSheetTests(unittest.TestCase):
    """O que as duas paginas tem em comum mora em um lugar so.

    O `shared.css` existe porque os dois css cresciam em paralelo: um ajuste
    de contraste no index era um ajuste que o scrap nao recebia, e a
    divergencia so aparecia quando alguem abria as duas paginas lado a lado.
    Estes testes travam as tres propriedades que fazem a extracao valer — e,
    igualmente importante, as regras que NAO podem subir.
    """

    def css(self, nome: str) -> str:
        return (server.WEB_DIR / nome).read_text(encoding="utf-8")

    def _seletores(self, texto: str) -> set:
        """Os seletores de nivel superior, um por bloco INTEIRO.

        O agrupamento e o que importa: `a:focus-visible, button:focus-visible,
        input:focus-visible, select:focus-visible { ... }` e UM bloco com
        quatro seletores. Quebra-los e comparar um a um acusaria `input` e
        `select` como duplicata no scrap — que e a mesma regra, o mesmo
        arquivo, o mesmo lugar. Foi o que a primeira versao fez.
        """
        limpo = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
        limpo = re.sub(r"@[a-z-]+[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", limpo)
        achados = set()
        for m in re.finditer(r"([^{}]+)\{", limpo):
            sel = m.group(1).strip()
            if not sel or sel.startswith("@") or sel.startswith("--"):
                continue
            achados.add(re.sub(r"\s+", " ", sel))
        return achados

    def declaracoes(self, texto: str, sel: str) -> dict:
        """Declaracoes de nivel superior, ignorando o que esta em `@media`.

        Ignorar a media query e o que torna este teste honesto. A
        `@media (max-width: 920px)` do index declara `.rail-dropdown
        { display: block }` e `.wrap { padding: 0 18px }`: sao ajustes de tela
        estreita, e NAO subiram — nao podem, porque o breakpoint e o da
        pagina. Sem o filtro, o teste acusaria duplicata onde a pagina esta
        certa, e o primeiro reflexo de quem o visse seria apagar a media query
        — quebrando o celular para deixar o teste passar.

        O que o filtro deixa passar e o que deve sobrar: regra de nivel
        superior, a unica categoria que a extracao moveu.
        """
        d = {}
        sem_com = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
        # As at-rules sao removidas inteiras, com o conteudo: o que sobrar
        # depois e so regra de nivel superior.
        sem_com = re.sub(r"@[a-z-]+[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", sem_com)
        for achado in re.finditer(rf"(?<![\w-]){re.escape(sel)}\s*\{{", sem_com):
            prof, j, i = 1, achado.end(), achado.end()
            while j < len(sem_com) and prof:
                if sem_com[j] == "{":
                    prof += 1
                elif sem_com[j] == "}":
                    prof -= 1
                j += 1
            for parte in sem_com[i:j - 1].split(";"):
                if ":" in parte:
                    k, v = parte.split(":", 1)
                    d[k.strip()] = re.sub(r"\s+", " ", v).strip()
        return d

    def tokens(self, folha: str) -> dict:
        d = {}
        for nome in ("shared.css", folha):
            d.update(self.declaracoes(self.css(nome), ":root"))
        return d

    def test_favicon_has_a_real_brand_mark(self):
        """O favicon nao e o play generico: a marca mora em `web/favicon.svg`.

        Desenho geometrico (clipe 9:16 renderizado, sem letra): o teste mede a
        semantica que distingue marca de placeholder — o retangulo do clipe e o
        triangulo do play juntos. Um `VC` em texto, uma letra so, ou um play
        avulso num retangulo chapado continuam sendo generico, e o teste recusa
        os tres pela mesma razao: marca e o que NAO se confunde com UI.
        """
        svg = (server.WEB_DIR / "favicon.svg").read_text(encoding="utf-8")
        self.assertIn("<svg", svg)
        self.assertNotIn(">VC<", svg, "a marca voltou a ser texto")
        # O clipe: retangulo 9:16 com cantos suaves. Nao e o retangulo externo
        # do favicon (esse e o fundo): o que importa e a proporcao do interno.
        internos = re.findall(r"<rect[^>]*>", svg)
        formas = "".join(internos)
        self.assertIn("rx=", formas, "o clipe nao tem cantos suaves")
        self.assertIn("<path", svg, "o play sumiu do favicon")

    def test_every_brand_mark_shows_the_icon_not_the_letters(self):
        """Todo `.brand-mark` carrega o SVG, e o texto `VC` sumiu das paginas.

        Sao DUAS marcas por pagina — a do rail e a do rail-brand ja e a mesma
        agora, mas o cabecalho de `index.html`/`scrap.html` NAO usa
        `.brand-mark`: ele tem o proprio icone (`.header-context-icon` /
        `.scrap-header-icon`). Por isso o total aqui e' o numero de
        `.brand-mark`, e o teste o CONTA em vez de fixar 2 — a contagem fixa
        de 4 envelheceu em silencio: o cabecalho mudou de marca e a assercao
        continuou exigindo a forma antiga. O que importa nao e' quantos sao,
        e' que nenhum voltou a ser texto.

        `ajustes.html` esteve FORA desta lista e da de favicon ate 2026-10-02,
        e era exatamente por isso que ela ainda mostrava `VC`: as outras duas
        foram migradas e a terceira ficou para tras sem nenhum teste notar.
        A lista agora cobre as tres paginas.
        """
        for pagina in ("index.html", "ajustes.html", "scrap.html"):
            with self.subTest(pagina=pagina):
                html = (server.WEB_DIR / pagina).read_text(encoding="utf-8")
                self.assertNotIn(">VC<", html, f"{pagina} traz o VC em texto")
                marks = re.findall(r'class="brand-mark"[^>]*>(.*?)</span>', html,
                                   re.S)
                self.assertTrue(marks, f"{pagina}: nenhuma marca encontrada")
                for mark in marks:
                    self.assertIn("<img", mark,
                                  f"{pagina}: uma marca nao tem imagem")
                    self.assertIn("/favicon.svg", mark,
                                  "a marca aponta para outro arquivo")
                    self.assertIn('alt=""', mark,
                                  "a imagem decorativa ganhou nome: o produto "
                                  "ja esta escrito no elemento vizinho")

    def test_the_shared_sheet_is_linked_first_by_both_pages(self):
        """As duas paginas carregam o compartilhado, e ele PRIMEIRO.

        Sem o link a pagina fica sem tokens nem rail inteiro, e nenhuma falha
        de sintaxe acusa isso — a pagina so fica feia. A ordem e testada
        porque inverter reintroduz o bug do rail no celular sem nenhum sinal
        de texto: com o compartilhado depois, ele sobrepoe a
        `@media (max-width: 920px)` da pagina. Medido: 100 de 156 elementos.
        """
        for pagina, propria in (("index.html", "index.css"),
                                ("scrap.html", "scrap.css")):
            with self.subTest(pagina=pagina):
                # O `href` e `/shared.css`, com barra, entao a lista guarda o
                # caminho inteiro e precisa do `rsplit`. E o filtro
                # `rel="stylesheet"`: os `preconnect` do Google tambem sao
                # `<link href>` e entravam na lista, fazendo `ordem[0]` ser um
                # deles. O `[^>]*` entre os dois atributos cobre qualquer
                # ordem — no HTML o `rel` vem antes do `href`.
                ordem = []
                for link in re.findall(r"<link[^>]*>", self.css(pagina)):
                    if 'rel="stylesheet"' not in link:
                        continue
                    # A query de cache-busting (`/shared.css?v=rail-brand-...`)
                    # sai aqui: sem isso nenhum href `.css` casa e o teste
                    # reprova um <link> que esta' na ordem certa.
                    m = re.search(r'href="([^"]+\.css)(?:\?[^"]*)?"', link)
                    if m:
                        ordem.append(m.group(1).rsplit("/", 1)[-1])
                # So as folhas LOCAIS: o `href` da fonte do Google tambem
                # termina em `.css` e entrava na lista, fazendo `ordem[0]` ser
                # a fonte e reprovar um link que estava certo.
                self.assertIn("shared.css", ordem,
                              f"{pagina} nao carrega o shared.css")
                self.assertEqual(ordem[0], "shared.css",
                                 f"{pagina} carrega o compartilhado depois do "
                                 "css dela: a media query que esconde o rail "
                                 "passa a perder para o .rail do compartilhado")
                self.assertLess(ordem.index("shared.css"),
                                ordem.index(propria))

    def test_the_shared_sheet_carries_one_set_of_tokens_for_both_pages(self):
        """Os 55 tokens estao no compartilhado, e as duas paginas veem os mesmos.

        E o teste que fecha a divergencia: se alguem ajustar `--text-ink-subtle`
        no css do scrap, os dois conjuntos param de bater e aqui acusa. O
        contraste em si e o `ContrastTokensTests`, que calcula o numero; este
        so exige que as DUAS paginas vejam o mesmo.
        """
        ti, ts = self.tokens("index.css"), self.tokens("scrap.css")
        self.assertGreaterEqual(len(ti), 55,
                                f"o compartilhado perdeu tokens: {len(ti)}")
        self.assertEqual(ti, ts,
                         "os tokens divergem entre as paginas — e o que o "
                         "arquivo compartilhado existia para impedir")

    def test_no_token_definition_stayed_behind_in_the_pages(self):
        """A definicao do token nao pode ter ficado tambem no css da pagina.

        A duplicata silenciosa e o modo classico de essa refatoracao dar errado:
        o arquivo encolhe, o diff parece limpo, e na verdade a segunda copia e
        que manda — ate alguem editar a primeira e nada mudar.
        """
        for folha in ("index.css", "scrap.css"):
            with self.subTest(folha=folha):
                restantes = self.declaracoes(self.css(folha), ":root")
                for tok in ("--rail-w", "--accent-primary", "--text-ink-subtle",
                            "--bg-deep", "--transition"):
                    self.assertNotIn(
                        tok, restantes,
                        f"{tok} foi definido no {folha} e no compartilhado: "
                        "duas verdades, e a da pagina manda")

    def test_the_shared_rules_are_not_duplicated_in_the_pages(self):
        """O que subiu nao pode ter ficado tambem no css da pagina.

        O seletor do rail e o mais tentador, por ser o bloco mais longo.
        """
        for sel in (".rail-item", ".rail-dropdown", ".menu-btn", ".sr-only",
                    ".chip strong", ".wrap", "header"):
            with self.subTest(seletor=sel):
                self.assertTrue(self.declaracoes(self.css("shared.css"), sel),
                                f"{sel} deveria ter subido")
                for folha in ("index.css", "scrap.css"):
                    self.assertEqual(
                        self.declaracoes(self.css(folha), sel), {},
                        f"{sel} esta no {folha} e no compartilhado: duas "
                        "verdades, e a da pagina manda")

    def test_the_page_only_overflow_travel_stays_in_the_index(self):
        """`html` e `body` sobrem — a parte comum — e o index guarda o `clip`.

        O `overflow-x: clip` e a trava de overflow horizontal do index; o
        scrap nao tem a linha. Perder o `clip` seria regressao silenciosa: a
        suite continua verde porque nenhum teste mede layout renderizado.

        A trava esta num seletor AGRUPADO, `html, body { overflow-x: hidden;
        overflow-x: clip; }`, e nao em `html` sozinho. Procurar so pelo `html`
        nao acha nada e reprova um arquivo que esta certo — foi o que a
        primeira versao fez.
        """
        comum = self.declaracoes(self.css("shared.css"), "html")
        self.assertIn("scroll-behavior", comum,
                      "a parte comum de `html` deveria ter subido")
        for folha, esperado in (("index.css", True), ("scrap.css", False)):
            with self.subTest(folha=folha):
                tem_clip = ("overflow-x" in self.declaracoes(self.css(folha), "html")
                            or "overflow-x" in
                            self.declaracoes(self.css(folha), "html, body"))
                self.assertEqual(tem_clip, esperado,
                                 f"{folha}: o overflow-x deveria "
                                 f"{'ficar' if esperado else 'nao existir'} ali")

    def test_no_selector_is_declared_on_both_sides(self):
        """Nenhum seletor pode estar no compartilhado E no css da pagina.

        Esta e a regra geral, e as outras sao casos dela. Vale mais do que
        parece: a duplicata silenciosa e o modo classico de essa refatoracao
        dar errado — o arquivo encolhe, o diff parece limpo, e na verdade a
        segunda copia e que manda, ate alguem editar a primeira e nada mudar.

        A lista e montada a partir do que o compartilhado realmente tem, e nao
        de uma lista escrita a mao: um teste que fixa nomes so pega o que
        alguem pensou em verificar.
        """
        compartilhado = self.css("shared.css")
        no_compartilhado = self._seletores(compartilhado)
        self.assertTrue(no_compartilhado, "o compartilhado nao declara nada")
        for folha in ("index.css", "scrap.css"):
            na_pagina = self._seletores(self.css(folha))
            with self.subTest(folha=folha):
                for sel in sorted(no_compartilhado & na_pagina):
                    # `html` e `body` sao a excecao declarada: subiram a parte
                    # comum e o index guardou o `overflow-x: clip`. Sao o mesmo
                    # seletor nos dois arquivos por desenho, e o teste do
                    # `clip` e que cobre o caso.
                    if sel in ("html", "body"):
                        continue
                    self.fail(
                        f"{sel} esta no {folha} e no compartilhado: duas "
                        "verdades, e a da pagina manda")


class ContrastTokensTests(unittest.TestCase):
    """O contraste dos tokens de texto, calculado e não lembrado.

    O contraste é uma propriedade do PAR cor de fundo + cor de texto, e nenhuma
    das duas sozinha avisa quando falha. O token ``--text-ink-subtle`` é a cor
    das dicas de campo — texto normal em corpo pequeno — e a WCAG exige 4,5:1
    para ele. Com ``#6b7280`` ele dava 3,71:1 sobre ``--bg-surface``: a dica de
    um campo ficava legível no monitor e apagava no celular.

    A fórmula entra no teste de propósito. Um ``assertIn("4.5", ...)`` na folha
    passaria com o número num comentário; o que trava é a conta — canais
    linearizados, soma ponderada 0,2126/0,7152/0,0722, e a razão
    (claro + 0,05) / (escuro + 0,05).
    """

    #: Piso da WCAG para texto normal. Texto grande (18,66px bold ou 24px) tem
    #: 3:1, e nenhuma dica aqui e desse tamanho.
    PISO = 4.5

    #: Os dois fundos onde o degrau `subtle` aparece. O `surface` e mais claro e
    #: por isso e o piso de verdade: um tom que passa nele passa no `deep`.
    FUNDOS = ("--bg-deep", "--bg-surface")

    PAGES = ("index.css", "scrap.css")

    @staticmethod
    def _lum(hexa: str) -> float:
        h = hexa.lstrip("#")
        r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def _contraste(self, a: str, b: str) -> float:
        l1, l2 = sorted([self._lum(a), self._lum(b)], reverse=True)
        return (l1 + 0.05) / (l2 + 0.05)

    def _tokens(self, folha: str) -> dict:
        """Os tokens de uma pagina: o compartilhado MAIS o css proprio.

        A partir da extracao, nenhum token esta no css da pagina — eles
        subiram para o `shared.css`, linkado antes dela. Ler so a folha da
        pagina devolvia um dicionario vazio e os tres testes de contraste
        caíam com `AttributeError` em vez de reprovar por contraste: uma falha
        de instrumentacao, que e pior que nenhuma, porque parece que o
        contraste foi verificado e nao foi.

        A uniao importa tambem: o `:root` vem em tres blocos (tokens, apelidos
        de papel e o `--rail-w` que fica a parte), e o `re.search` singular
        pegava so o primeiro.
        """
        CSS = server.WEB_DIR
        d = {}
        for nome in ("shared.css", folha):
            caminho = CSS / nome
            if not caminho.exists():
                continue
            css = caminho.read_text(encoding="utf-8")
            for bloco in re.findall(r":root\s*\{(.*?)\n\}", css, re.S):
                d.update(re.findall(r"(--[\w-]+):\s*([^;]+);", bloco))
        self.assertTrue(d, f"nenhum token encontrado em {folha}")
        return d

    def test_the_subtle_step_clears_the_wcag_floor_on_both_backgrounds(self):
        for folha in self.PAGES:
            t = self._tokens(folha)
            subtle = t["--text-ink-subtle"].strip()
            with self.subTest(folha=folha):
                for fundo in self.FUNDOS:
                    razao = self._contraste(subtle, t[fundo].strip())
                    self.assertGreaterEqual(
                        razao, self.PISO,
                        f"{folha}: {subtle} sobre {fundo} da {razao:.2f}:1 — "
                        f"abaixo de {self.PISO}:1 para texto normal")

    def test_the_three_steps_stay_three_steps(self):
        """Subir o degrau não pode apagar a hierarquia.

        O degrau `subtle` existe para ser o mais apagado dos três. Passar de
        `--text-ink-subtle` para `--text-ink-muted` cumpriria o contraste e
        destruiria o que o token significa: sem degrau intermediário, o olho
        para de distinguir texto de apoio de texto de apoio mais fraco.
        """
        for folha in self.PAGES:
            t = self._tokens(folha)
            with self.subTest(folha=folha):
                ink = self._lum(t["--text-ink"].strip())
                muted = self._lum(t["--text-ink-muted"].strip())
                subtle = self._lum(t["--text-ink-subtle"].strip())
                self.assertGreater(ink, muted, f"{folha}: ink tem de ser o mais claro")
                self.assertGreater(
                    muted, subtle, f"{folha}: muted tem de ficar acima de "
                                   "subtle — os dois viraram a mesma cor")
                # E o degrau não pode ser tão sutil que suma: um intervalo
                # minimo entre os dois degraus e o que mantem a diferença
                # visível a distância de leitura normal.
                self.assertGreaterEqual(
                    muted - subtle, 0.05,
                    f"{folha}: a distancia entre muted e subtle e "
                    f"{muted - subtle:.3f} de luminancia — pequena demais para "
                    "a diferenca aparecer na tela")

    def test_the_hints_take_the_scale_not_a_literal_size(self):
        """A dica de campo usa a escala, e a escala é a fonte da verdade.

        `.field .hint` tinha `font-size: 0.78rem` em literal, enquanto a página
        vizinha tirava o mesmo texto de `--text-caption`. Um literal não segue a
        escala: na próxima vez que o degrau subisse, o index ficaria para trás
        e ninguém notaria, porque as duas folhas continuariam compilando.

        A prova olha a DECLARAÇÃO, e nao o corpo da regra. A primeira versão
        testava `assertIn("var(--text-", corpo)` e passava com o literal no
        lugar: `color: var(--text-muted)` estava na mesma regra e satisfazia a
        busca. Um teste que passa pelo token vizinho e um teste que nao testa.
        """
        # O `font-size` com a DECLARACAO inteira, para casar a linha e nao a
        # regra. Aceita o espaco antes do dois-pontos, que e o que o formatador
        # pode deixar.
        tamanho = re.compile(r"font-size\s*:\s*([^;}]+)")
        for folha in self.PAGES:
            css = (server.WEB_DIR / folha).read_text(encoding="utf-8")
            with self.subTest(folha=folha):
                for regra in (".field .hint", ".field-hint", ".auth-note"):
                    for m in re.finditer(re.escape(regra) + r"\s*\{([^}]*)\}", css):
                        corpo = m.group(1)
                        achado = tamanho.search(corpo)
                        if not achado:
                            continue  # a regra nao mexe no corpo
                        valor = achado.group(1).strip()
                        self.assertIn(
                            "var(--text-", valor,
                            f"{folha}: {regra} escreve font-size em literal "
                            f"({valor}) em vez de usar a escala")

    def test_the_caption_step_is_big_enough_for_a_field_hint(self):
        """`--text-caption` é o corpo da dica, e dica não pode ser miúda.

        A base de texto do projeto é 14px (é o que ``--text-body: 0.875rem``
        declara), então ``0,78rem`` dava **10,9px** e ``0,8rem`` dá **11,2px**.
        O piso de 12px que eu escrevi aqui era do raciocínio "0,86rem" e não
        deste projeto: com base 14 ele exigiria 0,857rem, e essa é a nota que os
        próprios presets de letra do Windows dão como pequena. O degrau subiu
        9% e continua ABAIXO de 12px — o que é uma escolha de densidade, não um
        piso de legibilidade, e é o que o teste documenta.

        O que este teste trava é o que era o defeito: o degrau não pode voltar
        para 0,78rem. A referência é a BASE do degrau abaixo (`body-sm`, 0,84375),
        não um número absoluto: a dica é menor que o corpo de texto por
        desenho, e o que não pode e o degrau sumir de novo.
        """
        for folha in self.PAGES:
            t = self._tokens(folha)
            rem = float(t["--text-caption"].strip().removesuffix("rem"))
            body_sm = float(t["--text-body-sm"].strip().removesuffix("rem"))
            with self.subTest(folha=folha):
                # Não volta ao 0,78125rem antigo, e não se aproxima do degrau
                # seguinte: a dica é um degrau, não um parágrafo miúdo.
                self.assertGreaterEqual(
                    rem, 0.8,
                    f"{folha}: --text-caption = {rem}rem; o degrau voltou para "
                    "baixo de 0,8rem — a dica de campo some no celular")
                self.assertLess(
                    rem, body_sm,
                    f"{folha}: --text-caption = {rem}rem chegou no degrau "
                    f"body-sm ({body_sm}rem) — a dica deixou de ser apoio e "
                    "virou texto corrente")
                px = rem * 14
                self.assertGreaterEqual(
                    px, 11,
                    f"{folha}: --text-caption = {rem}rem = {px:.1f}px com base "
                    "14px — abaixo de 11px nenhuma dica se le no celular")


class FieldDescriptionTests(unittest.TestCase):
    """``aria-describedby`` ligando cada campo à dica que já está na tela.

    As páginas tinham 31 ``aria-label`` e nenhum ``aria-describedby``: o leitor
    de tela batia o nome do controle e parava. A dica — que é onde está a
    informação que o motor faz, e não só o rótulo — existia como texto ao lado
    do campo e nunca chegava a quem usa leitor de tela. O conteúdo já estava no
    DOM; faltava a referência.

    A verificação é sobre o PAR, não sobre o atributo: ``aria-describedby`` só
    funciona se o id apontar para um elemento que existe E tem texto. Um id
    errado é o pior caso — falha em silêncio, sem erro no console, e o campo
    volta a não ter descrição sem ninguém perceber.
    """

    PAGES = ("index.html", "scrap.html")

    def body(self, name: str) -> str:
        return (server.WEB_DIR / name).read_text(encoding="utf-8")

    def _refs(self, markup: str) -> list[str]:
        return re.findall(r'aria-describedby="([^"]+)"', markup)

    def test_every_description_resolves_to_an_element_with_text(self):
        """Toda referência acha um alvo, e o alvo não é vazio."""
        for pagina in self.PAGES:
            markup = self.body(pagina)
            with self.subTest(pagina=pagina):
                refs = self._refs(markup)
                self.assertTrue(refs, f"{pagina}: nenhum campo descreve a si mesmo")
                for ref in refs:
                    # O alvo precisa existir…
                    alvo = re.search(
                        r'id="' + re.escape(ref) + r'"[^>]*>(.*?)</', markup, re.S)
                    self.assertIsNotNone(
                        alvo, f"{pagina}: aria-describedby aponta para "
                              f"'{ref}', que nao existe no arquivo")
                    # …e precisa ter TEXTO. Um alvo vazio e um atributo que
                    # nao descreve nada: a tela mostra a dica, o leitor de tela
                    # le um silencio.
                    texto = re.sub(r"<[^>]+>", "", alvo.group(1)).strip()
                    self.assertTrue(
                        texto, f"{pagina}: a descricao '{ref}' esta vazia")

    def test_no_description_orphan(self):
        """Nenhuma dica ganha id sem um controle que a referencie.

        O outro lado do contrato: uma dica com id e sem dono não atrapalha quem
        vê (o texto está ali do mesmo jeito), mas sinaliza que a ligação foi
        pela metade — alguém deu id na dica e esqueceu do campo. A classe de
        elemento da dica é o que distingue "texto de ajuda" de "parágrafo".
        """
        for pagina in self.PAGES:
            markup = self.body(pagina)
            with self.subTest(pagina=pagina):
                # As classes que SAO dica de campo. Um <p class="card-sub"> no
                # topo de um card explica a secao inteira, e nao descreve um
                # controle: referencia-lo seria errado, nao apenas inutil.
                dicas = set()
                for classe in ("hint", "auth-note", "field-hint"):
                    dicas |= set(re.findall(
                        r'class="' + classe + r'" id="([^"]+)"', markup))
                orfas = sorted(dicas - set(self._refs(markup)))
                self.assertFalse(
                    orfas, f"{pagina}: dica com id e sem dono: {orfas}")

    def test_the_description_does_not_replace_the_label(self):
        """A dica descreve; o rótulo continua nomeando.

        Quando ``aria-describedby`` aponta para algo, ele passa a ser lido
        DEPOIS do nome. Se a dica for a única fonte do nome, o controle chega ao
        leitor de tela como "Uma URL por execução…", sem dizer que é a URL.

        A prova é sobre o ALVO: nenhum controle pode estar com
        ``aria-labelledby`` apontando para a própria descrição. A presença do
        atributo não é o defeito — o radiogroup de cookies do scrap usa
        ``aria-labelledby`` para o nome e ``aria-describedby`` para a dica, e
        está certo. O defeito seria os dois apontando para o mesmo id, com o
        rótulo visual do campo deixado de fora.
        """
        for pagina in self.PAGES:
            markup = self.body(pagina)
            with self.subTest(pagina=pagina):
                for m in re.finditer(r'<(\w+)\b([^>]*)>', markup):
                    tag, atributos = m.group(1), m.group(2)
                    desc = re.search(r'aria-describedby="([^"]+)"', atributos)
                    if not desc:
                        continue
                    nomeado = re.search(r'aria-labelledby="([^"]+)"', atributos)
                    if nomeado:
                        self.assertNotEqual(
                            nomeado.group(1), desc.group(1),
                            f"{pagina}: <{tag}> usa o MESMO id para o nome e "
                            "para a descrição — o rótulo do campo sairia de cena")
                # E o `sr-only` do scrap: o campo de URL é lido pelo label
                # escondido, e a dica entra DEPOIS dele. Sem o rótulo, o
                # controle leria só a dica.
                self.assertIn(
                    '<label class="sr-only" for="scrap-url">', self.body("scrap.html"),
                    "o campo de URL do scrap perdeu o rotulo sr-only")

    def test_the_descriptions_are_the_real_hints(self):
        """A referência aponta para a dica, e a dica não é outro campo.

        Testar "o atributo existe" passaria com um id em qualquer lugar do
        arquivo. O que precisa valer e o que faz a ligação valer:

        * o alvo **não** é o id de outro CONTROLE — senão o leitor de tela lê o
          rótulo de um campo como se fosse a descrição deste, que é pior do
          que não ter descrição;
        * o alvo **é** um elemento de dica, e não um ``<label>``, um ``<h2>`` ou
          um parágrafo de seção. Um ``<p class="card-sub">`` no topo de um card
          explica o card inteiro; apontá-lo aqui descreveria o campo com o texto
          do card.

        O nome do id NÃO precisa seguir o nome do campo, e forçar isso seria
        errado: ``cookies-from-browser`` é descrito por ``cookies-browser-hint``
        (o nome completo é longo demais para o id) e ``scrap-url`` pela dica do
        MODO, que o ``scrap.js`` reescreve a cada troca de aba. A regra do
        prefixo reprovaria os dois, e era o teste errado, não o HTML.
        """
        # As classes que SAO dica de campo. Um <p class="card-sub"> no topo de
        # um card explica a secao inteira, e nao descreve um controle:
        # referencia-lo seria errado, nao apenas inutil.
        dica = re.compile(
            r'class="(?:hint|auth-note|field-hint)"[^>]*\bid="([\w-]+)"')
        for pagina in self.PAGES:
            markup = self.body(pagina)
            ids_de_campo = set(re.findall(
                r'<(?:input|select|textarea|button)\b[^>]*\bid="([\w-]+)"', markup))
            ids_de_dica = set(dica.findall(markup))
            with self.subTest(pagina=pagina):
                for ref in self._refs(markup):
                    self.assertNotIn(
                        ref, ids_de_campo,
                        f"{pagina}: a descricao '{ref}' e o id de outro "
                        "controle — o leitor de tela leria o rotulo dele")
                    self.assertIn(
                        ref, ids_de_dica,
                        f"{pagina}: a descricao '{ref}' nao aponta para um "
                        "elemento de dica")


class RailNavigationTests(unittest.TestCase):
    """O rail: uma lista, tres paginas, dois lugares por pagina.

    A lista de destinos era escrita a mao SEIS vezes (rail fixo + menu do header,
    em cada uma das tres paginas) e a marcacao da pagina atual mais TRES vezes,
    uma por arquivo. As copias divergiram de verdade: o mapa de paginas do
    index.js e do scrap.js nao conhecia /ajustes, e a normalizacao do caminho
    era oposta nos dois grupos (um acrescentava barra final, o outro tirava).
    Antes da unificacao o proprio `index.js` chamava /scrap de "Scrap" enquanto o
    `scrap.js` e as duas paginas ja o chamavam de "Biblioteca" -- tres nomes para
    o mesmo destino, e o `document.title` divergia entre as paginas por isso.

    Agora a fonte unica e ``RAIL_PAGES`` em ``web/comum.js``, e cada pagina traz
    apenas dois ``<ul data-rail-list>`` vazios. O que estes testes travam:

    * a lista declarada uma vez so, com os destinos que existem;
    * nenhuma pagina carregando a lista ou o mapa de paginas de volta;
    * todo destino apontando para uma rota que o servidor realmente serve.
    """

    PAGES = ("index.html", "ajustes.html", "scrap.html")
    #: Os unicos destinos do rail. Tem de bater com RAIL_PAGES e com as rotas.
    DESTINATIONS = ("/", "/ajustes", "/scrap")
    #: Campos que cada entrada precisa para o item sair completo no render.
    FIELDS = ("path", "ico", "title", "desc")

    def markup(self, name: str) -> str:
        """A pagina sem <script>/<style>/comentarios."""
        text = (server.WEB_DIR / name).read_text(encoding="utf-8")
        text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
        return re.sub(r"<!--.*?-->", "", text, flags=re.S)

    def rail_source(self) -> str:
        return (server.WEB_DIR / "comum.js").read_text(encoding="utf-8")

    def rail_pages(self) -> list[dict]:
        """As entradas de RAIL_PAGES, lidas do proprio comum.js.

        Lidas do arquivo, e nao copiadas aqui: se a lista mudar la, o teste muda
        junto em vez de passar a verificar uma copia morta.
        """
        block = re.search(
            r"const RAIL_PAGES\s*=\s*\[(.*?)\n\s*\];", self.rail_source(), re.S
        )
        self.assertIsNotNone(block, "comum.js nao declara RAIL_PAGES")
        entries = []
        for raw in re.findall(r"\{([^{}]*)\}", block.group(1)):
            entries.append(dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", raw)))
        return entries

    def stylesheets(self, name: str) -> list[str]:
        """Os css linkados, sem a query de cache-busting.

        As paginas referenciam ``/shared.css?v=rail-brand-20261001``; sem tirar a
        query o teste tenta abrir um caminho que inclui ``?v=`` e leva OSError.
        """
        body = (server.WEB_DIR / name).read_text(encoding="utf-8")
        sheets = re.findall(r'<link rel="stylesheet" href="/([^"?]+)', body)
        return sheets

    def test_every_page_has_a_rail(self):
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertIn('class="rail"', markup)

    def test_every_rail_brand_link_is_clickable(self):
        """A marca e um alvo de navegacao, nao um rotulo com cara de link.

        `.rail-brand-link` tem hover e `:focus-visible` no shared.css. Em
        `ajustes.html` ela era um `<span>` sem `href`: parecia clicavel (muda no
        hover, o cursor, o anel de foco) e nao levava a lugar nenhum — o pior
        dos dois mundos, porque o usuario clica e nada acontece. Um controle
        que *parece* interativo e obrigado a ser.

        Um `<span>` aqui so se justificaria na propria pagina que ele aponta,
        e nao e o caso: o rail-brand leva ao Estudio, que existe.
        """
        for name in self.PAGES:
            markup = self.markup(name)
            brand = markup.split('class="rail-brand"', 1)[1].split("</div>", 1)[0]
            with self.subTest(page=name):
                self.assertIn("<a class=\"rail-brand-link\"", brand,
                              f"{name}: a marca do rail nao e um link")
                self.assertIn('href="/"', brand,
                              f"{name}: a marca do rail nao leva ao Estudio")

    def test_the_local_badge_stays_out_of_the_link(self):
        """O selo LOCAL e rotulo, nao destino: fora do `<a>`.

        Dentro, ele viraria parte da area clicavel e o leitor de tela o
        anunciaria como o nome do link — "LOCAL" como destino nao quer dizer
        nada.
        """
        for name in self.PAGES:
            markup = self.markup(name)
            brand = markup.split('class="rail-brand"', 1)[1].split("</div>", 1)[0]
            with self.subTest(page=name):
                link = brand.split("</a>", 1)[0]
                self.assertNotIn("rail-brand-badge", link,
                                 f"{name}: o selo LOCAL entrou no link da marca")

    def test_every_page_has_the_two_list_containers(self):
        """Um container no rail fixo, um no menu do header. Nada mais."""
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertEqual(
                    markup.count("data-rail-list"),
                    2,
                    f"{name}: esperava 2 <ul data-rail-list> (rail + menu do header)",
                )

    def test_no_page_carries_the_list_in_markup(self):
        """A lista nao pode voltar para o HTML.

        E' a regressao exata que este passo corrigiu: duas copias escritas a mao
        que ninguem lembra de atualizar juntas.
        """
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertNotIn("data-rail-page", markup)
                self.assertNotIn("rail-item", markup)

    def test_one_container_is_the_rail_and_the_other_is_the_header_menu(self):
        for name in self.PAGES:
            markup = self.markup(name)
            rail = markup.split("</nav>", 1)[0]
            fallback = markup.split("data-rail-picker", 1)[1].split("</header>", 1)[0]
            with self.subTest(page=name):
                self.assertEqual(rail.count("data-rail-list"), 1, "rail fixo")
                self.assertEqual(fallback.count("data-rail-list"), 1, "menu do header")

    def test_the_rail_lists_destinations_as_plain_links(self):
        """O rail e' lista sempre visivel; so o menu do header abre e fecha.

        O rail ja foi um botao que abria um listbox. Se alguem reintroduzir
        semantica de popup aqui, ela contradiz o modelo "sempre visivel".
        """
        for name in self.PAGES:
            markup = self.markup(name)
            rail = markup.split("</nav>", 1)[0]
            with self.subTest(page=name):
                self.assertNotIn('role="listbox"', rail)
                self.assertNotIn('role="option"', rail)
                self.assertNotIn("aria-haspopup", rail)

    def test_the_header_fallback_is_a_toggle_with_aria(self):
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertIn('class="menu-btn', markup)
                self.assertIn('aria-haspopup="true"', markup)
                self.assertIn('aria-expanded="false"', markup)
                self.assertIn('aria-controls="rail-menu-sm"', markup)
                self.assertIn('id="rail-menu-sm"', markup)
                self.assertIn("data-rail-menu", markup)

    def test_every_destination_is_declared_in_the_js(self):
        """As entradas de RAIL_PAGES, e nao copias no HTML.

        Substitui o antigo `test_every_page_offers_every_destination`, que exigia
        `data-rail-page="..."` no markup -- justamente o que saiu de la.
        """
        pages = self.rail_pages()
        self.assertEqual(
            tuple(page.get("path") for page in pages),
            self.DESTINATIONS,
            "RAIL_PAGES nao lista exatamente os destinos esperados",
        )
        for page in pages:
            with self.subTest(path=page.get("path")):
                for field in self.FIELDS:
                    self.assertTrue(page.get(field), f"entrada sem {field}: {page}")

    def test_the_current_page_is_marked_by_the_js(self):
        """A marcacao mora no render, nao numa varredura do documento.

        Gerar o item ja marcado evita o instante em que a lista existe sem nenhum
        item marcado -- que e' o que a varredura pos-load produzia.
        """
        source = self.rail_source()
        self.assertIn('aria-current="page"', source)
        self.assertIn("function railItemHtml(", source)
        self.assertIn("function railKey(", source)

    def test_the_rail_marks_one_item_per_instance(self):
        """Um item marcado por instancia, e o mesmo HTML nas duas.

        As duas listas recebem o MESMO html (com o item da pagina ja marcado),
        entao nao ha como uma instancia render e a outra ficar sem marcacao --
        que era o bug de ter duas varreduras independentes.
        """
        source = self.rail_source()
        with self.subTest(instance="both"):
            self.assertIn("querySelectorAll('[data-rail-list]')", source)
            # Uma unica construcao do html, aplicada a todas as listas.
            self.assertIn("lists.forEach", source)

    def test_no_page_declares_its_own_destination_map(self):
        """A copia por pagina e' o que divergiu; nao pode voltar."""
        for name in self.PAGES:
            body = (server.WEB_DIR / name.replace(".html", ".js")).read_text(
                encoding="utf-8"
            )
            with self.subTest(page=name):
                self.assertNotIn("PAGES = {", body)
                self.assertNotIn("initRail", body)
                self.assertNotIn("data-rail-page", body)

    def test_comum_js_is_loaded_before_the_page_script(self):
        """O modulo compartilhado tem de vir primeiro.

        Os srcs carregam cache-busting (``/comum.js?v=studio-20261001``), entao a
        comparacao ignora a query: o que importa e' a ordem, nao o token.
        """
        for name in self.PAGES:
            body = (server.WEB_DIR / name).read_text(encoding="utf-8")
            own = "/" + name.replace(".html", ".js")
            srcs = re.findall(r'<script src="([^"]+)"', body)
            srcs = [src.split("?", 1)[0] for src in srcs]
            with self.subTest(page=name):
                self.assertIn("/comum.js", srcs, f"{name} nao carrega /comum.js")
                self.assertIn(own, srcs, f"{name} nao carrega {own}")
                self.assertLess(
                    srcs.index("/comum.js"),
                    srcs.index(own),
                    f"{name}: /comum.js tem de vir antes do script da pagina",
                )

    def test_every_destination_is_a_route_the_server_serves(self):
        """Destino de rail que o servidor nao serve e' link morto.

        As rotas sao `if` no corpo de do_GET, nao uma tabela, entao o teste le o
        codigo do proprio handler. E' o que pega o caso real: acrescentar a
        pagina em RAIL_PAGES e esquecer a rota.
        """
        import inspect

        source = inspect.getsource(server.Handler.do_GET)
        for path in self.DESTINATIONS:
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', source, f"do_GET nao serve {path}")

    def test_the_rail_has_a_narrow_screen_fallback(self):
        """Abaixo do breakpoint o rail some, e o menu do header tem de assumir."""
        for name in self.PAGES:
            body = (server.WEB_DIR / name).read_text(encoding="utf-8")
            sheets = self.stylesheets(name)
            with self.subTest(page=name):
                self.assertIn("rail-dropdown", body)
                self.assertTrue(sheets, f"{name} nao linka nenhum css")
                self.assertTrue(
                    any(
                        "max-width: 920px"
                        in (server.WEB_DIR / sheet).read_text(encoding="utf-8")
                        for sheet in sheets
                    ),
                    f"{name}: nenhum dos css ({sheets}) esconde o rail abaixo de 920px",
                )

    def test_the_old_static_template_link_is_gone(self):
        """O link do header foi substituido pelo rail; manter os dois seria
        duas entradas concorrentes, e a antiga sairia de sincronia."""
        self.assertNotIn(
            '<a class="btn pressable" href="/templates">', self.markup("index.html")
        )


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

        A second version paused whatever scrolled out of the viewport, to save
        CPU. It failed the same way and for the same reason, which is why both
        are forbidden by the same assertion: ``data-live="0"`` sets the video to
        ``opacity: 0``, so "paused" does not leave a still frame on screen — it
        leaves the SCHEMATIC. Anyone with Windows animations off
        (``MinAnimate=0``, which Chromium reads as ``prefers-reduced-motion:
        reduce``) saw three empty cards with a ▶ and no hint of why.

        ``video.play()`` is asserted on the same breath so the check cannot pass
        by the script being deleted: no pause AND a play, or the card is broken
        either way.
        """
        self.assertNotIn("video.pause()", self.page)
        self.assertIn("video.play()", self.page)
        # A terceira tentativa teria a mesma cara das duas primeiras: um
        # observador que decide parar. Ele nao aparece em lugar nenhum da
        # pagina, e voltar a aparecer e esta linha reprovando — o comentario
        # acima cita o termo, o mecanismo nao existe.
        self.assertNotIn("IntersectionObserver", self.page)

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


class IndexOverflowTests(unittest.TestCase):
    """A página inicial não tem rolagem horizontal: a trava é o contrato.

    Três fontes somavam a barra: painel `.select-panel` fechado medindo o
    conteúdo, filhos de grid/flex sem `min-width: 0` (o input empurra a
    coluna pela largura intrínseca) e qualquer resto cortado no root.
    """

    def setUp(self):
        self.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    def test_the_root_clips_horizontal_overflow(self):
        self.assertIn("overflow-x: clip", self.css,
                      "a trava de overflow sumiu do root")

    def test_grid_and_flex_children_can_shrink(self):
        for marker in (".main-grid > *", ".field input"):
            self.assertIn(marker, self.css, f"{marker} fora da trava")
        self.assertIn("min-width: 0", self.css)

    def test_the_closed_dropdown_panel_never_exceeds_the_viewport(self):
        self.assertIn("max-width: calc(100vw - 24px)", self.css,
                      "o painel fechado voltou a medir o conteúdo")

    def test_wide_content_is_contained_not_paged(self):
        self.assertIn("overflow-x: auto", self.css,
                      "a tabela de transcrição volta a vazar para a página")


class BrowseTests(unittest.TestCase):
    """GET /browse/native: o diálogo do SO escolhe a pasta de saída.

    O navegador esconde caminhos reais do JS, então nenhuma lista na página
    entrega uma pasta local ao servidor: o clique abre o diálogo nativo na
    máquina do servidor (tkinter) e o caminho cai direto no campo.
    """

    def test_the_output_field_has_the_picker_wired(self):
        page = page_source("index.html")
        for marker in ("btn-browse", "browseNative", "/browse/native",
                       ">Selecionar</button>"):
            self.assertIn(marker, page, f"{marker} fora da pagina")

    def test_the_native_dialog_returns_the_chosen_path(self):
        self.assertEqual(server._browse_native(ask=lambda: "C:\\Clips"),
                         {"path": "C:\\Clips"})

    def test_a_cancelled_dialog_is_not_an_error(self):
        self.assertEqual(server._browse_native(ask=lambda: ""), {"cancelled": True})

    def test_a_dialog_failure_is_an_error(self):
        def boom():
            raise ImportError("no tkinter")
        r = server._browse_native(ask=boom)
        self.assertIn("error", r)

    def test_without_tkinter_falls_back_to_win32(self):
        """ venvs sem tkinter usam o diálogo Win32 direto (sem install). """
        from unittest import mock

        with mock.patch.object(server, "_tk_askdirectory",
                               side_effect=ImportError("no tkinter")), \
             mock.patch.object(server, "_win32_askdirectory",
                               return_value="D:\\Clips") as win:
            self.assertEqual(server._native_askdirectory(), "D:\\Clips")
            win.assert_called_once_with()

    def test_win32_param_block_accepts_the_display_buffer(self):
        """O cast do buffer é o que quebrou o picker (incompatible types)."""
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        info = server._browse_info(buf, "teste")
        self.assertEqual(info.ulFlags, 0x41)
        self.assertTrue(info.lpfn, "sem callback o diálogo abre atrás do navegador")


class RunProgressTests(unittest.TestCase):
    """GET /run/progress: o record que o painel de Execucao pinta durante o job.

    O mesmo formato que `_download_record` e `_archive_record` ja tinham:
    chaves sempre presentes, porque a pagina le o JSON direto a cada segundo e
    uma chave ausente seria erro de renderizacao, e nao um payload menor.

    Nao roda o pipeline. O que esta em prova e o contrato — as quatro etapas,
    a cauda de linhas publicada a cada `_emit`, e o `/status` continuando a
    responder `{jobs, clips}` com o record novo morando em outro canto.
    """

    def setUp(self):
        self._antes = dict(server._state.get(server._RUN_SLOT) or {})

    def tearDown(self):
        with server._lock:
            if self._antes:
                server._state[server._RUN_SLOT] = self._antes
            else:
                server._state.pop(server._RUN_SLOT, None)

    def test_the_idle_record_has_every_key_and_no_lit_step(self):
        """Ocioso: 13 chaves, 4 etapas pendentes, nenhum erro e nenhuma url."""
        r = server._run_record()
        self.assertEqual(len(r), 13)
        self.assertEqual([s["state"] for s in r["stages"]], ["pendente"] * 4)
        self.assertEqual(r["error"], "")
        self.assertEqual(r["url"], "")

    def test_stages_come_from_the_same_tuple_the_pipeline_calls(self):
        """As etapas sao as `server._RUN_STAGES`: e de la que `_run_job` marca."""
        self.assertEqual([s["key"] for s in server._run_record()["stages"]],
                         [key for key, _ in server._RUN_STAGES])

    def test_marking_a_stage_closes_the_earlier_ones(self):
        server._mark_stage(1)
        with server._lock:
            estados = [s["state"] for s in
                       server._state[server._RUN_SLOT]["stages"]]
        self.assertEqual(estados, ["feito", "agora", "pendente", "pendente"])

    def test_the_run_logger_publishes_the_tail_on_every_line(self):
        """A linha aparece no record antes do fim — e so a cauda viaja."""
        logger = server.RunLogger()
        for i in range(server.RunLogger.TAIL + 50):
            logger.step(f"linha {i}")
        with server._lock:
            linhas = server._state[server._RUN_SLOT]["lines"]
        self.assertEqual(len(linhas), server.RunLogger.TAIL)
        self.assertTrue(linhas[-1].endswith(f"linha {server.RunLogger.TAIL + 49}"),
                        "a cauda tem que ser as ultimas linhas, nao as primeiras")
        # E a lista completa continua intacta para a resposta final do /run.
        self.assertEqual(len(logger.lines), server.RunLogger.TAIL + 50)

    def test_a_failed_stage_keeps_where_it_got_to(self):
        """Falha: a etapa quebrou recebe o X, e o resto conserva o que era."""
        self.assertEqual(
            [s["state"] for s in server._mark_failed(
                [{"key": "a", "label": "A", "state": "feito"},
                 {"key": "b", "label": "B", "state": "agora"},
                 {"key": "c", "label": "C", "state": "pendente"}])],
            ["feito", "erro", "pendente"])

    def test_the_run_record_stays_out_of_status(self):
        """/status continua respondendo {jobs, clips}: o slot novo mora a parte."""
        with server._lock:
            server._state[server._RUN_SLOT] = server._run_record(active=True)
            snapshot = dict(server._state)
        snapshot.pop(server._DOWNLOAD_SLOT, None)
        snapshot.pop(server._ARCHIVE_SLOT, None)
        snapshot.pop(server._RUN_SLOT, None)
        self.assertNotIn("run", snapshot)

    def test_the_page_ladder_matches_the_server_stages(self):
        """As chaves do HTML são as do `server._RUN_STAGES`: a tela não inventa fases."""
        html = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="step-list"', html)
        for key, label in server._RUN_STAGES:
            with self.subTest(stage=key):
                self.assertIn(key, html)
                self.assertIn(label, html)

    def test_the_front_follows_run_progress_once_per_second(self):
        """O JS abre o acompanhamento com a mesma cadência do scrap.

        O `setInterval(followRun, 1000)` é o ritmo: segue 1 Hz como o resto,
        sem serrar o servidor nem dormir na espera — a prova amarra o número
        no código para ninguém "suavizar" em silêncio.
        """
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        self.assertIn("api('/run/progress')", js)
        self.assertIn("setInterval(followRun, 1000)", js)
        self.assertIn("renderSteps(r.stages)", js)

    def test_the_front_paints_states_not_percentages(self):
        """A escada nunca desenha percentual: do `_RUN_STAGES` vem o vocabulário.

        Ela usa a mesma palavra do servidor — "agora", "feito", "pulado" — e
        zera na conclusão: o usuário lê fases, não distingue fração de etapa.
        """
        css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")
        for estado in ("agora", "feito", "pulado"):
            with self.subTest(estado=estado):
                self.assertIn(f'[data-state="{estado}"]', css)



class DocsTests(unittest.TestCase):
    """GET /docs: o README do repo, renderizado localmente.

    O botão "Ver documentação" abria o GitHub com `window.open` — o endereço
    dá 404 (repo privado ou renomeado), então o único atalho de ajuda do
    produto levava a nada. Servir o próprio README tira a ajuda da
    dependência externa: funciona offline, com o texto que corresponde à
    versão instalada.
    """

    def test_the_docs_button_points_at_the_local_page(self):
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        self.assertIn("'/docs'", js)
        self.assertNotIn("github.com/roberto-sena89", js,
                         "o botão voltou a depender do GitHub")

    def test_the_renderer_covers_the_readme_surface(self):
        md = ("# Título\n\nTexto com **negrito** e `codigo`.\n\n"
              "- item um\n- item dois\n\n"
              "1. passo\n\n"
              "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
              "```powershell\npython -m viralclipper\n```\n")
        html_out = server._md_to_html(md)
        for marker in ("<h1>Título</h1>", "<strong>negrito</strong>",
                       "<code>codigo</code>", "<ul>", "<li>item um</li>",
                       "<ol>", "<li>passo</li>", "<th>A</th>", "<td>2</td>",
                       "<pre><code>python -m viralclipper</code></pre>"):
            self.assertIn(marker, html_out)
        self.assertNotIn("| --- |", html_out, "o separador da tabela vazou como texto")

    def test_readme_text_cannot_become_markup(self):
        html_out = server._md_to_html("Um `<script>` no texto\n")
        self.assertNotIn("<script>", html_out)

    def test_the_real_readme_renders_end_to_end(self):
        md = (server.REPO_ROOT / "README.md").read_text(encoding="utf-8")
        page = server._docs_page(md)
        self.assertIn("<title>Documentação · Viral Clipper</title>", page)
        self.assertIn('href="/"', page, "a barra de volta ao painel sumiu")
        for marker in ("<h1", "<h2", "<table>", "<pre><code"):
            self.assertIn(marker, page)


class CacheTests(unittest.TestCase):
    """Cache-Control + ETag nas respostas.

    Nenhum response nunca carregou validador: a cada F5 o navegador baixava
    tudo de novo — CSS, JS e os ~10 MB de vídeo do hero. ETag por mtime+size
    permite 304 sem hashear vídeo grande a cada request.
    """

    class _Stub:
        def __init__(self, headers=None):
            self.headers = headers or {}
            self.codes, self.hdrs = [], []
            self.wfile = io.BytesIO()

        def send_response(self, code):
            self.codes.append(code)

        def send_header(self, key, value):
            self.hdrs.append((key, value))

        def end_headers(self):
            pass

        def header(self, key):
            return dict(self.hdrs).get(key)

    def test_revalidation_with_matching_etag_answers_304(self):
        stub = self._Stub({"If-None-Match": '"abc-1"'})
        server.Handler._send_file(stub, b"x", "text/plain", etag='"abc-1"')
        self.assertEqual(stub.codes, [304])
        self.assertEqual(stub.header("ETag"), '"abc-1"')

    def test_assets_carry_cache_and_etag(self):
        stub = self._Stub()
        server.Handler._send_file(stub, b"payload", "text/css",
                                   cache=server.Handler._CACHE_ASSET,
                                   etag='"m-s"')
        self.assertEqual(stub.codes, [200])
        self.assertEqual(stub.header("Cache-Control"), "public, max-age=300")
        self.assertEqual(stub.header("ETag"), '"m-s"')

    def test_json_state_is_never_cacheable(self):
        stub = self._Stub()
        server.Handler._send_json(stub, {"jobs": []})
        self.assertEqual(stub.header("Cache-Control"), "no-store")

    def test_etag_changes_with_the_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"one")
            path = Path(fh.name)
        try:
            first = server.Handler._etag_for(path)
            path.write_bytes(b"two two")
            self.assertNotEqual(first, server.Handler._etag_for(path))
        finally:
            path.unlink()


class FirstVisitTests(unittest.TestCase):
    """O primeiro minuto de quem nunca viu o produto.

    Cada teste daqui trava um ponto em que o visitante de primeira viagem
    ficava sem resposta: onde esta o campo que importa, o que a espera longa
    esta fazendo, para onde ir do rodape, e o que aparece primeiro na tela
    estreita. Nao sao detalhes de estilo — sao as perguntas que decidem se a
    pessoa chega a gerar um clip.
    """

    def html(self, nome: str = "index.html") -> str:
        return (server.WEB_DIR / nome).read_text(encoding="utf-8")

    def css(self, nome: str = "index.css") -> str:
        return (server.WEB_DIR / nome).read_text(encoding="utf-8")

    def test_the_url_hint_says_it_is_the_only_required_field(self):
        """A dica da URL responde "quanto eu preciso preencher?".

        Sem isso o formulario de 29 campos nao diz onde ele termina para quem
        so quer o caminho curto, e a pessoa assume que precisa de todos.
        """
        html = self.html()
        self.assertIn("único campo obrigatório", html)
        self.assertIn('id="url-hint"', html)

    def test_the_hero_promises_the_short_path(self):
        """O lead diz que so o endereco e obrigatorio, antes do formulario."""
        html = self.html()
        trecho = html[html.index('class="lead"'):html.index('id="hero-cta"')]
        self.assertIn("Só", trecho)
        self.assertIn("padrão", trecho)

    def test_the_cookies_warning_is_not_the_first_thing_read(self):
        """O aviso de "sempre falha" mora dentro de um disclosure, e aberto.

        Aberto porque o conteudo e curto e escondido vira o detalhe que a
        pessoa precisava ter visto; dentro de um disclosure porque ele e
        condicional — a maioria dos videos publicos nao passa por ali. As
        duas coisas juntas: visivel, e claramente opcional.
        """
        html = self.html()
        inicio = html.index('class="cookies-box"')
        bloco = html[inicio:html.index("</details>", inicio)]
        self.assertIn(" open>", html[inicio - 40:inicio + 60])
        self.assertIn("Acessar vídeo restrito (opcional)", bloco)
        # E o resumo explica que da para ignorar, antes de falar de falha.
        self.assertLess(bloco.index("passa sem isto"), bloco.index("sempre falha"))

    def test_the_cookies_summary_is_described_by_its_hint(self):
        """O resumo tem nome e descricao: leitor de tela le as duas."""
        html = self.html()
        self.assertIn('id="cookies-summary"', html)
        self.assertIn('aria-describedby="cookies-summary-hint"', html)

    def test_the_transcript_card_moved_to_ajustes(self):
        """O card da transcricao vive em Ajustes, e nao mais em Cortes.

        A garantia continua a mesma -- o card diz o que acontece se ficar em
        branco -- mas ela agora e verificada na pagina que tem o card. Deixar a
        assercao aqui passaria a travar uma ausencia: o texto nao esta mais em
        index.html, e um teste que exige o contrario so pode falhar.
        """
        html = self.html()
        self.assertNotIn("Transcrição (só se já tiver uma)", html)

        ajustes = (server.WEB_DIR / "ajustes.html").read_text(encoding="utf-8")
        # As duas metades da mesma promessa: da para pular, e o que acontece
        # quando se pula.
        self.assertIn("o próprio site transcreve", ajustes)
        self.assertIn("Só cole aqui se você já tem o texto pronto", ajustes)

    def test_the_empty_states_tell_the_next_step(self):
        """Estado vazio aponta o proximo passo, e nao um log que nao existe."""
        html = self.html()
        self.assertIn("Cole a URL acima", html)
        # A galeria nao pode mandar olhar um log que ainda nao foi escrito.
        self.assertNotIn("Verifique o log acima", html)

    def test_the_footer_has_an_exit(self):
        """O rodape oferece os mesmos destinos do rail.

        Era o unico lugar da pagina sem saida. Importa sobretudo no celular,
        onde o rail vira um hamburguer e o Scrap fica a dois toques.
        """
        html = self.html()
        self.assertIn('class="footer-nav"', html)
        for alvo in ("btn-docs-foot", "btn-rail-cortes", "btn-rail-scrap"):
            with self.subTest(alvo=alvo):
                self.assertIn(f'id="{alvo}"', html)
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        self.assertIn("liga('btn-rail-scrap'", js)
        self.assertIn("liga('btn-docs-foot'", js)

    def test_the_headline_comes_before_the_decorative_video_on_mobile(self):
        """No celular o H1 vem primeiro, e nao os videos decorativos.

        `order: -1` empurrava o `.hero-visual` (que e `aria-hidden`, ou seja,
        nada para leitor de tela) para cima do titulo. A primeira dobra virava
        vitrine sem frase, e a unica linha que diz o que o site faz saia da
        tela.
        """
        css = self.css()
        inicio = css.index("@media (max-width: 960px)")
        # O bloco vai ate a chave que FECHA a media query, e nao ate a primeira
        # que aparece: `@media (...) {` abre, e cada seletor dentro dela abre e
        # fecha a sua. Cortar na primeira fecharia o `.main-grid` e leria so
        # tres linhas do bloco.
        profundidade, fim = 0, inicio
        for fim in range(css.index("{", inicio), len(css)):
            if css[fim] == "{":
                profundidade += 1
            elif css[fim] == "}":
                profundidade -= 1
                if profundidade == 0:
                    break
        bloco = css[inicio:fim]
        # Os comentarios saem ANTES da checagem: o proprio comentario que
        # registra a decisao cita `order: -1` para explicar por que ele nao
        # esta ali, e a assercao o encontrava — o teste reprovava a sua
        # documentacao.
        sem_comentario = re.sub(r"/\*.*?\*/", "", bloco, flags=re.S)
        self.assertNotIn("order: -1", sem_comentario)
        # E o comentario registra a decisao, para a linha nao voltar sozinha.
        self.assertIn("SEM order: -1", bloco)

    def test_the_step_ladder_never_shows_a_percentage(self):
        """A escada fala em etapas; quem responde "quanto falta" e a barra.

        Misturar as duas linguagens foi o defeito original: a barra parada em
        0% durante um job de minutos parecia travamento, porque ela era a
        unica coisa na tela tentando responder "quanto falta".
        """
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        inicio = js.index("function renderSteps")
        corpo = js[inicio:js.index("\n  }", inicio)]
        self.assertNotIn("%", corpo)
        self.assertIn("data-state", corpo)


class FrontendPolishTests(unittest.TestCase):
    """Higiene do frontend que o audit apontou e não dá para ver num F5 só.

    Fonte self-hosted (antes: <link> do Google render-blocking), favicon,
    corte de sufixo no ramo estático (server.py e __pycache__ moram em web/),
    CSP fechado, âncoras sob o header sticky e o default de cookies que vazava
    o usuário da máquina no markup.
    """

    def test_pages_do_not_call_google_fonts(self):
        for name in ("index.html", "scrap.html"):
            html = (server.WEB_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("fonts.googleapis.com", html)
            self.assertNotIn("fonts.gstatic.com", html)
        css = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")
        self.assertIn("@font-face", css, "a fonte deixou de ser self-hosted")
        self.assertIn("/fonts/inter-latin.woff2", css)
        self.assertIn("font-display: swap", css)

    def test_font_files_are_served_as_woff2(self):
        self.assertIn(".woff2", server._ASSET_TYPES)
        self.assertEqual(
            server.asset_content_type(Path("inter-latin.woff2")), "font/woff2"
        )

    def test_static_route_refuses_python_sources(self):
        # web/server.py e web/__pycache__ estao no mesmo diretorio que o CSS:
        # sem o corte por sufixo, GET /server.py serviria o fonte pelo ramo
        # que serve os assets. O 404 e identico ao de arquivo inexistente.
        src = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn("asset.suffix.lower() not in _ASSET_TYPES", src)
        for suffix in (".py", ".pyc", ".pyo", ".pyd"):
            self.assertNotIn(suffix, server._ASSET_TYPES)

    def test_csp_finishes_the_job(self):
        csp = server.Handler.CSP
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("base-uri 'none'", csp)
        self.assertIn("font-src 'self'", csp)
        self.assertNotIn("fonts.googleapis.com", csp)
        self.assertNotIn("fonts.gstatic.com", csp)

    def test_all_pages_have_a_favicon(self):
        """A aba mostra a marca; sem o link ela cai no padrao do navegador.

        Eram so `index.html` e `scrap.html` — `ajustes.html` ficou de fora e
        por isso nunca recebeu o `<link rel="icon">`. A mesma lista de paginas
        que o teste de `.brand-mark` usa, para as duas nunca mais divergirem.
        """
        self.assertTrue((server.WEB_DIR / "favicon.svg").is_file())
        for name in ("index.html", "ajustes.html", "scrap.html"):
            html = (server.WEB_DIR / name).read_text(encoding="utf-8")
            self.assertIn('rel="icon" href="/favicon.svg"', html)

    def test_anchors_clear_the_sticky_header(self):
        # scrollIntoView/#config e o foco do #url rolam o elemento ate a borda
        # top da viewport: sem scroll-margin o titulo para POR BAIXO da barra
        # de 68px; o card "Escolha" do scrap com top=18px grudava atras dela.
        shared = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")
        self.assertIn("scroll-margin-top: 84px", shared)
        scrap_css = (server.WEB_DIR / "scrap.css").read_text(encoding="utf-8")
        self.assertIn(".pick { position: sticky; top: 80px; }", scrap_css)
        self.assertNotIn("position: sticky; top: 18px", scrap_css)

    def test_browse_button_tells_the_user_where_the_dialog_is(self):
        # Sem abort: o request vive enquanto o dialogo esta aberto (uma escolha
        # demorada e legitima). O estado precisa e mostrar que esta vivo.
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        self.assertIn("Aguardando… ", js)
        self.assertIn("confira também outro monitor", js)

    def test_cookies_default_is_not_baked_into_the_markup(self):
        html = (server.WEB_DIR / "scrap.html").read_text(encoding="utf-8")
        self.assertNotIn("USUARIO", html, "o caminho da maquina de quem commitou voltou")
        self.assertIn('id="scrap-cookies-file"', html)
        js = (server.WEB_DIR / "scrap.js").read_text(encoding="utf-8")
        self.assertIn("vc-cookies-file", js, "o caminho deixou de ser lembrado por navegador")


class BackendReadinessTests(unittest.TestCase):
    """A pagina diz se ela mesma consegue falar com o servidor.

    A meta inicial era responder "o servidor esta no ar?" numa linguagem que
    o leigo entenda. A resposta estrutural foi esta: a pagina nao consegue
    PERGUNTAR, ela so consegue tentar — e quando a tentativa falha ela diz o
    que ela sabe, que e que a tentativa nao passou. O que muda entre os casos
    e o conselho, e era para ai que o visitante se perdia: sem servidor e o
    servidor sao duas falhas distintas, e mandar subir um processo que ja
    esta rodando e mandar fazer nada.
    """

    def html(self) -> str:
        return (server.WEB_DIR / "index.html").read_text(encoding="utf-8")

    def js(self) -> str:
        return (server.WEB_DIR / "index.js").read_text(encoding="utf-8")

    def css(self) -> str:
        return (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    def test_the_page_declares_whether_it_has_a_backend(self):
        """A nota existe, nasce escondida, e mora onde a pessoa esta olhando.

        Escondida e obrigatorio: numa pagina funcionando o painel nao pode
        carregar uma linha de aviso a toa, senao o aviso vira ruido e para de
        ser lido — e um aviso que sempre aparece nao informa nada.
        """
        html = self.html()
        self.assertIn('id="backend-state"', html)
        self.assertIn('class="backend-note" id="backend-state" hidden', html)
        pino = html.index('id="backend-state"')
        self.assertLess(html.index('id="status-pill"'), pino)
        self.assertLess(pino, html.index('id="progress-track"'))

    def test_the_advice_changes_with_what_actually_failed(self):
        """Origem errada e servidor caido pedem coisas opostas.

        Trava na ORIGEM e nao no protocolo, por medida no navegador: a pagina
        servida em 7842 era bloqueada igual a de `file:` (nao ha cabecalho
        CORS), e a nota mandava subir um servidor que estava de pe — e depois
        mandava abrir o endereço certo, que era o unico conselho bom dos dois.
        """
        js = self.js()
        self.assertIn("location.origin !== API", js)
        # O ramo de origem errada manda abrir o 7755 — nao subir nada.
        self.assertIn("http://127.0.0.1:7755/ e carregue", js)
        self.assertIn("Endereço errado", js)
        # So o ramo de origem CERTA manda subir servidor: ali quem serviu a
        # pagina foi o servidor, entao se ele parou depois, ele parou mesmo.
        # Fatia com o inicio ancorado: `} else {` existe em varios outros
        # pontos do arquivo, e uma busca a partir do zero devolveria uma
        # janela negativa (string vazia) em vez de reprovar.
        ini = js.index("} else if (r.sem_conexao) {")
        fim = js.index("} else {", ini)
        ramo = js[ini:fim]
        self.assertGreater(len(ramo), 80, "ramo truncado: confira o recorte")
        self.assertIn('"python web/server.py"', ramo)
        self.assertIn("r.sem_conexao", js)
        # Se os tres avisos fossem a mesma frase, o encadeamento nao estaria.
        self.assertIn("O servidor respondeu com erro (", js)

    def test_a_http_error_is_not_blamed_on_a_missing_server(self):
        """Um 500 nao quer dizer que falta servidor, e nao manda subir um.

        `api()` marcava todo erro de `offline: true`, e `offline` ja virou
        palavra reservada para "o fetch nem saiu". Sem `sem_conexao` a pagina
        nao teria como escolher o ramo, e o conselho viraria chute.

        A assercao e sobre o CONTRATO, nao sobre as linhas que o implementam:
        a versao anterior checava `String(e.message).startsWith('HTTP ')`, que
        sumiu quando `api()` passou a ler o corpo do erro sem lancar excecao.
        O que nao pode mudar e: um `!res.ok` responde com `sem_conexao: false`
        (ramo "Erro"), e uma falha de fetch responde com `sem_conexao: true`
        (ramo "Sem servidor").
        """
        js = self.js()
        bloco = fn_body(js, "api")
        self.assertIn("sem_conexao: false", bloco,
                      "um erro HTTP nao e marcado como diferente de 'sem servidor'")
        self.assertIn("sem_conexao: true", bloco,
                      "uma falha de fetch nao e marcada como 'sem servidor'")
        # O ramo que usa a distincao: `sem_conexao` -> Sem servidor, senao Erro.
        self.assertIn("else if (r.sem_conexao)", js,
                      "a pagina nao separa 'sem servidor' de 'servidor respondeu erro'")


    def test_the_first_poll_does_not_wait_for_the_timer(self):
        """A nota aparece no primeiro segundo, nao no quarto.

        Quem abriu o arquivo pelo disco nao deve esperar um intervalo de 4s
        para descobrir por que nada funciona — e a primeira tentativa e
        tambem a que traz a fila e a galeria antes de haver um unico clicar.
        """
        js = self.js()
        self.assertIn("startPolling();\n  poll();", js)
        # O `poll` e quem chama a nota; se ele so rodasse via `setInterval`,
        # a nota nasceria atras do primeiro tique.
        self.assertIn("const r = await api('/status');\n    prontidao(r);", js)

    def test_a_healthy_page_shows_nothing_and_keeps_its_running_state(self):
        """No caminho bom a nota some, e o pill nao apaga o trabalho novo.

        A restauracao e condicionada a `ateve_pill_offline` e a `!state.running`
        justamente porque o mesmo pill carrega "Em execucao" e "Concluido":
        zerar o estado a cada tique de 4s apagaria a resposta de um job que
        acabou de terminar.
        """
        js = self.js()
        bloco = js[js.index("function prontidao(r) {"):js.index("async function poll()")]
        self.assertIn("el.hidden = true;", bloco)
        self.assertIn("if (ateve_pill_offline && !state.running)", bloco)
        self.assertIn("ateve_pill_offline = true;", bloco)

    def test_the_note_is_not_registered_as_a_field_hint(self):
        """A nota e estado, nao descricao de campo, e fica fora do registro.

        `FieldDescriptionTests` apaga como orfa qualquer `.hint` que nenhum
        campo referencia — correto, porque ali ha uma descricao esquecida
        pendurada numa entrada. Esta linha descreve nenhuma entrada, entao
        nao pode usar essa classe: sumiria no primeiro passe de limpeza.
        """
        html = self.html()
        self.assertNotIn('class="hint" id="backend-state"', html)
        # E tem estilo proprio: sem ele a nota herdaria o corpo do card e
        # ficaria indistinguivel de paragrafo normal.
        self.assertIn(".backend-note {", self.css())

    def test_run_uses_the_same_note_instead_of_a_second_verdict(self):
        """O botao de gerar nao pode dar outro diagnostico que o poll.

        Eram duas falas: `run()` escrevia "Servidor local não encontrado" e
        o poll dizia outra coisa, e nas duas o arquivo aberto direto acabava
        mandando reiniciar um servidor que nunca parou. Uma fonte so.
        """
        js = self.js()
        # Ancora na frase so do `run()`: pegar o primeiro `if (r.offline)` da
        # pagina arrastaria 178 linhas — de 665 ate 843 — e o teste passaria
        # porque a janela acabasse passando por cima de `prontidao`, sem
        # provar nada sobre o botao de gerar.
        ini = js.index("Servidor local não encontrado")
        fim = js.index("} else if (r.error)", ini)
        branch = js[ini:fim]
        self.assertLess(fim - ini, 600, "a janela cresceu: confira o recorte")
        self.assertIn("prontidao(r);", branch)
        self.assertNotIn("setStatus('error', 'Offline')", branch)


class HostHeaderGuardTests(unittest.TestCase):
    """O painel recusa requisicao cujo Host nao e esta maquina.

    O servidor escuta em 127.0.0.1, entao nao e alcancavel pela rede — mas e
    alcancavel por uma pagina que o usuario tenha aberta, via DNS rebinding:
    um dominio do atacante resolve para 127.0.0.1 e o navegador passa a tratar
    as requisicoes como mesma origem, o que deixa aquela pagina dar POST em
    /run e ler /status. `frame-ancestors` nao ajuda (nao e frame) e CORS nao
    ajuda (para o navegador nao e cross-origin). O Host e a unica coisa que o
    atacante nao forja, porque o navegador escreve o nome em que conectou.
    """

    def test_only_a_local_host_is_accepted(self):
        # Formas legitimas: a mesma origem em que o painel e servido.
        for ok in ("127.0.0.1:7755", "localhost:7755", "127.0.0.1",
                   "[::1]:7755", "::1", "LOCALHOST:7755"):
            self.assertTrue(server._host_is_local(ok), ok)
        # Formas que um rebinding produz, e os sufixos que uma allowlist
        # ingenua (com `endswith`) deixaria passar.
        for ruim in ("evil.com", "evil.com:7755", "127.0.0.1.evil.com",
                     "localhost.evil.com", "", "   "):
            self.assertFalse(server._host_is_local(ruim), ruim)

    def test_the_port_has_to_be_the_one_we_listen_on(self):
        """Um Host com outra porta nao e este servidor falando consigo.

        Sem isto o guarda nao sobrevive ao `--port`: bastava acertar o nome.
        """
        self.assertTrue(server._host_is_local("127.0.0.1:8000", 8000))
        self.assertFalse(server._host_is_local("127.0.0.1:7755", 8000))
        # Sem porta no Host e aceito: o navegador omite na 80.
        self.assertTrue(server._host_is_local("127.0.0.1", 8000))

    def test_both_entrypoints_call_the_guard(self):
        """A guarda fica no topo de do_GET e do_POST, nao dentro de uma rota.

        Uma checagem por rota e uma que a proxima rota esquece; foi assim que
        a pagina de Ajustes pôde nascer sem H1 e ninguem notou.
        """
        import inspect

        for nome in ("do_GET", "do_POST"):
            src = inspect.getsource(getattr(server.Handler, nome))
            self.assertIn("_guard_origin()", src, nome)
            # Tem de vir antes de qualquer despacho de rota.
            self.assertLess(src.index("_guard_origin()"), src.index("urlparse"),
                            f"{nome}: a guarda vem depois do parse do path")


class AjustesHeadingTests(unittest.TestCase):
    """A pagina de Ajustes tem um H1, como as outras duas.

    Ela era a unica das tres sem heading de nivel 1 — o outline comecava no
    H2, entao leitor de tela e navegacao por landmarks nao tinham a que pagina
    pertenciam os blocos seguintes.
    """

    def html(self) -> str:
        return page_source("ajustes.html")

    def test_the_page_has_exactly_one_h1(self):
        html = self.html()
        self.assertEqual(len(re.findall(r"<h1[\s>]", html)), 1, "H1 unico")

    def test_the_h1_names_the_page(self):
        """O H1 diz de que assunto a pagina trata, nao so o nome do rail.

        O rotulo do rail e "Ajustes"; o heading diz *Ajustes do projeto* porque
        e o que a pagina governa -- os valores valem para todo o projeto, no
        painel e na linha de comando. Prender o H1 a palavra "Ajustes" e o que
        importa; o resto do texto pode evoluir sem falso alarme.
        """
        html = self.html()
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        self.assertIsNotNone(m, "a pagina tem de ter um H1")
        titulo = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        self.assertTrue(titulo.startswith("Ajustes"),
                        f"o H1 nomeia a pagina, veio: {titulo!r}")

    def test_no_heading_level_is_skipped(self):
        """O outline nao salta de nivel: H1 -> H2, nunca H1 -> H3."""
        html = self.html()
        niveis = [int(t[1]) for t in re.findall(r"<(h[1-6])[\s>]", html)]
        self.assertTrue(niveis, "a pagina tem headings")
        self.assertEqual(niveis[0], 1, "o outline comeca no H1")
        for anterior, seguinte in zip(niveis, niveis[1:]):
            self.assertLessEqual(seguinte, anterior + 1,
                                 f"salto de H{anterior} para H{seguinte}")

    def test_the_h1_is_labelled_by_its_section(self):
        """A secao que abriga o titulo tem nome acessivel.

        Sem `aria-labelledby`, a navegacao por landmarks (rotor do VoiceOver)
        ouve "regiao" sem nome — o heading e a regiao sao coisas separadas.
        """
        html = self.html()
        m = re.search(r'<section[^>]*aria-labelledby="([^"]+)"', html)
        self.assertIsNotNone(m, "a secao do titulo precisa de aria-labelledby")
        self.assertIn(f'id="{m.group(1)}"', html, "o alvo do aria-labelledby existe")
        self.assertRegex(html, rf'<h1[^>]*id="{m.group(1)}"')


class RenderSectionOwnershipTests(unittest.TestCase):
    """A secao Renderizacao vive so em /ajustes; a Cortes apenas a le.

    Antes as duas paginas desenhavam os mesmos 11 controles, cada uma com a sua
    copia do markup. Duas copias do mesmo formulario divergem: a do index.html
    ficou com o layout antigo (font-size no primeiro triple) e com o id errado
    do loudness (`lufs`, que o servidor so aceitava por um alias de
    compatibilidade). O index.js lia os 11 campos do DOM e mandava no POST /run.

    Agora a Cortes le state.ajustes, carregado de /ajustes.json no boot -- o
    mesmo padrao que Selecao e Transcricao ja usavam. Estes testes travam as
    duas metades do contrato: o markup saiu de la, e a coleta le do estado.
    """

    #: Os 11 controles que migraram. O valor e a chave em state.ajustes.
    MIGRADOS = {
        "layout": "layout",
        "caption-preset": "caption_preset",
        "caption-style": "caption_style",
        "font-size": "font_size",
        "crf": "crf",
        "target-lufs": "target_lufs",
        "workers": "workers",
        "headline-seconds": "headline_seconds",
        "progress-bar-on": "progress_bar",
        "jump-cut": "jump_cut",
        "loudnorm": "loudnorm",
    }

    def setUp(self):
        self.index_html = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.index_js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        self.ajustes_html = (server.WEB_DIR / "ajustes.html").read_text(encoding="utf-8")

    def test_the_controls_left_the_cortes_page(self):
        for control in self.MIGRADOS:
            self.assertNotIn(
                f'id="{control}"', self.index_html,
                f"o controle {control} voltou para index.html",
            )

    def test_the_controls_still_live_in_ajustes(self):
        # O outro lado: tirar da Cortes sem ter na Ajustes apagaria o controle
        # das duas paginas, e o valor nao teria mais onde ser editado.
        for control in self.MIGRADOS:
            self.assertIn(
                f'id="{control}"', self.ajustes_html,
                f"o controle {control} sumiu de ajustes.html",
            )

    def test_no_leftover_reader_of_the_removed_controls(self):
        """O par e obrigatorio: tirar o campo E quem o le.

        `$('#layout').value` com o markup removido estoura `TypeError` em null,
        e o POST /run morre antes de sair -- a pagina parece quebrada sem
        mensagem. Este teste e a rede contra isso.
        """
        for control in self.MIGRADOS:
            self.assertNotIn(f"'#{control}'", self.index_js,
                             f"index.js ainda le '#{control}'")

    def test_the_collection_reads_the_saved_settings(self):
        corpo = fn_body(self.index_js, "collectOptions")
        for chave in self.MIGRADOS.values():
            self.assertIn(
                f"state.ajustes.{chave}", corpo,
                f"collectOptions nao le state.ajustes.{chave}",
            )

    def test_every_migrated_key_is_accepted_by_the_loader(self):
        """Quem le de state.ajustes depende de applyAjustes ter escrito la.

        As chaves sao agrupadas por tipo dentro de applyAjustes; uma chave nova
        que ninguem classificou fica com o default para sempre, e o painel
        pareceria ignorar o que foi salvo.
        """
        corpo = fn_body(self.index_js, "applyAjustes")
        for chave in self.MIGRADOS.values():
            # font_size tem tratamento proprio (tem um terceiro estado, null).
            existe = (f"'{chave}'" in corpo) or (f"{chave}" in corpo)
            self.assertTrue(existe, f"applyAjustes nao classifica {chave}")

    def test_the_font_size_keeps_its_third_state(self):
        """`font_size` tem tres estados, e o null nao pode ser descartado.

        Vazio = "herda do preset". Se ele entrasse junto com os numericos, o
        filtro `valor !== null` o descartaria e o preset nunca voltaria a valer
        depois de alguem digitar um tamanho.
        """
        corpo = fn_body(self.index_js, "applyAjustes")
        self.assertIn("font_size", corpo)
        self.assertRegex(
            corpo, r"font_size[\s\S]{0,400}null",
            "o tratamento de font_size perdeu o estado null",
        )

    def test_the_legacy_name_is_not_sent_by_the_page(self):
        """A Cortes manda `target_lufs`, nao o alias `lufs`.

        O alias sobrevive no servidor para uma pagina em cache; uma pagina atual
        mandando o nome velho manteria a divida viva sem motivo.
        """
        corpo = fn_body(self.index_js, "collectOptions")
        self.assertIn("target_lufs:", corpo)
        self.assertNotRegex(corpo, r"\blufs\s*:")

    def test_the_step_list_still_names_the_three_steps(self):
        # O resumo do topo cita os tres passos; o texto do terceiro tem de dizer
        # que os ajustes de render moram em Ajustes, como o segundo ja diz.
        self.assertIn("Renderização", self.index_html)
        self.assertRegex(self.index_html, r"Renderização</strong><small>[^<]*Ajustes")


class CuradorFieldsFollowTheSwitchTests(unittest.TestCase):
    """O bloco do curador so vale quando o interruptor esta ligado.

    O payload do run so le `ranker_provider`, `ranker_model`, `ranker_top_n`,
    `ranker_weight` e os demais dentro do `if (o.ranker === 'llm')` do
    `cliCommand`. Sem desabilitar o bloco com o toggle desligado, a pagina
    aceitava edicao de seis campos que seriam descartados em silencio — o mesmo
    defeito do rail-brand: um controle que PARECE ativo e nao tem efeito.
    """

    def setUp(self):
        self.html = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        self.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    def test_the_fields_live_inside_a_fieldset_that_starts_disabled(self):
        """`disabled` no <fieldset> e o que faz a regra valer para os seis.

        Marcar campo a campo e onde o proximo campo esquecido entra; o
        <fieldset> desabilita o grupo inteiro de uma vez.
        """
        m = re.search(r"<fieldset[^>]*id=\"ranker-fields\"[^>]*>", self.html)
        self.assertIsNotNone(m, "os campos do curador precisam de um <fieldset>")
        self.assertIn("disabled", m.group(0), "o <fieldset> nasce desabilitado")

    def test_every_curador_control_is_inside_that_fieldset(self):
        """O grupo tem de conter TODOS os seis, senao sobra campo ativo a toa."""
        i = self.html.index('id="ranker-fields"')
        fim = self.html.index("</fieldset>", i)
        bloco = self.html[i:fim]
        for cid in ("ranker-provider", "ranker-model", "ranker-base-url",
                    "ranker-api-key-env", "ranker-top-n", "ranker-weight"):
            with self.subTest(control=cid):
                self.assertIn(f'id="{cid}"', bloco,
                              f"{cid} ficou fora do <fieldset> e nunca desabilita")

    def test_the_switch_drives_the_fieldset(self):
        """O toggle chama a sincronizacao, e a funcao escreve `disabled`."""
        self.assertIn("if (t.id === 'ranker-llm') syncRankerFields();", self.js)
        corpo = fn_body(self.js, "syncRankerFields")
        self.assertIn("$('#ranker-fields')", corpo)
        self.assertRegex(corpo, r"\.disabled\s*=",
                         "syncRankerFields nao escreve o disabled")

    def test_the_off_note_is_shown_only_when_off(self):
        corpo = fn_body(self.js, "syncRankerFields")
        # A nota diz "esta desligado"; com o interruptor ligado ela some.
        self.assertRegex(corpo, r"ranker-off-note[\s\S]{0,120}\.hidden\s*=\s*on",
                         "a nota do estado desligado nao acompanha o toggle")

    def test_the_disabled_block_is_styled_dimmed(self):
        self.assertIn(".curador-campos:disabled", self.css)

    def test_the_automatic_count_warns_that_top_n_is_a_ceiling(self):
        """Com count=0 o curador limita a saida, e a tela tem de dizer isso.

        O motor avisa em log (pipeline.py); sem o aviso na tela, a pessoa pede
        "quantos o video render" e recebe `ranker_top_n` sem entender por que.
        """
        self.assertIn('id="ranker-top-n-hint"', self.html)
        corpo = fn_body(self.js, "refreshRankerTopNHint")
        self.assertIn("$('#count')", corpo, "o aviso ignora a quantidade")
        self.assertIn("=== 0", corpo, "o aviso nao testa o modo automatico")
        self.assertRegex(corpo, r"toggleOn\('#ranker-llm'\)",
                         "o aviso nao considera se o curador esta ligado")

    def test_the_count_field_refreshes_the_warning(self):
        self.assertRegex(
            self.js, r"\$\('#count'\)\.addEventListener\('input',\s*refreshRankerTopNHint\)",
            "editar a quantidade nao reavalia o aviso do Top-N",
        )

    def test_a_failed_providers_route_is_announced_not_silent(self):
        """`/providers` fora do ar deixava o dropdown vazio e MUDO.

        O caminho manual (endpoint/modelo digitados) segue valido, entao nao e
        erro fatal — mas a pessoa precisa saber por que nao ha provedor nomeado.
        """
        corpo = fn_body(self.js, "markProvidersUnavailable")
        self.assertIn("ranker-provider-hint", corpo)
        self.assertIn("hint-erro", corpo)
        self.assertIn("else markProvidersUnavailable();", self.js,
                      "a falha de /providers nao e tratada no initCurator")


class UserProviderCardTests(unittest.TestCase):
    """O card "Meus provedores": incluir um endpoint proprio e testa-lo.

    A tabela de ``providers.py`` e revisada e versionada — a nota de cada
    entrada e uma medicao. Este card existe para o caso que ela nao cobre: um
    endpoint que o usuario ja tem e nao pode esperar por um commit. Duas coisas
    aqui nao podem se perder sem quebrar o recurso em silencio:

    1. o TESTE acontece ANTES de salvar, senao a unica forma de descobrir que a
       URL esta errada e gravar primeiro;
    2. a lista que volta do POST redesenha tambem o seletor do Curador, senao o
       provedor recem-salvo so aparece depois de recarregar a pagina.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = page_source("index.html")
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")

    def test_the_card_exists_on_the_cortes_page(self):
        self.assertIn('id="card-meus-provedores"', self.html)

    def test_every_control_the_js_reads_is_in_the_markup(self):
        """Um id lido e nao escrito estoura com `null` no primeiro clique.

        Remover markup com id e sempre um par: tirar o campo E tirar quem o le.
        A lista e derivada do proprio JS para nao envelhecer.
        """
        for name in ("prov-name", "prov-label", "prov-base-url", "prov-model",
                     "prov-api-key-env", "prov-note", "prov-requires-key",
                     "prov-list", "prov-empty", "prov-status", "prov-result",
                     "btn-prov-test", "btn-prov-save", "btn-prov-clear"):
            self.assertIn(f'id="{name}"', self.html, f"falta o controle {name}")

    def test_the_provider_form_requires_a_key_by_default(self):
        """Quase todo endpoint remoto exige chave; o caso sem chave e o local.

        Nascer desligado faria a pessoa testar um endpoint remoto sem chave e
        receber um 401 sem entender por que.
        """
        trecho = self.html.split('id="prov-requires-key"', 1)[1][:200]
        self.assertIn('aria-checked="true"', trecho)

    def test_the_test_button_is_not_the_save_button(self):
        """Testar e salvar sao acoes diferentes e nao podem virar uma so.

        Se testar salvasse, o "testar antes de salvar" (que e o ponto do card)
        viraria "salvar com um nome", e um teste que falha deixaria lixo no
        arquivo.
        """
        self.assertIn('id="btn-prov-test"', self.html)
        self.assertIn('id="btn-prov-save"', self.html)
        testar = fn_body(self.js, "testProvider")
        self.assertIn("/providers/test", testar)
        self.assertNotIn("/providers/save", testar,
                         "o botao de testar tambem salva")

    def test_the_test_sends_what_is_typed_not_what_is_saved(self):
        """O teste tem de valer para um provedor que ainda nao existe no arquivo.

        E o motivo do card existir: descobrir que a URL esta errada antes de
        grava-la. Se o teste lesse so o que ja foi salvo, o usuario teria de
        salvar um palpite para poder testa-lo.
        """
        testar = fn_body(self.js, "testProvider")
        self.assertIn("providerFormPayload()", testar,
                      "o teste nao le o formulario")

    def test_a_successful_save_redraws_the_curator_select(self):
        """Salvar tem de refletir no seletor do Curador sem recarregar a pagina."""
        aplicar = fn_body(self.js, "applyProviderPayload")
        self.assertIn("populateProviders", aplicar,
                      "salvar nao reconstroi o seletor do Curador")
        self.assertIn("renderProviderList", aplicar,
                      "salvar nao redesenha a lista do card")

    def test_saving_preserves_the_selected_provider(self):
        """Salvar um provedor novo nao pode trocar o que ja estava escolhido.

        A assercao e sobre a CONDICAO do guarda, nao sobre a atribuicao: com
        `select.value = anterior` sozinho, apagar a condicao (`if (false)`)
        deixava o teste verde, porque a linha continuava no arquivo. O que
        decide o comportamento e o `if`.
        """
        aplicar = fn_body(self.js, "applyProviderPayload")
        self.assertIn("provItems.some((p) => p.name === anterior)", aplicar,
                      "o guarda de 'preservar a escolha' foi removido")

    def test_the_validation_message_from_the_server_is_shown_verbatim(self):
        """A recusa do servidor ja e a frase que diz o que corrigir.

        Trocar por um "nao consegui salvar" generico jogaria fora o unico texto
        util do fluxo. E a frase so chega se `api()` ler o corpo da resposta
        de erro: `throw new Error('HTTP ' + status)` mostrava "HTTP 400" e
        descartava o motivo que o servidor escreveu.
        """
        bloco = fn_body(self.js, "api")
        self.assertIn("await res.json()", bloco,
                      "api() nao le o corpo do erro")
        self.assertIn("detalhe.error", bloco,
                      "api() nao usa a razao que o servidor mandou")
        salvar = fn_body(self.js, "saveProvider")
        self.assertIn("data.error", salvar)

    def test_the_test_button_is_locked_while_the_probe_runs(self):
        """Dois cliques seriam duas chamadas pagas e uma resposta fora de ordem."""
        testar = fn_body(self.js, "testProvider")
        self.assertIn("aria-busy", testar)

    def test_a_probe_result_is_shown_not_only_logged(self):
        testar = fn_body(self.js, "testProvider")
        self.assertIn("showProviderResult", testar)

    def test_the_verdict_drives_the_colour_not_just_ok(self):
        """"respondeu" e "respondeu o que eu pedi" sao resultados diferentes.

        Foi exatamente essa diferenca que pegou os cinco modelos inuteis que o
        doc do projeto registra (200 com content vazio, tradutor devolvendo a
        instrucao). Pintar tudo de verde por `ok` perderia a distincao.
        """
        mostrar = fn_body(self.js, "showProviderResult")
        self.assertIn("weak", mostrar)
        self.assertIn("echo", mostrar)

    def test_the_card_reads_the_user_route_on_load(self):
        self.assertIn("/providers/user", self.js)

    def test_saving_refuses_to_shadow_a_built_in_name(self):
        """O servidor recusa; este teste trava a recusa no lugar.

        Sem ela, um arquivo editado a mao chamado "openai" seria listado,
        editavel, e nao faria nada no run — o pior dos tres comportamentos.

        A assercao e sobre a LINHA de guarda, nao sobre a presenca da tabela: o
        corpo do handler tambem menciona ``providers.PROVIDERS`` numa checagem
        de sanidade no topo, entao um `assertIn` solto ficava verde mesmo com o
        guarda removido.
        """
        fonte = (server.REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8")
        corpo = fonte.split("def _handle_save_provider", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("if name in providers.PROVIDERS:", corpo,
                      "o save nao recusa sobrescrever um provedor de fabrica")
        self.assertIn("400", corpo, "a colisao nao e recusada")

    def test_the_probe_route_reports_a_missing_key_without_calling_out(self):
        """Sem a variavel no ambiente, o servidor responde ANTES de sair na rede.

        Mandar a chamada sem chave daria um 401 que o usuario teria de decodificar,
        quando a causa (a variavel nao esta exportada) e conhecida aqui.
        """
        fonte = (server.REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8")
        corpo = fonte.split("def _handle_test_provider", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("no-key", corpo)


class UserProviderStoreTests(unittest.TestCase):
    """O arquivo do painel e resolvido de um lugar so.

    A primeira versao tinha duas constantes para o mesmo caminho: o servidor
    declarava ``USER_PROVIDERS_PATH`` e o modulo tinha ``USERS_PATH``. O save
    gravava num e a mesclagem lia o outro, entao todo save respondia 200 com
    uma lista que nao tinha mudado.
    """

    def test_the_server_path_comes_from_the_module(self):
        from viralclipper import user_providers

        self.assertEqual(
            server.USER_PROVIDERS_PATH,
            (server.REPO_ROOT / user_providers.USERS_PATH).resolve()
            if not Path(user_providers.USERS_PATH).is_absolute()
            else Path(user_providers.USERS_PATH).resolve(),
        )

    def test_the_module_default_now_points_at_the_server_file(self):
        """A mesclagem passa pelo modulo, entao ele tem de ler o mesmo arquivo."""
        from viralclipper import user_providers

        self.assertEqual(Path(user_providers.USERS_PATH).resolve(),
                         server.USER_PROVIDERS_PATH)

    def test_the_store_file_is_not_committed(self):
        """É dado do usuario, nao config do projeto: nao pode aparecer no git."""
        gitignore = server.REPO_ROOT / ".gitignore"
        if not gitignore.is_file():
            self.skipTest("sem .gitignore")
        texto = gitignore.read_text(encoding="utf-8")
        self.assertIn("provedores-usuario.toml", texto,
                      "o arquivo do usuario nao esta no gitignore")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()