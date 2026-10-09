"""Unit tests for the web UI server path resolution.

The gallery serves clips whose file names carry accents; the browser sends
them percent-encoded, and :mod:`web.server` must decode before it touches the
filesystem. These tests lock the pure path logic; the HTTP layer is exercised
by running the real server against the rendered output.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from viralclipper import report
from viralclipper.config import ClipConfig
from viralclipper.ig_profile import ProfileItem, ProfileListing
from viralclipper.util import ClipperError
from web import server
from web.server import resolve_within

# A medicao do DOM de verdade. Os testes de markup que dependem do JS rodar
# (rail, rodape, marca) chamam `pagina_montada`, que executa o /comum.js com os
# scripts da pagina e devolve o DOM resultante. Ver tests/_dom.js.
from tests._dom_bridge import pagina_montada

# O MESMO modulo que ``server.py`` importa. Ele faz ``import routes_providers``
# com ``WEB_DIR`` no path, entao a instancia viva e' ``sys.modules[
# "routes_providers"]``. Um ``from web import routes_providers`` criaria uma
# SEGUNDA instancia (``web.routes_providers``) e todo ``patch.object`` cairia no
# modulo errado -- o teste passaria sem testar. A armadilha e' a mesma que a
# extracao de ``state.py`` ja' documentou: um nome, um dono, um alvo de patch.
routes_providers = server.routes_providers
routes_scrap = server.routes_scrap


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
        """do_GET must have a /biblioteca branch, not fall through to 404."""
        source = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")
        self.assertIn('"/biblioteca", "/biblioteca.html"', source)
        self.assertIn('WEB_DIR / "scrap.html"', source)

    def test_the_old_address_still_arrives(self):
        """`/scrap` continua chegando na Biblioteca -- por 301.

        A pagina mudou de endereco (`/scrap` -> `/biblioteca`) porque
        "Biblioteca" nomeava DUAS coisas: esta pagina (fontes externas) e a
        pasta de saida, que e' a rota JSON. Renomear a pagina resolveu o lado
        que o usuario le.

        Mas esta e' a unica porta para buscar midia. Quem tinha o endereco
        antigo salvo nao pode receber 404 -- a pagina simplesmente sumiria.
        Dai o redirect em vez da rota removida.
        """
        import inspect

        source = inspect.getsource(server.Handler.do_GET)
        self.assertIn('"/scrap", "/scrap.html"', source,
                      "a rota antiga sumiu: o favorito de quem ja usa vira 404")
        self.assertIn('self._send_redirect("/biblioteca")', source)

    def test_the_redirect_is_permanent_and_empty(self):
        """301, nao 302, e sem corpo.

        301 porque o nome mudou de vez: o navegador guarda e para de bater no
        endereco velho. Com 302 ele voltaria ao endereco antigo toda vez, e o
        redirect viraria parte permanente do produto.

        Corpo vazio porque quem segue o `Location` nunca o le -- um corpo com
        HTML criaria uma segunda pagina para manter, e ela divergiria da
        primeira na primeira edicao.
        """
        import inspect

        assinatura = inspect.signature(server.Handler._send_redirect)
        self.assertEqual(assinatura.parameters["code"].default, 301,
                         "o redirect deixou de ser permanente")

        class _Stub:
            def __init__(self):
                self.codes, self.hdrs = [], []

            def send_response(self, code):
                self.codes.append(code)

            def send_header(self, key, value):
                self.hdrs.append((key, value))

            def end_headers(self):
                pass

        stub = _Stub()
        server.Handler._send_redirect(stub, "/biblioteca")
        self.assertEqual(stub.codes, [301])
        cabecalhos = dict(stub.hdrs)
        self.assertEqual(cabecalhos.get("Location"), "/biblioteca")
        self.assertEqual(cabecalhos.get("Content-Length"), "0")

    def test_the_route_survives_the_not_run_guard(self):
        """POST /api/scrap must be handled before the `path != "/api/run"` rejection.

        The normalize handler used to live nested inside that guard. A new route
        added below it would be swallowed and answered 404 with no clue why.
        """
        source = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")
        scrap_at = source.index('if path == "/api/scrap":')
        guard_at = source.index('if path != "/api/run":')
        self.assertLess(scrap_at, guard_at, "POST /api/scrap ficou atras do guard")

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
            results, title, removed = routes_scrap._scrap_results(options)
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
            routes_scrap._scrap_results({"url": "  ", "mode": "link"})

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
        self.assertIn('"/api/scrap/thumb?i="', page)
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
        self.assertIn('"/api/scrap/download"', self.source)
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
        with mock.patch.object(routes_scrap, "_ig_profile_results", side_effect=fake_ig), \
             mock.patch.object(server.download_mod, "fetch_metadata", side_effect=fake_meta), \
             mock.patch.object(server.download_mod, "repair_view_counts",
                               side_effect=lambda *a, **k: None):
            results, _title, _removed = routes_scrap._scrap_results(payload)
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
            return routes_scrap._ig_profile_results(
                "alvo", payload, "cookies.txt", ["--cookies", "cookies.txt"]
            )

    def test_a_missing_cookies_file_is_named(self):
        with self.assertRaises(ClipperError) as ctx:
            routes_scrap._ig_profile_results("alvo", {}, "", [])
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
        """TODOS os tracks do `.main-grid` sao `minmax(0, …)`.

        Filho de grid tem `min-width: auto` por padrao, ou seja, nunca encolhe
        abaixo do seu conteudo. A coluna da esquerda e o formulario, com o
        `<textarea>` de transcricao e o `<select>` de 36 presets: qualquer um
        deles com largura minima intrinseca maior que a coluna empurra o track
        e o `1fr` cresce alem da tela. O `.scrap-grid` ja usa `minmax(0, …)`
        pelos dois motivos; aqui faltava.

        A trava e a FORMA de cada track, nao a QUANTIDADE deles: o grid ja teve
        duas colunas (Fonte+Exec / Prompt) e passou a ter tres (Fonte+Exec /
        Curador+Provedores / Prompt), e contar "2" fazia o teste cair numa
        mudanca legitima de layout em vez de num defeito. O que nao pode
        regredir e `1fr` puro, que encolhe pelo conteudo.
        """
        css = self.body("index.css")
        # O `.main-grid` aparece em MAIS DE UMA regra: uma so com display/gap e
        # a outra (a "base de colunas") com o `grid-template-columns` que vale no
        # desktop. Casar so `\.main-grid\s*\{` acha a primeira e o teste cai
        # dizendo que a coluna nao existe. Entao a busca e pela regra que de
        # fato DECLARA o track — ancorada em coluna 0, fora de `@media`, onde
        # `1fr` sozinho e LEGITIMO (e o empilhamento de coluna unica).
        base = re.search(
            r"^\.main-grid\s*\{[^}]*grid-template-columns:\s*([^;}]+);",
            css, re.S | re.M)
        self.assertIsNotNone(
            base, "a regra base de colunas do .main-grid sumiu")
        colunas = base.group(1)
        # Nenhum track pode ser `1fr` SOLTO: `minmax(0, 1fr)` e a forma que
        # colapsa e `1fr` puro e a que vaza. A trava e no `minmax`.
        self.assertNotRegex(
            colunas, r"(?<!minmax\(0,\s)\b1fr\b",
            f"track com `1fr` solto na regra base do .main-grid: {colunas}")
        self.assertEqual(
            colunas.count("minmax(0,"), colunas.count("fr"),
            f"nem todo track da regra base e minmax(0, …): {colunas}")
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
        for nome in ("index.js", "scrap.js", "publicar.js"):
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
                               ("scrap.html", "scrap.js"),
                               ("publicar.html", "publicar.js")):
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

        Medido no DOM RENDERIZADO, e nao no HTML-fonte: desde que a marca saiu
        dos quatro HTMLs e passou a vir de `renderChrome()`, procurar
        `class="brand-mark"` no texto do arquivo encontra ZERO ocorrencias -- o
        elemento so' existe depois que o `comum.js` roda. Um teste que lesse o
        arquivo passaria a nao testar nada (ou a falhar por ausencia), sem que
        a marca tivesse piorado. O que importa nao e' quantas marcas sao, e'
        que nenhuma voltou a ser texto.

        A marca e' uma so' por pagina agora (o cabecalho tem o proprio icone,
        `.header-context-icon` / `.scrap-header-icon`), mas o teste CONTA em vez
        de fixar o numero: a contagem fixa de 4 envelheceu em silencio no
        passado, quando o cabecalho mudou de marca.
        """
        for pagina in sorted(RenderedMarkupTests.PAGES):
            dados = pagina_montada(
                pagina,
                seletores=(".brand-mark", ".brand-mark img"),
                incluir_html=True)
            with self.subTest(pagina=pagina):
                marcas = dados["achados"][".brand-mark"]
                self.assertTrue(marcas, f"{pagina}: nenhuma marca renderizada")
                for marca in marcas:
                    self.assertIn("<img", marca["innerHTML"],
                                  f"{pagina}: uma marca nao tem imagem")
                #: O atributo e' lido no ELEMENTO, e nao procurado na string
                #: re-serializada: `alt=""` sai impresso como `alt` puro (o
                #: navegador trata os dois igual), entao um `assertIn` de
                #: string media o serializador em vez do HTML da pagina.
                imagens = dados["achados"][".brand-mark img"]
                self.assertTrue(imagens, f"{pagina}: nenhuma <img> na marca")
                for img in imagens:
                    self.assertEqual(img["attrs"].get("src"), "/favicon.svg",
                                     f"{pagina}: a marca aponta para outro arquivo")
                    self.assertEqual(img["attrs"].get("alt"), "",
                                     "a imagem decorativa ganhou nome: o produto "
                                     "ja esta escrito no elemento vizinho")
                    self.assertNotEqual(img["attrs"].get("alt"), None,
                                        f"{pagina}: a imagem nao tem alt")
                #: E o texto `VC` nao pode reaparecer em lugar nenhum.
                self.assertNotIn(
                    ">VC<", dados["html"],
                    f"{pagina} traz o VC em texto em algum ponto do DOM")

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
                # E o rotulo do campo de URL do scrap, com a dica DEPOIS dele.

        # A checagem mora aqui FORA do laco das paginas: ela e do scrap.html, e
        # dentro do laco ela reprovava o index.html tambem, por uma string que
        # nunca esteve no arquivo dele.

        # O rotulo deixou de ser `sr-only` e passou a ser VISIVEL ("Link do
        # video ou perfil"). Nao e perda: rotulo visivel serve a quem ve e a
        # quem usa leitor de tela, enquanto `sr-only` so servia ao segundo. O
        # principio do docstring continua inteiro e e o que se verifica aqui: o
        # controle tem NOME proprio, e a dica (#scrap-hint) entra DEPOIS dele
        # em vez de no lugar dele. Por isso o teste aceita as duas formas e
        # exige, nas duas, que o rotulo carregue texto.
        scrap = self.body("scrap.html")
        rotulo = re.search(
            r'<label[^>]*\bfor="scrap-url"[^>]*>(.*?)</label>', scrap, re.S)
        self.assertIsNotNone(
            rotulo,
            "o campo de URL do scrap perdeu o rotulo: sem ele o controle chega "
            "ao leitor de tela nomeado so pela dica")
        self.assertTrue(
            re.sub(r"<[^>]+>", "", rotulo.group(1)).strip(),
            "o rotulo do campo de URL do scrap esta vazio — um rotulo sem "
            "texto nao nomeia nada, visivel ou escondido")
        self.assertNotEqual(
            rotulo.group(1).strip(), "",
            "o rotulo virou um placeholder: precisa dizer o que o campo e")

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
        # uma secao explica a secao inteira, e nao descreve um controle:
        # referencia-lo seria errado, nao apenas inutil.
        #
        # O `[^"]*` antes do id aceita CLASSES EXTRAS depois da dica. A forma
        # anterior exigia `class="hint"` com aspas logo depois, entao um
        # `class="hint field-note"` ou `class="field-hint tool-note"` nao era
        # reconhecido -- e o elemento ERA uma dica legitima, so que com uma
        # classe a mais. Um teste que reprova a grafia correta deixa de
        # comparar e passa a caçar estilo.
        dica = re.compile(
            r'class="(?:hint|auth-note|field-hint)(?:\s[^"]*)?"[^>]*\bid="([\w-]+)"')
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


class RenderedMarkupTests(unittest.TestCase):
    """O markup que o JS monta, medido DEPOIS dele rodar.

    Os testes de `RailNavigationTests` leem o HTML da fonte. Isso bastava
    enquanto a lista de destinos morava no HTML; hoje ela mora em `RAIL_PAGES` e
    e' escrita pelo `renderRail`, entao "esta no HTML?" mede a coisa errada --
    passa vazio e nao prova nada. Pior: no dia em que alguem tirar o
    `<script src="/comum.js">`, todas as paginas ficam sem rail e nenhum teste
    daquela classe acusa.

    Aqui a pagina e' montada com o /comum.js de verdade rodando e o DOM e'
    inspecionado. O `/comum.js` e' o unico script executado: os scripts de
    pagina fazem fetch no load (o `poll` do index.js) e o objetivo e' o markup
    do cromo comum, nao o app inteiro.

    Este nao substitui o probe de browser. O DOM de teste nao tem CSS, entao
    ele nao sabe que `.rail-dropdown { display: none }` esconde o menu do header
    no desktop -- por isso "quantos itens existem" se mede aqui, e "quantos
    estao VISIVEIS" continua sendo medido no navegador, com o
    `sticky-probe/aria_current_probe.js`.
    """

    PAGES = ("index.html", "publicar.html", "ajustes.html", "scrap.html")
    DESTINATIONS = ("/", "/publicar", "/ajustes", "/biblioteca")
    #: Arquivo -> a rota em que ele e' servido. Nao da' para derivar do nome:
    #: `scrap.html` atende em `/biblioteca`, e inferir "/scrap" fazia este
    #: teste reprovar por um destino que nao existe.
    ROTA = {
        "index.html": "/",
        "publicar.html": "/publicar",
        "ajustes.html": "/ajustes",
        "scrap.html": "/biblioteca",
    }

    def monta(self, name: str, **kw) -> dict:
        return pagina_montada(name, **kw)

    def test_every_page_renders_the_rail(self):
        """O rail existe no DOM depois do JS, nas quatro paginas."""
        for name in self.PAGES:
            with self.subTest(page=name):
                r = self.monta(name, seletores_um=(".rail",))
                self.assertIsNotNone(r["achadoUm"][".rail"],
                                     f"{name}: o rail nao foi montado")

    def test_no_page_raises_while_rendering_the_common_chrome(self):
        """O /comum.js roda sem excecao em nenhuma das quatro.

        Um `comum.js` que estoura no `renderRail` deixa a pagina sem rail e sem
        rodape, e o erro so' aparece no console do usuario. O harness captura
        isso como falha de teste.
        """
        for name in self.PAGES:
            with self.subTest(page=name):
                r = self.monta(name)
                self.assertEqual([], r["erros"],
                                 f"{name}: o JS do cromo estourou: {r['erros']}")

    def test_every_page_renders_every_destination_twice(self):
        """Quatro destinos, em DUAS listas (rail fixo + menu do header) = 8.

        E' a checagem que o HTML estatico nao faz: ele conta os containers, nao
        os itens. Se o `renderRail` so' preenchesse a primeira lista, o menu do
        header ficaria vazio em tela pequena e nenhum teste atual acusaria.
        """
        for name in self.PAGES:
            with self.subTest(page=name):
                r = self.monta(name, seletores=(".rail-item",))
                itens = r["achados"][".rail-item"]
                self.assertEqual(
                    len(itens), len(self.DESTINATIONS) * 2,
                    f"{name}: esperava {len(self.DESTINATIONS) * 2} itens "
                    f"(4 destinos x 2 listas), veio {len(itens)}")
                self.assertEqual(
                    [i["href"] for i in itens][:len(self.DESTINATIONS)],
                    list(self.DESTINATIONS),
                    f"{name}: os hrefs do rail nao batem com os destinos")

    def test_the_current_page_is_marked_in_the_rendered_dom(self):
        """Cada lista tem EXATAMENTE um item com aria-current="page".

        Medido no DOM montado para a pagina certa: entrando em /ajustes, o item
        marcado em cada lista tem de ser o de /ajustes -- e nao o da raiz. Um
        `railKey` que so' comparasse o comeco do caminho marcaria "/" para
        todas as paginas, e o HTML estatico nunca mostraria isso porque a
        marcacao nao esta nele.
        """
        for name in self.PAGES:
            path = self.ROTA[name]
            with self.subTest(page=name):
                r = self.monta(name, pathname=path, seletores=(".rail-item",))
                marcados = [i for i in r["achados"][".rail-item"]
                            if i["ariaCurrent"] == "page"]
                self.assertEqual(
                    len(marcados), 2,
                    f"{name}: esperava 1 item marcado por lista (2 no total), "
                    f"veio {len(marcados)}")
                for item in marcados:
                    self.assertEqual(
                        item["href"], path,
                        f"{name}: o item marcado aponta para {item['href']}, "
                        f"e nao para a pagina atual ({path})")

    def test_the_brand_is_a_link_in_the_rendered_dom(self):
        """A marca do rail e' um link clicavel para o Estudio, nas quatro."""
        for name in self.PAGES:
            with self.subTest(page=name):
                r = self.monta(name, seletores_um=(".rail-brand-link",))
                link = r["achadoUm"][".rail-brand-link"]
                self.assertIsNotNone(link, f"{name}: a marca do rail nao existe")
                self.assertEqual(link["tag"], "A", f"{name}: a marca nao e um <a>")
                self.assertEqual(link["href"], "/",
                                 f"{name}: a marca nao leva ao Estudio")

    def test_the_local_badge_is_rendered_outside_the_brand_link(self):
        """O selo LOCAL existe, e fora do <a> da marca.

        O selo e' rotulo, nao destino. Dentro do `<a>` ele viraria parte da
        area clicavel e o leitor de tela o anunciaria como nome do link --
        "LOCAL" como destino nao quer dizer nada. Mede-se a ARVORE, nao a
        string: e' o que o `split("</a>")` do teste antigo nao via.
        """
        for name in self.PAGES:
            with self.subTest(page=name):
                r = self.monta(name, seletores=(".rail-brand-badge",))
                selos = r["achados"][".rail-brand-badge"]
                self.assertEqual(len(selos), 1, f"{name}: sem o selo LOCAL")
                self.assertNotIn("A", selos[0]["ancestrais"],
                                 f"{name}: o selo LOCAL entrou no link da marca")

    def test_the_rendered_chrome_classes_exist_in_a_stylesheet(self):
        """Toda classe que o cromo RENDERIZA tem de existir em algum CSS.

        Este teste existe por um bug real: o `headerContextHtml` montava a raiz
        como `pre + '-context'`, e como o prefixo ja' era `header-context` o
        resultado foi `header-context-context` -- uma classe que nenhum CSS
        define. O wrapper perdia o `display:flex` e a altura ia de 38px para
        102px em Estudio, Ajustes e Publicar (a Biblioteca escapou porque la' o
        prefixo e' `scrap-header`, entao `scrap-header-context` saiu certo).

        Nenhum teste pegava isso: o DOM de teste nao aplica CSS, e os probes de
        browser mediam so' a cor do icone. Aqui se confere o CONTRATO entre o que
        o JS escreve e o que o CSS declara -- sem precisar de layout.

        A lista de excecoes e' FECHADA de proposito. A primeira versao deste
        teste filtrava por prefixo ("tudo que comeca com `header-context-` pode
        passar") e por isso NAO pegava o proprio bug que o motivou: a classe
        espuria comeca com `header-context-`. Uma excecao por nome obriga a
        encarar cada caso novo.
        """
        #: As folhas que o cromo usa.
        folhas = ["shared.css", "index.css", "scrap.css", "publicar.css",
                  "ajustes.css"]
        css = ""
        for folha in folhas:
            caminho = server.WEB_DIR / folha
            if caminho.exists():
                css += caminho.read_text(encoding="utf-8")
        #: Os nomes de classe DEFINIDOS no CSS (`.nome`).
        definidas = set(re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", css))
        #: Os nomes de classe que o cromo EMITE no DOM renderizado.
        emitidas = set()
        for name in self.PAGES:
            r = self.monta(name, incluir_html=True)
            for achado in re.finditer(r'class="([^"]+)"', r["html"] or ""):
                emitidas.update(achado.group(1).split())
        #: `header-context-context` nunca pode voltar: e' o bug que este teste
        #: existe para impedir. Por isso ele e' exigido AUSENTE, e nao tolerado.
        self.assertNotIn(
            "header-context-context", emitidas,
            "a raiz do cabecalho voltou a ser `pre + '-context'`: nenhum CSS "
            "define essa classe e o wrapper perde o layout")
        #: Classes sem regra no CSS que NAO vem do cromo (nasceram antes, ou sao
        #: de paginas especificas). Conferidas uma a uma; nenhuma e' do cromo.
        toleradas = {
            "ajustes-abertura__texto", "clips-grid", "clips-status",
            "execution-progress-message", "publicar-lista", "resumo-card",
            "studio-welcome-copy", "sub-heading", "summary-list",
        }
        faltando = sorted(emitidas - definidas - toleradas)
        self.assertEqual(
            [], faltando,
            "o cromo renderiza classes que nenhum CSS define: %r" % faltando)

    def test_the_footer_is_rendered_on_every_page(self):
        """O rodape monta e traz a saida para os docs."""
        for name in self.PAGES:
            with self.subTest(page=name):
                r = self.monta(name, seletores=("[data-footer]",))
                hosts = r["achados"]["[data-footer]"]
                self.assertTrue(hosts, f"{name}: sem hospedeiro de rodape")
                html = hosts[0]["innerHTML"]
                self.assertNotEqual("", html, f"{name}: o rodape nao foi montado")



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

    PAGES = ("index.html", "publicar.html", "ajustes.html", "scrap.html")
    #: Os unicos destinos do rail. Tem de bater com RAIL_PAGES e com as rotas.
    DESTINATIONS = ("/", "/publicar", "/ajustes", "/biblioteca")
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

        Medido no DOM renderizado: a marca hoje nasce em `renderChrome()`, entao
        o HTML-fonte nao a contem mais. A assercao virou estrutural — o
        `<a href="/">` tem de ser ANCESTRAL do texto da marca, e nao um pedaco de
        string entre dois marcadores.
        """
        for name in sorted(RenderedMarkupTests.PAGES):
            dados = pagina_montada(
                name, seletores=(".rail-brand-link",),
                seletores_um=(".rail-brand",))
            with self.subTest(page=name):
                links = dados["achados"][".rail-brand-link"]
                self.assertTrue(links, f"{name}: a marca do rail nao e um link")
                self.assertEqual(links[0]["tag"].lower(), "a",
                                 f"{name}: a marca do rail nao e um <a>")
                self.assertEqual(links[0]["href"], "/",
                                 f"{name}: a marca do rail nao leva ao Estudio")

    def test_the_local_badge_stays_out_of_the_link(self):
        """O selo LOCAL e rotulo, nao destino: fora do `<a>`.

        Dentro, ele viraria parte da area clicavel e o leitor de tela o
        anunciaria como o nome do link — "LOCAL" como destino nao quer dizer
        nada.

        Medido pela CADEIA DE ANCESTRAIS do selo, e nao por `split("</a>")`:
        procurar `</a>` num HTML que ja' passou pelo `innerHTML` do navegador
        nao diz onde o elemento esta' na arvore.
        """
        for name in sorted(RenderedMarkupTests.PAGES):
            dados = pagina_montada(name, seletores=(".rail-brand-badge",))
            with self.subTest(page=name):
                selos = dados["achados"][".rail-brand-badge"]
                self.assertTrue(selos, f"{name}: o selo LOCAL sumiu")
                self.assertNotIn(
                    "A", selos[0]["ancestrais"],
                    f"{name}: o selo LOCAL entrou no link da marca")

    def test_every_page_has_the_two_list_containers(self):
        """Um container no rail fixo, um no menu do header. Nada mais.

        Medido no DOM RENDERIZADO: o segundo container nasce em
        `renderChrome()` (dentro do `menuBtnHtml`). No HTML-fonte sobrou um so'
        -- o do rail --, entao contar `data-rail-list` no arquivo passou a
        medir o lugar errado e dava 1 em vez de 2.
        """
        for name in sorted(RenderedMarkupTests.PAGES):
            dados = pagina_montada(name, seletores=("[data-rail-list]",))
            with self.subTest(page=name):
                self.assertEqual(
                    len(dados["achados"]["[data-rail-list]"]),
                    2,
                    f"{name}: esperava 2 <ul data-rail-list> (rail + menu do header)",
                )

    def test_no_page_carries_the_list_in_markup(self):
        """A lista nao pode voltar para o HTML.

        E' a regressao exata que este passo corrigiu: duas copias escritas a mao
        que ninguem lembra de atualizar juntas. Aqui o alvo e' mesmo o
        HTML-FONTE: o que se proibe e' a lista escrita a mao no arquivo, nao os
        itens que o `renderRail` cria em tempo de execucao (esses sao o objetivo).
        """
        for name in self.PAGES:
            markup = self.markup(name)
            with self.subTest(page=name):
                self.assertNotIn("data-rail-page", markup)
                self.assertNotIn("rail-item", markup)

    def test_one_container_is_the_rail_and_the_other_is_the_header_menu(self):
        """Cada container no seu dono: o `<nav class="rail">` e o header.

        Medido pela arvore, e nao por `split("</nav>")`: o container do menu
        nasce dentro do header e o do rail dentro do `<nav>`; a assercao
        confere o `ancestrais` de cada um.
        """
        for name in sorted(RenderedMarkupTests.PAGES):
            dados = pagina_montada(name, seletores=("[data-rail-list]",))
            with self.subTest(page=name):
                listas = dados["achados"]["[data-rail-list]"]
                self.assertEqual(len(listas), 2, f"{name}: esperava 2 listas")
                donos = [lista["ancestrais"] for lista in listas]
                self.assertEqual(
                    sum("NAV" in d for d in donos), 1,
                    f"{name}: esperava exatamente 1 lista dentro do <nav>")
                self.assertEqual(
                    sum("HEADER" in d for d in donos), 1,
                    f"{name}: esperava exatamente 1 lista dentro do <header>")

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
        """O botao do menu abre e fecha, e o ARIA diz isso.

        Medido no DOM renderizado E no estado do elemento, nao no texto: o
        botao nasce em `renderChrome()` (dentro do `menuBtnHtml`), entao o
        HTML-fonte nao tem `class="menu-btn` em lugar nenhum. Procurar a
        substring no arquivo passou a falhar nas quatro paginas sem que o
        controle tivesse piorado.

        Aqui os atributos vem do mapa do elemento (`aria_expanded` booleano,
        `aria_controls` por id), e nao de um `assertIn` sobre a string: um
        `assertIn('aria-expanded="false"')` tambem passa se o atributo estiver
        num elemento que NAO e' o botao.
        """
        for name in sorted(RenderedMarkupTests.PAGES):
            dados = pagina_montada(
                name, seletores=(".menu-btn",),
                seletores_um=("#rail-menu-sm",))
            with self.subTest(page=name):
                botoes = dados["achados"][".menu-btn"]
                self.assertEqual(len(botoes), 1,
                                 f"{name}: esperava 1 botao de menu")
                botao = botoes[0]
                self.assertEqual(botao["tag"].lower(), "button",
                                 f"{name}: o menu nao e' um <button>")
                self.assertEqual(botao["attrs"].get("aria-haspopup"), "true",
                                 f"{name}: sem aria-haspopup")
                self.assertEqual(botao["attrs"].get("aria-expanded"), "false",
                                 f"{name}: o menu ja' nasce aberto")
                self.assertEqual(botao["attrs"].get("aria-controls"),
                                 "rail-menu-sm",
                                 f"{name}: o botao nao aponta para a lista")
                #: O alvo do `aria-controls` tem de existir de verdade.
                menu = dados["achadoUm"]["#rail-menu-sm"]
                self.assertIsNotNone(menu,
                                     f"{name}: aria-controls aponta para um id "
                                     "que nao existe no DOM")
                self.assertEqual(menu["attrs"].get("data-rail-menu"), "",
                                 f"{name}: a lista do menu perdeu data-rail-menu")

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

    def test_the_home_page_has_exactly_one_name(self):
        """A raiz tem UM nome -- "Est\u00fadio" -- em todo lugar que a nomeia.

        Antes ela atendia por tr\u00eas nomes ao mesmo tempo: o `<title>` dizia
        "Est\u00fadio", o rail dizia "Cortes" e o header dizia "Est\u00fadio /
        Cortes verticais". Quem lia o menu e depois o titulo nao sabia que era a
        mesma pagina -- e o `<title>`, afinal, e' escrito pelo MESMO RAIL_PAGES
        (`renderRail` faz `document.title = here.title + ' \u00b7 Viral Clipper'`),
        entao "Cortes" era o nome de fato. O nome da pagina e' o que o rail
        declara; "cortes" fica sendo o RESULTADO (a lista de clips), nao o destino.
        """
        pages = self.rail_pages()
        home = [page for page in pages if page.get("path") == "/"]
        self.assertEqual(len(home), 1, "RAIL_PAGES nao tem exatamente uma raiz")
        self.assertEqual(home[0].get("title"), "Est\u00fadio",
                         "a raiz do rail voltou a se chamar de outro nome")

        # O rotulo do rodape que leva a raiz nao pode divergir. Ele nao mora
        # mais no HTML: o rodape e' montado por comum.js e a navegacao dele
        # deriva do MESMO RAIL_PAGES, entao o nome sai daqui por construcao.
        # Antes havia um `<button id="btn-rail-cortes">Cortes</button>` escrito
        # a mao no HTML -- era uma segunda copia do nome, e foi ela que divergiu.
        comum = (server.WEB_DIR / "comum.js").read_text(encoding="utf-8")
        ini = comum.index("function footerNavHtml(")
        nav = comum[ini:comum.index("function footerHtml(")]
        self.assertIn("esc(page.title)", nav)
        self.assertNotIn(">Cortes<", nav)

        index = self.markup("index.html")

        # O "Cortes" que sobrar na interface tem de ser o RESULTADO, nunca um
        # destino. O header da raiz e' o caso que ja divergiu uma vez.
        header_strong = re.search(r"<strong>(.*?)</strong>", index, re.S)
        self.assertIsNotNone(header_strong, "o header da raiz perdeu o <strong>")
        self.assertNotIn("Cortes", header_strong.group(1),
                         "o header voltou a nomear a pagina de Cortes")

    def test_no_user_visible_string_sends_the_user_to_cortes(self):
        """Nenhum rotulo vis\u00edvel manda o usuario para uma pagina "Cortes".

        A verificacao cobre o texto que o usuario LE (nao comentario, nao
        docstring): o botao que leva da Biblioteca ao Estudio, e o resumo de
        Ajustes. Os dois diziam "Cortes" e passariam a mandar para um nome que
        nao existe em menu nenhum.
        """
        scrap = (server.WEB_DIR / "scrap.js").read_text(encoding="utf-8")
        alvo = re.search(r'textContent\s*=\s*"([^"]*Cortes[^"]*)"', scrap)
        self.assertIsNone(alvo,
                          f"scrap.js ainda rotula um destino como Cortes: {alvo and alvo.group(1)}")

        ajustes = (server.WEB_DIR / "ajustes.js").read_text(encoding="utf-8")
        for match in re.finditer(r"\[\s*'Curador'\s*,\s*'([^']*)'\s*\]", ajustes):
            self.assertNotIn("Cortes", match.group(1),
                             "o resumo de Ajustes voltou a mandar o usuario a Cortes")

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


class HeroSectionTests(unittest.TestCase):
    """O hero do Estudio: a porta de entrada do produto.

    Ele ja foi uma pilha de tres cards, cada um tocando um video 9:16, e os
    testes daquela versao (`HeroPreviewTests`) mediam o caminho de degradacao:
    os arquivos ficavam em `web/` e eram GITIGNORED, entao um clone novo nao
    tinha nenhum deles e o card precisava parecer intacto em vez de mostrar um
    icone quebrado. Essa pilha saiu do produto -- `preview-card`,
    `preview-video` e `data-live` hoje tem ZERO ocorrencias em `index.html` --
    e com ela os nove asserts que mediam aquele markup.

    O que ficou no lugar e' copia, um botao e tres atalhos, e e' isso que os
    testes abaixo travam. A cobertura nao foi apagada: foi TROCADA pela do hero
    que existe. Os tres que mais valem medem o que quebra em silencio -- um
    botao sem handler, um atalho para um `id` que nao existe, e o retorno da
    dependencia de midia gitignored.
    """

    #: O texto de cada passo do fluxo, na ordem em que o motor executa.
    WORKFLOW = ("Fonte", "Seleção", "Renderização")

    def setUp(self):
        self.page = page_source("index.html")
        self.markup = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        inicio = self.markup.index('<section class="hero studio-welcome"')
        self.hero = self.markup[inicio:self.markup.index("</section>", inicio)]

    def test_the_hero_labels_itself_with_the_page_h1(self):
        """`aria-labelledby` aponta para o `id` do H1 -- e esse `id` existe.

        Um `aria-labelledby` apontando para um `id` inexistente nao da erro: a
        regiao simplesmente fica sem nome para o leitor de tela, em silencio.
        """
        self.assertIn('aria-labelledby="studio-title"', self.hero)
        self.assertIn('<h1 id="studio-title">', self.markup)
        self.assertEqual(self.markup.count('id="studio-title"'), 1)

    def test_the_cta_is_a_button_that_has_a_handler(self):
        """Botao sem handler e' promessa vazia: o HTML sozinho nao prova nada.

        O `id` e' o contrato entre o markup e o JS, entao o teste exige os dois
        lados. O `type="button"` entra junto porque, dentro de um `<form>`, o
        default `submit` transformaria "colar link" em "enviar o formulario".
        """
        self.assertIn('id="hero-cta"', self.hero)
        self.assertEqual(self.markup.count('id="hero-cta"'), 1)
        self.assertIn("type=\"button\" id=\"hero-cta\"", self.hero)
        self.assertIn("$('#hero-cta').addEventListener", self.page)

    def test_the_cta_scrolls_to_a_form_that_exists(self):
        """O CTA promete levar ao formulario; ele tem de levar a ALGUM lugar.

        `scrollIntoView` num `id` que nao existe nao levanta: o botao fica
        inerte e o usuario conclui que a pagina esta quebrada. Por isso o alvo
        do scroll (`#config`) e o campo que recebe o foco (`#url`) sao
        conferidos no markup, e nao so' no JS.
        """
        self.assertIn("getElementById('config')", self.page)
        self.assertIn("$('#url').focus()", self.page)
        for alvo in ("config", "url"):
            with self.subTest(alvo=alvo):
                self.assertEqual(
                    self.markup.count(f'id="{alvo}"'), 1,
                    f"o CTA aponta para #{alvo}, que nao existe no markup")

    def test_every_jump_link_points_at_an_id_that_exists(self):
        """Atalho para `id` inexistente nao faz nada e nao avisa.

        O rotulo do atalho e' o MESMO texto do `<h2>` de destino -- quem clica
        em "Fila de processamento" tem de chegar num titulo com essas palavras.
        Um atalho morto quebra as duas pontas dessa promessa.
        """
        alvos = re.findall(r'href="#([^"]+)"', self.hero)
        self.assertEqual(len(alvos), 3, "os atalhos do hero mudaram de numero")
        for alvo in alvos:
            with self.subTest(alvo=alvo):
                self.assertEqual(
                    self.markup.count(f'id="{alvo}"'), 1,
                    f"atalho para #{alvo} sem destino na pagina")

    def test_the_workflow_lists_the_pipeline_in_order(self):
        """O fluxo do hero tem de descrever o pipeline REAL, na ordem real.

        Um hero que promete a ordem errada ensina o usuario a esperar o
        contrario do que o motor faz -- e o motor e' `download -> ... ->
        render`, que e' exatamente a ordem abaixo.
        """
        passos = re.findall(r'studio-step-number">(\d+)<', self.hero)
        self.assertEqual(passos, ["01", "02", "03"])
        for indice, rotulo in enumerate(self.WORKFLOW, start=1):
            with self.subTest(passo=rotulo):
                self.assertIn(f"<strong>{rotulo}</strong>", self.hero)
                # A ordem importa: o rotulo tem de vir DEPOIS do seu numero.
                self.assertLess(
                    self.hero.index(f'>{indice:02d}<'),
                    self.hero.index(f"<strong>{rotulo}</strong>"),
                    f"{rotulo} nao esta no passo {indice:02d}",
                )

    def test_the_output_spec_matches_the_project_constant(self):
        """`9:16 1080x1920` e' constante do produto, entao virou rotulo.

        O hero anuncia o formato; se o render mudasse de alvo, este rotulo
        mentiria para o usuario antes de qualquer clique.
        """
        self.assertIn("9:16", self.hero)
        self.assertIn("1080", self.hero)
        self.assertIn("1920", self.hero)

    def test_the_hero_does_not_depend_on_the_gitignored_previews(self):
        """A dependencia que derrubou a versao anterior nao pode voltar.

        `/1.mp4`, `/2.mp4` e `/3.mp4` viviam em `web/` e eram gitignored: um
        clone novo nao tinha nenhum, e o hero so' nao quebrava porque havia um
        caminho de degradacao escrito a mao, so' para isso. O hero atual e'
        copia -- nao depende de arquivo que o repositorio nao carrega.
        """
        for asset in ("/1.mp4", "/2.mp4", "/3.mp4"):
            with self.subTest(asset=asset):
                self.assertNotIn(asset, self.hero)
        self.assertNotIn("<video", self.hero)

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



class PublicacaoPayloadTests(unittest.TestCase):
    """GET /api/publicacao: o recorte do ``clips.json`` que o post consome.

    A galeria do Estudio ja le o MESMO arquivo -- aqui nao ha uma segunda
    fonte, so um segundo recorte. O que a galeria precisa sao os arquivos; o
    que a aba Publicar precisa e o TEXTO que vai no campo de descricao da
    plataforma, mais o poster para o usuario conferir o corte.

    O ``REPO_ROOT`` e' trocado por um diretorio temporario: a funcao le
    ``<repo>/output/clips.json``, e um teste que escrevesse no ``output/`` de
    verdade estaria mexendo no resultado de uma execucao do usuario.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="publicacao-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = self.tmp / "output"
        self.base.mkdir(parents=True)
        self.patch = mock.patch.object(server, "REPO_ROOT", self.tmp)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def escrever(self, documento) -> Path:
        destino = self.base / "clips.json"
        destino.write_bytes(json.dumps(documento, ensure_ascii=False).encode("utf-8"))
        return destino

    @staticmethod
    def clip(indice: int = 1, **extra) -> dict:
        base = {
            "index": indice, "start": 12.4, "end": 40.8, "duration": 28.4,
            "score": 8.4, "meets_minimum": True, "file": "", "hook_terms": [],
            "text": "", "components": {}, "width": 0, "height": 0,
            "headline": "", "hashtags": "", "start_label": "00:12",
            "end_label": "00:40",
        }
        base.update(extra)
        return base

    def test_the_first_run_is_not_an_error(self):
        """Sem relatorio, a pagina diz o que fazer -- nao acusa falha.

        A primeira execucao de quem acabou de instalar cai aqui. Um ``error``
        neste caso faria a tela pedir desculpa por um estado normal.
        """
        payload = server._publicacao_payload()
        self.assertFalse(payload["exists"])
        self.assertEqual(payload["clips"], [])
        self.assertNotIn("error", payload)
        self.assertEqual(payload["path"], "output/clips.json")

    def test_a_broken_report_is_reported_and_not_raised(self):
        """JSON quebrado vira ``error`` na resposta; a pagina continua de pe.

        O arquivo e' reescrito a cada execucao, e uma execucao interrompida
        pode deixar um pela metade. Estourar aqui derrubaria a aba inteira por
        causa de um arquivo que a proxima execucao conserta.
        """
        (self.base / "clips.json").write_bytes(b'{"clips": [')
        payload = server._publicacao_payload()
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["clips"], [])
        self.assertIn("error", payload)

    def test_the_clip_path_becomes_relative_to_output(self):
        """``file`` chega ABSOLUTO do motor; a pagina so pede sob ``output/``.

        ``/api/clips/`` recusa o que sai da pasta, entao entregar o caminho
        absoluto seria entregar um endereco que o proprio servidor nega.
        """
        video = self.base / "clip-01.mp4"
        self.escrever({"title": "T", "clips": [self.clip(file=str(video))]})
        payload = server._publicacao_payload()
        self.assertEqual(payload["clips"][0]["rel"], "clip-01.mp4")

    def test_a_file_outside_the_output_folder_is_not_offered(self):
        """Caminho fora de ``output/`` nao vira endereco.

        Nao e' um caso hipotetico: `--output` aponta para outra pasta e o
        relatorio guarda o caminho absoluto de la. O que nao pode acontecer e'
        a pagina montar `/api/clips/../../algo` e o servidor ter de recusar.
        """
        fora = self.tmp / "fora" / "clip.mp4"
        self.escrever({"clips": [self.clip(file=str(fora))]})
        payload = server._publicacao_payload()
        self.assertEqual(payload["clips"][0]["rel"], "")
        self.assertEqual(payload["clips"][0]["poster"], "")

    def test_the_poster_is_only_offered_when_it_exists(self):
        """Poster e' um jpg ao lado do mp4 -- quando ele foi escrito.

        Sem o arquivo, o ``<img>`` apontaria para um 404 e o cartao mostraria o
        icone de imagem quebrada no lugar da miniatura.
        """
        video = self.base / "clip-01.mp4"
        self.escrever({"clips": [self.clip(file=str(video))]})
        self.assertEqual(server._publicacao_payload()["clips"][0]["poster"], "")

        _poster = server._poster_path(video)
        _poster.write_bytes(b"\xff\xd8\xff\xd9")
        self.assertEqual(server._publicacao_payload()["clips"][0]["poster"], "clip-01.jpg")

    def test_the_text_of_the_post_survives_the_round_trip(self):
        """``headline`` e ``hashtags`` chegam a pagina -- e' o ponto da aba.

        Eles ja existiam no relatorio desde o curador; ate agora so apareciam
        no ``clips.md``, que e' markdown e ninguem cola no TikTok.
        """
        self.escrever({"clips": [self.clip(
            headline="O preco nao era o problema",
            hashtags="#vendas #marketing")]})
        clip = server._publicacao_payload()["clips"][0]
        self.assertEqual(clip["headline"], "O preco nao era o problema")
        self.assertEqual(clip["hashtags"], "#vendas #marketing")

    def test_a_clip_without_the_model_text_still_arrives_whole(self):
        """Ausente vira ``""``, nunca ``None``.

        A pagina chama ``.trim()`` no que recebe. Um ``None`` ali nao e' um
        campo vazio: e' um ``TypeError`` que mata o render do cartao inteiro --
        e o clip sem texto do modelo e' justamente o caso normal de quem
        desliga o curador.
        """
        self.escrever({"clips": [self.clip()]})
        clip = server._publicacao_payload()["clips"][0]
        for campo in ("headline", "hashtags", "text", "rel", "poster"):
            with self.subTest(campo=campo):
                self.assertEqual(clip[campo], "")
        self.assertEqual(clip["hook_terms"], [])

    def test_junk_entries_do_not_break_the_list(self):
        """Uma entrada que nao e' objeto e' pulada, nao derruba as outras."""
        self.escrever({"clips": [None, "texto", 7, self.clip(index=9)]})
        payload = server._publicacao_payload()
        self.assertEqual([c["index"] for c in payload["clips"]], [9])

    def test_a_report_without_a_clip_list_is_an_empty_list(self):
        for documento in ({}, {"clips": None}, {"clips": "todos"}):
            with self.subTest(documento=documento):
                self.escrever(documento)
                self.assertEqual(server._publicacao_payload()["clips"], [])

    def test_the_source_sheet_comes_from_the_same_document(self):
        """Titulo, canal, duracao e modelo vem do relatorio, nao do nome do arquivo."""
        self.escrever({
            "title": "Como eu dobrei o faturamento",
            "uploader": "Canal Exemplo",
            "url": "https://youtu.be/x",
            "source_duration": 2530.5,
            "model": "whisper-large-v3",
            "clips": [self.clip()],
        })
        payload = server._publicacao_payload()
        self.assertEqual(payload["title"], "Como eu dobrei o faturamento")
        self.assertEqual(payload["uploader"], "Canal Exemplo")
        self.assertEqual(payload["source_duration"], 2530.5)
        self.assertEqual(payload["model"], "whisper-large-v3")


class PublicarPageTests(unittest.TestCase):
    """A pagina Publicar: o que ela promete e o que ela nao faz.

    Ela existe para responder uma pergunta que o Estudio nao responde: "o que
    eu escrevo no post de cada clip?". O curador ja escrevia a headline e as
    hashtags, mas elas so saiam no ``clips.md``.
    """

    HTML = server.WEB_DIR / "publicar.html"
    JS = server.WEB_DIR / "publicar.js"
    CSS = server.WEB_DIR / "publicar.css"

    def fonte(self, caminho: Path) -> str:
        return caminho.read_text(encoding="utf-8")

    @staticmethod
    def sem_comentario(fonte: str) -> str:
        """Fora os comentarios -- de linha inteira e de bloco.

        Um teste que procura CODIGO ausente nao pode enxergar a documentacao da
        remocao: o comentario deste arquivo que explica por que a porta fixa
        saiu CITA `127.0.0.1`, e reprovaria o proprio texto que a justifica.
        """
        sem_bloco = re.sub(r"/\*.*?\*/", "", fonte, flags=re.S)
        linhas = [ln for ln in sem_bloco.split("\n")
                  if not ln.strip().startswith(("//", "*"))]
        return "\n".join(linhas)

    def test_the_route_serves_both_spellings(self):
        """`/publicar` e `/publicar.html` servem a MESMA pagina.

        Duas grafias de proposito, como em /ajustes: a pagina linka a primeira,
        e a segunda e' o que as pessoas digitam. Sem a rota, `/publicar.html`
        cairia no ramo estatico -- onde `.html` nao e' um sufixo conhecido -- e
        o arquivo seria *baixado* em vez de mostrado.
        """
        import inspect

        src = inspect.getsource(server.Handler.do_GET)
        self.assertIn('"/publicar", "/publicar.html"', src)
        self.assertIn('WEB_DIR / "publicar.html"', src)

    def test_the_page_never_writes_anything(self):
        """A aba so LE. Nenhuma chamada com verbo de escrita.

        O contrato esta escrito na propria pagina ("esta pagina nao faz upload
        de nada"). Publicar de verdade exige a API de cada plataforma e a
        credencial do usuario -- nada disso mora num servidor local sem
        autenticacao.
        """
        js = self.fonte(self.JS)
        self.assertNotIn("method: 'POST'", js)
        self.assertNotIn('method: "POST"', js)
        self.assertNotIn("method: 'PUT'", js)
        self.assertNotIn('method: "PUT"', js)
        self.assertNotIn("FormData", js)
        self.assertNotIn("sendBeacon", js)

    def test_the_clip_url_is_built_in_one_place_here_too(self):
        """Mesma regra do index.js: o prefixo de `/api/clips/` entra uma vez.

        Sao as duas paginas que pedem arquivo de clip. Duas montagens a mao do
        mesmo caminho e' o que quebrou quando o prefixo virou `/api/clips/`.
        """
        js = self.sem_comentario(self.fonte(self.JS))
        self.assertIn("const CLIP_URL_BASE = '/api/clips/';", js)
        self.assertIn("function clipsPath(", js)
        self.assertEqual(js.count("/api/clips/"), 1,
                         "o prefixo do clip aparece mais de uma vez: ha uma "
                         "segunda montagem do mesmo caminho")

    def test_the_page_does_not_hardcode_the_port(self):
        """A origem e' implicita: a pagina e' servida pelo proprio servidor.

        As outras duas paginas escrevem a porta 7755 a mao, e por isso quebram
        quando o servidor sobe em outra porta (`--port 7756`) -- o CSP
        `connect-src 'self'` recusa o destino. Uma pagina nova nao precisa
        repetir o defeito: `''` ja aponta para o host e a porta certos.
        """
        js = self.sem_comentario(self.fonte(self.JS))
        self.assertIn("const API = '';", js)
        self.assertNotIn("127.0.0.1", js)

    def test_the_caption_is_visible_beside_the_button_that_copies_it(self):
        """O texto fica na TELA, e o botao so' o copia.

        E' o que salva a copia quando o clipboard e' negado (permissao, http
        puro): a mensagem de erro manda "selecione e copie", e para isso o
        texto tem de estar ali. Um botao que guarda a string so no JS deixa o
        usuario sem saida.
        """
        js = self.fonte(self.JS)
        self.assertIn("readonly", js)
        self.assertIn("<textarea", js)

    def test_the_page_has_its_own_sheet_and_only_its_own_selectors(self):
        """O css proprio so' declara o que e' proprio.

        Um seletor generico numa folha carregada depois do shared.css passa a
        valer em qualquer pagina que venha a carregar esta folha, e vira uma
        segunda verdade sobre a mesma coisa -- exatamente o que a extracao para
        o shared.css desfez. O nome do seletor e' a unica trava possivel aqui:
        o CSS nao diz a que pagina uma regra pertence, entao a convencao
        (`publicar-` ou `[data-page="publicar"]`) e' o contrato.
        """
        css = self.fonte(self.CSS)
        # O `@media` fica de fora: um breakpoint nao e' um seletor.
        limpo = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        seletores = []
        for bloco in re.findall(r"([^{}]+)\{", limpo):
            for seletor in bloco.split(","):
                seletor = seletor.strip()
                if seletor and not seletor.startswith("@"):
                    seletores.append(seletor)
        self.assertTrue(seletores, "o css da pagina nao declara nada")
        for seletor in seletores:
            with self.subTest(seletor=seletor):
                self.assertIn(
                    "publicar", seletor,
                    f"{seletor} nao e' desta pagina: use um seletor "
                    "`publicar-` ou escopado por [data-page=\"publicar\"]")

    def test_the_page_borrows_what_already_exists(self):
        """O que ja' existe nao ganha uma segunda copia aqui.

        `.card`, `.btn` e `.field` vivem no shared.css; `.section-title` e
        `.clips-empty`, no index.css. As duas folhas entram na pagina, entao
        redefinir qualquer um deles seria uma segunda verdade sobre a mesma
        coisa -- e a da pagina mandaria, por vir depois.
        """
        css = self.fonte(self.CSS)
        for seletor in (".card", ".btn", ".field", ".section-title",
                        ".clips-empty", ".jump-links", ".rail-item", ".hint"):
            with self.subTest(seletor=seletor):
                self.assertNotRegex(
                    css, r"(?m)^\s*" + re.escape(seletor) + r"\s*\{",
                    f"{seletor} esta no css da pagina e no compartilhado")


class PublicarOrigemVisibilityTests(unittest.TestCase):
    """O card da Origem e o atalho dele sobem e descem JUNTOS.

    O atalho "Origem" mora no `nav.jump-links` da abertura; o card mora no fim
    da pagina. Sao dois elementos, um estado so': "existe ficha da origem?".
    Enquanto cada um decidia sozinho, uma leitura sem relatorio
    (`data.exists === false`) escondia a ficha e deixava o atalho apontando para
    uma secao vazia -- um clique que nao leva a lugar nenhum, e sem erro no
    console. `mostrarOrigem` passou a ser a unica fonte desse estado, e os DOIS
    caminhos que escondem (sem relatorio e servidor fora do ar) passam por ela.

    O teste de comportamento roda o `publicar.js` de verdade, com um DOM falso,
    via node. Um teste que so' lesse o HTML nao veria a divergencia: o HTML esta
    certo -- quem decide o `hidden` e' o JS.
    """

    HTML = server.WEB_DIR / "publicar.html"
    JS = server.WEB_DIR / "publicar.js"

    #: O que o teste le de volta depois de rodar a funcao. `hidden: true`
    #: significa fora da tela.
    ESTADO = ("{card: els['#publicar-origem'].hidden, "
              "atalho: els['#publicar-link-origem'].hidden}")

    def _html(self) -> str:
        return self.HTML.read_text(encoding="utf-8")

    def _js(self) -> str:
        return self.JS.read_text(encoding="utf-8")

    def _nav(self) -> str:
        achado = re.search(r'<nav class="jump-links".*?</nav>', self._html(),
                           re.S)
        self.assertIsNotNone(achado, "publicar.html: nav.jump-links nao existe")
        return achado.group(0)

    @staticmethod
    def sem_comentario(fonte: str) -> str:
        """Fora os comentarios -- de linha inteira e de bloco.

        Um teste que conta ocorrencias de um seletor nao pode enxergar a
        documentacao da regra: o comentario que explica por que o card sai
        CITA o `#publicar-origem`, e contaria como se fosse uma segunda
        decisao sobre o `hidden`.
        """
        sem_bloco = re.sub(r"/\*.*?\*/", "", fonte, flags=re.S)
        linhas = [ln for ln in sem_bloco.split("\n")
                  if not ln.strip().startswith(("//", "*"))]
        return "\n".join(linhas)

    # ---------- contrato HTML <-> JS ----------

    def test_the_card_and_the_shortcut_carry_the_ids_the_script_looks_for(self):
        """O atalho e o card tem os ids que o JS procura -- os DOIS.

        Sem o `id` no atalho, `$('#publicar-link-origem')` devolve null,
        `mostrarOrigem` esconde so' o card e ninguem percebe: o atalho continua
        na tela apontando para uma ancora que sumiu. E' a falha silenciosa que
        o teste de comportamento nao pega sozinho, porque ele monta o proprio
        DOM falso.
        """
        html = self._html()
        js = self._js()
        self.assertIn('id="publicar-origem"', html)
        self.assertIn('id="publicar-link-origem"', html)
        self.assertIn("'#publicar-origem'", js)
        self.assertIn("'#publicar-link-origem'", js)

    def test_the_shortcut_points_at_the_card_it_hides_with(self):
        """O par casa: `href="#publicar-origem"` e `id="publicar-origem"`.

        Um atalho apontando para outra ancora esconderia o card errado -- e o
        card certo ficaria visivel, que e' o defeito de volta.
        """
        self.assertIn('href="#publicar-origem"', self._nav())
        self.assertIn('id="publicar-origem"', self._html())

    def test_the_card_visibility_is_decided_in_one_place(self):
        """Cada seletor aparece UMA vez no codigo: a decisao tem uma fonte.

        Era este o defeito: o `render` reexibia o card (`hidden = false`) por
        conta propria, enquanto o `mostrarFalha` o escondia. Duas decisoes sobre
        o mesmo `hidden`, e a divergencia so' aparecia em uma das leituras.
        """
        js = self.sem_comentario(self._js())
        for seletor in ("#publicar-origem", "#publicar-link-origem"):
            with self.subTest(seletor=seletor):
                self.assertEqual(
                    js.count(seletor), 1,
                    f"{seletor} aparece mais de uma vez no publicar.js: ha uma "
                    "segunda decisao sobre o `hidden`, fora do `mostrarOrigem`")

    # ---------- comportamento, com o JS de verdade ----------

    def _executa(self, expressao: str):
        """Roda `expressao` no escopo do `publicar.js`, com um DOM falso.

        Nao ha copia da logica: as funcoes sao extraidas do arquivo real. Uma
        copia passaria com a copia certa e o produto quebrado -- que foi
        exatamente o defeito que a pagina teve antes, quando o HTML estava certo
        e o JS desalinhado.
        """
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node nao esta no PATH")

        ancoras = [
            r"function segundos\(valor\) \{[\s\S]*?\n  \}",
            r"function nota\(valor\) \{[\s\S]*?\n  \}",
            r"function legendaDe\(clip\) \{[\s\S]*?\n  \}",
            r"function fichaHtml\(data\) \{[\s\S]*?\n  \}",
            r"function mostrarOrigem\(visivel\) \{[\s\S]*?\n  \}",
            r"function mostrarFalha\(msg\) \{[\s\S]*?\n  \}",
            r"function render\(data\) \{[\s\S]*?\n  \}",
        ]
        fonte = self._js()
        corpo = []
        for padrao in ancoras:
            achado = re.search(padrao, fonte)
            if achado is None:
                raise AssertionError(f"ancora ausente em publicar.js: {padrao}")
            corpo.append(achado.group(0))

        script = (
            # Cada seletor vira um elemento de mentira, criado na hora: o
            # `hidden` que ele guarda e' o que o teste le de volta.
            "const els = {};\n"
            "const novo = () => ({ hidden: null, innerHTML: '', textContent: '' });\n"
            "const document = {\n"
            "  querySelector: (s) => (s in els ? els[s] : (els[s] = novo())),\n"
            "  addEventListener: () => {},\n"
            "  createElement: () => novo(),\n"
            "};\n"
            "const $ = (s) => document.querySelector(s);\n"
            "const window = { esc: (v) => String(v == null ? '' : v) };\n"
            "const esc = window.esc;\n"
            # `render` guarda os clips lidos num `let` do modulo; sem ele o
            # `render` estoura com ReferenceError e o teste mediria o erro.
            "let clipsLidos = [];\n"
            + "\n".join(corpo) + "\n"
            "console.log(JSON.stringify(" + expressao + "));\n"
        )
        proc = subprocess.run([node, "-e", script], capture_output=True,
                              text=True, timeout=30)
        if proc.returncode != 0:
            linhas = [l.strip() for l in proc.stderr.splitlines() if l.strip()]
            erro = next((l for l in linhas if re.match(r"^[A-Za-z]*Error\b", l)),
                        linhas[0] if linhas else "sem stderr")
            raise AssertionError("publicar.js estourou: " + erro)
        return json.loads(proc.stdout.strip())

    def test_a_reading_without_a_report_hides_both(self):
        """`exists === false`: sem ficha, o card E o atalho saem da tela.

        Era o defeito medido: o card sumia (o `mostrarFalha` ja o escondia) e o
        atalho ficava, apontando para uma secao que nao existe mais.
        """
        estado = self._executa(
            "(render({exists: false, clips: []}), " + self.ESTADO + ")")
        self.assertTrue(estado["card"],
                        "o card da Origem ficou visivel sem relatorio")
        self.assertTrue(estado["atalho"],
                        "o atalho da Origem ficou visivel sem relatorio")

    def test_a_reading_with_a_report_shows_both(self):
        """`exists === true`: a ficha existe, entao o card e o atalho voltam.

        O estado do DOM sobrevive ao erro: sem esta volta, uma leitura que
        falhou deixaria a Origem escondida para sempre.
        """
        estado = self._executa(
            "(render({exists: true, clips: []}), " + self.ESTADO + ")")
        self.assertFalse(estado["card"],
                         "o card da Origem nao voltou com o relatorio presente")
        self.assertFalse(estado["atalho"],
                         "o atalho da Origem nao voltou com o relatorio presente")

    def test_a_dead_server_hides_both(self):
        """`mostrarFalha`: sem servidor nao ha o que ler, entao some tudo.

        Mesmo par do caminho sem relatorio: o erro nao pode divergir dele.
        """
        estado = self._executa("(mostrarFalha('x'), " + self.ESTADO + ")")
        self.assertTrue(estado["card"],
                        "o card da Origem ficou visivel com o servidor fora")
        self.assertTrue(estado["atalho"],
                        "o atalho da Origem ficou visivel com o servidor fora")

    def test_the_two_are_always_on_the_same_side(self):
        """A chamada direta: um booleano, dois elementos, sempre iguais.

        Prova que o par nao tem estado proprio -- e' o que permite os dois
        caminhos (`render` e `mostrarFalha`) delegarem para ela sem repetir a
        regra.
        """
        ligado = self._executa("(mostrarOrigem(true), " + self.ESTADO + ")")
        desligado = self._executa("(mostrarOrigem(false), " + self.ESTADO + ")")
        self.assertEqual(ligado, {"card": False, "atalho": False})
        self.assertEqual(desligado, {"card": True, "atalho": True})


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
        """O card da transcricao vive em Ajustes, e nao mais no Estudio.

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
        onde o rail vira um hamburguer e a Biblioteca fica a dois toques.

        O rodape passou a ser montado por /comum.js e a estar nas TRES paginas.
        A navegacao dele DERIVA de RAIL_PAGES, entao os destinos do rodape nao
        podem mais divergir dos do rail -- era justamente o que os tres ids
        escritos a mao no HTML permitiam.
        """
        html = self.html()
        self.assertIn('class="site-footer"', html)
        self.assertIn("data-footer", html)
        comum = (server.WEB_DIR / "comum.js").read_text(encoding="utf-8")
        self.assertIn('class="footer-nav"', comum)
        self.assertIn("RAIL_PAGES.map(", comum)
        self.assertIn('data-footer-action="docs"', comum)

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
        # A lista incluye as QUATRO páginas. Faltava a ajustes.html, e era por
        # isso que ela ainda carregava <link> do Google: o teste passava sem
        # olhar para ela. Sobrava um preconnect no <head> que o proprio CSP
        # (`font-src 'self'`) bloqueia em produção — ou seja, o custo era pago
        # e o resultado nunca chegava.
        for name in ("index.html", "scrap.html", "ajustes.html", "publicar.html"):
            html = (server.WEB_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("fonts.googleapis.com", html)
            self.assertNotIn("fonts.gstatic.com", html)
            # E nenhuma pagina pode reescrever a familia: a @font-face do
            # compartilhado e a unica fonte de verdade da interface.
            self.assertNotIn("fonts.googleapis", html)
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
        for name in ("index.html", "ajustes.html", "scrap.html", "publicar.html"):
            html = (server.WEB_DIR / name).read_text(encoding="utf-8")
            self.assertIn('rel="icon" href="/favicon.svg"', html)

    def test_anchors_clear_the_sticky_header(self):
        # scrollIntoView/#config e o foco do #url rolam o elemento ate a borda
        # top da viewport: sem scroll-margin o titulo para POR BAIXO da barra
        # de 68px. O card "Escolha" do scrap nao precisa mais desta protecao --
        # ele deixou de ser fixo em 2026-10-07 e hoje rola junto com a pagina,
        # entao nao ha mais como ele parar atras da barra.
        shared = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")
        self.assertIn("scroll-margin-top: 84px", shared)

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


class PickScrollsWithThePageTests(unittest.TestCase):
    """O card "Escolha" rola junto com as demais secoes -- ele NAO e' fixo.

    Ele foi `sticky` (top: 80px) ate 2026-10-07, quando o Sr. Sena pediu o
    contrario: "ao mover a pagina para baixo a secao move junto, verifique e
    corrija a secao, deixando-a igual as outras". Vale registrar que este card
    ja tinha consumido duas sessoes tentando CONSERTAR o sticky, sem que
    ninguem tivesse perguntado se ele era desejado. A pergunta certa e' a
    primeira: o usuario quer este efeito?

    O que este teste trava e' o que sobra DEPOIS da remocao:

    * nenhuma regra do `.pick` declara `position` -- nem `sticky` (o efeito) nem
      `static` (o desligamento dele, que vira orfao);
    * o piso 0 da grade de 3 colunas FICA: ele nao tem nada a ver com o sticky,
      conserta um transbordo horizontal real de 1280-1373 e nao pode sair junto;
    * o `header` continua fixo -- a remocao e' do card, nao da barra.
    """

    def css(self) -> str:
        return (server.WEB_DIR / "scrap.css").read_text(encoding="utf-8")

    @staticmethod
    def sem_comentario(css: str) -> str:
        """Tira os comentarios antes de procurar codigo AUSENTE.

        Os comentarios que explicam a remocao CITAM o `sticky` que saiu (e o
        `minmax(340px)` que motivou o piso 0). Sem esta limpeza, o teste da
        remocao reprova por causa da propria explicacao dela.
        """
        return re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    @staticmethod
    def bloco(css: str, condicao: str) -> str:
        """O corpo de um `@media (condicao) { ... }`, com as chaves casadas.

        As regras do card vivem em quatro blocos (base, >=1280, 981-1279 e
        <=980) e a ultima vence. Procurar a declaracao no arquivo inteiro nao
        distingue "esta ligada" de "esta desligada logo abaixo" -- foi por um
        `@media` esquecido que o sticky virou `static` sem ninguem notar.
        """
        i = css.index("@media " + condicao)
        j = css.index("{", i)
        nivel = 0
        for k in range(j, len(css)):
            if css[k] == "{":
                nivel += 1
            elif css[k] == "}":
                nivel -= 1
                if nivel == 0:
                    return css[j + 1:k]
        raise AssertionError(f"@media {condicao} sem fechamento")

    def test_no_rule_positions_the_pick(self):
        """Nenhuma regra do `.pick` posiciona o card: nem sticky, nem static.

        O `static` nao e' inofensivo -- ele era o DESLIGAMENTO do sticky nas
        viewports estreitas. Deixado para tras, ele faz o leitor achar que ha um
        efeito para desligar, e a proxima pessoa que mexer nao sabe se pode
        apagar.
        """
        codigo = self.sem_comentario(self.css())
        for seletor, corpo in re.findall(r"([^{}]+)\{([^}]*)\}", codigo):
            if not re.search(r"\.pick(?![-\w])", seletor):
                continue
            with self.subTest(seletor=seletor.strip()):
                self.assertNotIn("position", corpo,
                                 f"{seletor.strip()} voltou a posicionar o card")

    def test_the_wide_columns_have_no_hard_floor(self):
        """Piso fixo somava 1030px num container de 972px: a grade vazava.

        Nada a ver com o sticky -- o conserto veio junto e FICA.
        """
        bloco = self.sem_comentario(self.bloco(self.css(), "(min-width: 1280px)"))
        self.assertNotIn("minmax(340px", bloco)
        self.assertIn("minmax(0, 1.05fr)", bloco)

    def test_the_removal_did_not_touch_the_header(self):
        """A barra de navegacao segue fixa: a remocao foi do card, so'."""
        shared = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")
        self.assertIn("position: sticky; top: 0", shared)

    def test_the_stacked_layout_is_not_sticky(self):
        """Empilhado o card tem 611px no celular: fixo, cobriria a tela toda."""
        bloco = self.bloco(self.css(), "(max-width: 980px)")
        self.assertIn("position: static", bloco)


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
        # O `hidden` e procurado DENTRO da tag, e nao como substring da linha
        # toda. A forma anterior exigia a sequencia exata
        # `class="backend-note" id="backend-state" hidden`, o que reprovava o
        # elemento por causa da ORDEM dos atributos — e ordem de atributo nao
        # significa nada em HTML. O elemento real tem `role` e `aria-live`
        # entre o id e o `hidden`, que e a forma correta de uma regiao viva que
        # comeca escondida. O que o teste quer garantir e que ela nasca oculta.
        tag = re.search(r'<p class="backend-note"[^>]*\bid="backend-state"[^>]*>', html)
        self.assertIsNotNone(tag, "a nota de backend nao existe")
        self.assertIn(
            "hidden", tag.group(0),
            "a nota de backend precisa nascer OCULTA: numa pagina funcionando "
            "o painel nao pode carregar uma linha de aviso a toa, senao um aviso "
            "que sempre aparece deixa de informar")
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

    def test_every_entrypoint_calls_the_guard(self):
        """A guarda fica no topo de do_GET, do_PUT e do_POST -- nao dentro de uma rota.

        Uma checagem por rota e uma que a proxima rota esquece; foi assim que
        a pagina de Ajustes pôde nascer sem H1 e ninguem notou.

        O `do_PUT` entrou depois e ficou de fora deste assert: os dois verbos
        originais eram cobertos, o terceiro so' existia por leitura do codigo.
        Um verbo novo sem guarda e' uma porta a mais para uma pagina de fora
        escrever `ajustes.toml` e o prompt do curador.
        """
        import inspect

        for nome in ("do_GET", "do_PUT", "do_POST"):
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
    """A secao Renderizacao vive so em /ajustes; o Estudio apenas a le.

    Antes as duas paginas desenhavam os mesmos 11 controles, cada uma com a sua
    copia do markup. Duas copias do mesmo formulario divergem: a do index.html
    ficou com o layout antigo (font-size no primeiro triple) e com o id errado
    do loudness (`lufs`, que o servidor so aceitava por um alias de
    compatibilidade). O index.js lia os 11 campos do DOM e mandava no POST /run.

    Agora o Estudio le state.ajustes, carregado de /ajustes.json no boot -- o
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
        # O outro lado: tirar do Estudio sem ter na Ajustes apagaria o controle
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

    def test_the_raw_endpoint_body_is_shown_when_there_is_one(self):
        """A `reason` resume; o corpo cru prova.

        Sem ele, um 403 do Groq ("error code: 1010" do Cloudflare) fica so com a
        frase generica e o usuario nao tem como ver o que o provedor respondeu.
        """
        mostrar = fn_body(self.js, "showProviderResult")
        self.assertIn("data.detail", mostrar,
                      "o painel descarta o corpo cru da resposta de erro")
        css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")
        self.assertIn(".prov-detalhe", css, "sem estilo, o detalhe nao aparece")

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
        fonte = (server.REPO_ROOT / "web" / "routes_providers.py").read_text(encoding="utf-8")
        corpo = fonte.split("def _handle_save_provider", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("if name in providers.PROVIDERS:", corpo,
                      "o save nao recusa sobrescrever um provedor de fabrica")
        self.assertIn("400", corpo, "a colisao nao e recusada")

    def test_the_probe_route_reports_a_missing_key_without_calling_out(self):
        """Sem a variavel no ambiente, o servidor responde ANTES de sair na rede.

        Mandar a chamada sem chave daria um 401 que o usuario teria de decodificar,
        quando a causa (a variavel nao esta exportada) e conhecida aqui.
        """
        fonte = (server.REPO_ROOT / "web" / "routes_providers.py").read_text(encoding="utf-8")
        corpo = fonte.split("def _handle_test_provider", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("no-key", corpo)


class BodyGridLayoutTests(unittest.TestCase):
    """O corpo do Estudio: a grade 2x2 que ESTICA, sem deixar vao.

    Historico das mudancas de forma:

    1. Era UMA coluna (788px) com tudo empilhado mais um aside de 340px so com
       o prompt — a coluna curta ficava ~1350px mais baixa e o vazio aparecia.
    2. Virou TRES colunas iguais; a do meio (Curador 605 + Provedores 886 =
       1537px) ficou ~870px mais alta que a do Prompt (667px).
    3. Virou uma grade 2x2: `fonte | exec` na linha 1 e `curador | prompt` na
       linha 2.
    4. TRES pedidos do Sr. Sena em 2026-10-07, na ordem em que chegaram:
       (a) o Prompt PROXIMO da Execucao; (b) o Curador ABAIXO DO FONTE e
       alinhado ao Prompt; (c) "nao deixe nenhum espaco vazio na pagina".
       (a) e (b) foram perseguidos mexendo nas areas — o Fonte passou a cobrir
       duas faixas, o Prompt esticou por duas, o Curador desceu para a largura
       toda. Cada arranjo matava um vao e abria outro: no ultimo, a coluna da
       ESQUERDA terminava em 2029px e a da direita em 1118px — 911px de buraco
       embaixo do Prompt, que era exatamente a reclamacao de (c).
       A solucao nao estava nas areas e sim em `align-items`: a grade volta a
       ser a 2x2 SIMPLES (que ja atendia (a) e (b) de uma vez) e ESTICA as
       celulas. As duas colunas terminam na mesma linha.

    O que este teste trava NAO e a largura (isso e medicao de navegador, feita
    no `estudio_grid.js`) e sim a ARVORE, a ORDEM e as duas regras de CSS que
    sustentam o "sem vao": `align-items: stretch` e a corrente de `flex: 1` da
    celula ate o card. Trocar o aninhamento muda o layout em silencio — o
    navegador conserta HTML malformado e uma `grid-area` errada nao da erro.
    """

    # As quatro areas, na ordem em que o pedido as quer no documento.
    CELULAS = ("studio-cell-fonte", "studio-cell-exec",
               "studio-cell-curador", "studio-cell-prompt")

    @classmethod
    def setUpClass(cls):
        cls.html = page_source("index.html")
        cls.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    def _sem_comentarios(self):
        """O HTML sem os comentarios `<!-- … -->`.

        Eles citam markup e quebrariam a contagem de `</div>`; tirar antes de
        contar e a base de todo o resto.
        """
        return re.sub(r"<!--.*?-->", "", self.html, flags=re.S)

    def _bloco_de(self, classe, corpo=None):
        """O `<div class="{classe}">` inteiro, do abre ao fecha que o casa.

        `.*?` nao serve: o primeiro `</div>` que aparece ja nao e o do no. Aqui
        acha-se o abre pela classe, conta-se a profundidade `<div>`/`</div>` ate
        voltar a zero e devolve-se o recorte fechado — o unico jeito honesto de
        saber onde um no aninhado termina.
        """
        corpo = self._sem_comentarios() if corpo is None else corpo
        alvo = f'class="{classe}"'
        if alvo not in corpo:
            raise AssertionError(f"o bloco `{alvo}` sumiu do index.html")
        i = corpo.index(alvo)
        ini = corpo.rindex("<div", 0, i)
        depth = 0
        for m in re.finditer(r"<div\b|</div>", corpo[ini:]):
            depth += 1 if m.group(0) == "<div" else -1
            if depth == 0:
                return corpo[ini:ini + m.end()]
        raise AssertionError(f"o bloco `{alvo}` nunca fecha")

    def _itens_do_grid(self):
        """Os filhos diretos do `.main-grid`, em ordem.

        O `<form>` conta como item porque nao e `<div>` (a varredura de
        profundidade so ve `<div>`/`</div>`); as QUATRO celulas vivem dentro
        dele e sobem para o grid via `display: contents`, entao a lista de
        filhos do grid na arvore HTML inclui o form e NAO as celulas — que e
        exatamente o que este teste precisa distinguir.
        """
        bloco = self._bloco_de("main-grid")
        inner = bloco[bloco.index(">") + 1:bloco.rindex("</div>")]
        itens, depth = [], 0
        for linha in inner.split("\n"):
            faixa = linha.strip()
            abre_div = len(re.findall(r"<div\b", linha))
            antes = depth
            depth += abre_div - len(re.findall(r"</div>", linha))
            if antes == 0 and (abre_div or faixa.startswith("<form")):
                itens.append(faixa)
        return itens

    # ---------------------------------------------------------------- arvore

    def test_the_cells_are_the_four_expected_ones(self):
        """Cada celula existe UMA vez — a base de tudo o resto neste teste."""
        for celula in self.CELULAS:
            with self.subTest(celula=celula):
                self.assertEqual(
                    self.html.count(f'class="studio-cell {celula}"'), 1,
                    f"a celula `{celula}` nao aparece exatamente 1 vez")

    def test_fonte_and_execucao_are_two_sibling_cells(self):
        """Fonte e Execucao sao celulas IRMAS: o par entrada/saida.

        Empilha-las devolve o problema antigo: preencher os campos e rolar a
        pagina para achar o botao. Irmas, o CSS as poem lado a lado.
        """
        fonte = self._bloco_de("studio-cell studio-cell-fonte")
        exec_ = self._bloco_de("studio-cell studio-cell-exec")
        self.assertIn("card-fonte", fonte)
        self.assertIn("execution-card", exec_)
        # uma nao contem a outra
        self.assertNotIn("execution-card", fonte)
        self.assertNotIn("card-fonte", exec_)

    def test_the_curador_cell_carries_the_curador_and_the_providers(self):
        """Curador e Meus provedores na MESMA celula.

        O Curador ESCOLHE o provedor; o card de provedores e onde um que ainda
        nao existe passa a existir. Juntos, a escolha e a origem da lista ficam
        a um olhar de distancia.
        """
        cel = self._bloco_de("studio-cell studio-cell-curador")
        self.assertIn("Curador com IA", cel)
        self.assertIn('id="card-meus-provedores"', cel)

    def test_the_prompt_is_its_own_cell(self):
        """O prompt e celula propria, separado do Curador na arvore.

        Na tela ele divide a faixa de baixo com o Curador (area `prompt`); na
        arvore e' separado para nao herdar a altura dele nem a da Execucao — e',
        ao contrario, a celula que ABSORVE a sobra da faixa pelo textarea.
        """
        cel = self._bloco_de("studio-cell studio-cell-prompt")
        self.assertIn("prompt-card", cel)
        self.assertNotIn("Curador com IA", cel)
        self.assertNotIn("card-meus-provedores", cel)

    def test_the_execution_card_is_not_in_the_curador_cell(self):
        """A Execucao pertence a linha 1 (par do Fonte), nao a do Curador.

        O `.studio-col` antigo embrulhava Execucao + Curador juntos; separar os
        dois foi o que abriu a coluna nova. Se o Execucao voltar para dentro da
        celula do Curador, o par entrada/saida se desfaz.
        """
        cel = self._bloco_de("studio-cell studio-cell-curador")
        self.assertNotIn("execution-card", cel)

    def test_the_four_cells_follow_the_declared_order_in_the_document(self):
        """A ordem no documento e fonte, exec, curador, prompt.

        A ordem importa em duas frentes: e a ordem de leitura para teclado e
        leitor de tela, e e a ordem que a coluna unica usa ao empilhar — abaixo
        de 1100px ela e a UNICA coisa que posiciona as celulas.

        A coluna unica e' o espelho da grade: `fonte exec` na faixa de cima e
        `curador prompt` na de baixo viram fonte, exec, curador, prompt. Trocar
        a ordem no documento sem trocar as areas (ou o contrario) faz a tela
        estreita contar uma historia diferente da larga.
        """
        posicoes = [self.html.index(f'class="studio-cell {c}"') for c in self.CELULAS]
        self.assertEqual(
            posicoes, sorted(posicoes),
            f"as celulas sairam de ordem: {list(zip(self.CELULAS, posicoes))}")

    def test_the_cells_live_inside_the_form(self):
        """As quatro celulas ficam DENTRO do `<form id="clip-form">`.

        Os campos de cada card precisam estar no form para o botao "Gerar
        cortes" os ler. Com `display: contents` o form nao tem caixa, mas a
        arvore continua mandando: fora do form, o payload perde os campos.
        """
        corpo = self._sem_comentarios()
        ini = corpo.index('<form id="clip-form"')
        fim = corpo.index("</form>", ini)
        for celula in self.CELULAS:
            with self.subTest(celula=celula):
                pos = corpo.index(f'class="studio-cell {celula}"')
                self.assertTrue(
                    ini < pos < fim,
                    f"a celula `{celula}` ficou fora do form")

    def test_the_head_sits_above_the_cells(self):
        """A cabeca (overline + titulo + instrucao) vem ANTES das celulas."""
        corpo = self._sem_comentarios()
        cabeca = corpo.index('class="main-head"')
        for celula in self.CELULAS:
            with self.subTest(celula=celula):
                self.assertLess(
                    cabeca, corpo.index(f'class="studio-cell {celula}"'),
                    f"`{celula}` aparece antes da cabeca .main-head")

    # ------------------------------------------------------------------- css

    def _regra_base(self):
        """O corpo da regra `.main-grid` da GRADE (fora de `@media`).

        `.main-grid` aparece mais de uma vez no arquivo: a primeira so tem
        `display`/`gap`, e a grade do `@media` tambem declara areas. A regra
        base e a que tem DUAS colunas `minmax(0, 1fr)` — e a unica assinatura
        que so ela carrega.
        """
        regras = [
            corpo for corpo in re.findall(r"\.main-grid\s*\{([^}]*)\}", self.css, re.S)
            if "grid-template-areas" in corpo
            and corpo.count("minmax(0, 1fr)") == 2
        ]
        self.assertEqual(
            len(regras), 1,
            f"esperava UMA regra base .main-grid (2 tracks); achei {len(regras)}")
        return regras[0]

    def test_the_base_rule_declares_two_tracks(self):
        """A grade base tem DOIS tracks `minmax(0, 1fr)`.

        O `minmax(0, …)` e obrigatorio: filho de grid nunca encolhe abaixo do
        conteudo sem ele, e um `<select>` mais largo que a coluna empurraria o
        track alem da tela.
        """
        colunas = re.search(
            r"grid-template-columns:\s*([^;]+);", self._regra_base()
        ).group(1)
        self.assertEqual(colunas.count("minmax(0,"), 2, colunas)
        self.assertNotRegex(colunas, r"(?<!minmax\(0,\s)\b1fr\b",
                            f"track com `1fr` solto: {colunas}")

    def test_the_areas_are_the_plain_2x2_with_the_curador_under_the_fonte(self):
        """As areas sao `fonte exec` / `curador prompt` — a 2x2 simples.

        Ela atende os TRES pedidos do Sr. Sena de 2026-10-07 de uma vez, e e' por
        isso que voltou a ser esta:

        1. O Prompt PROXIMO da Execucao: esta na coluna da direita, e a Execucao
           e' o card de cima dessa mesma coluna.
        2. O Curador ABAIXO DO FONTE e ALINHADO ao Prompt: os dois dividem a
           faixa de baixo, Curador na coluna 1 e Prompt na coluna 2.
        3. "Nao deixe nenhum espaco vazio": com `align-items: stretch` (o teste
           vizinho) as duas colunas terminam na MESMA linha.

        O que nao se pode voltar a fazer e' esticar uma celula por DUAS faixas
        (o `"fonte exec" / "fonte prompt" / "curador prompt"` tentado mais cedo
        hoje): isso poe o Fonte com 668px numa area de 2007px e devolve 1339px de
        vao embaixo dele. Cada celula ocupa UMA faixa.
        """
        areas = re.search(
            r"grid-template-areas:\s*((?:\s*\"[^\"]+\")+)", self._regra_base()
        )
        self.assertIsNotNone(areas, "as grid-template-areas do .main-grid sumiram")
        # normaliza o espaco interno: alinhar as colunas no texto e so estetica
        linhas = [" ".join(l.split()) for l in re.findall(r'"([^"]+)"', areas.group(1))]
        self.assertEqual(
            linhas,
            ["cabeca cabeca", "fonte exec", "curador prompt"],
            f"as areas do corpo mudaram: {linhas}")
        # Todas as faixas de corpo tem DUAS colunas: nenhuma celula estica.
        for faixa in linhas[1:]:
            with self.subTest(faixa=faixa):
                self.assertEqual(
                    len(faixa.split()), 2,
                    f"a faixa `{faixa}` deixou de ter duas colunas — uma celula "
                    "esta esticando e vai sobrar vao na outra")
        # O Curador na COLUNA 1 da faixa de baixo: e o que o poe sob o Fonte.
        self.assertEqual(
            linhas[-1].split()[0], "curador",
            "o Curador saiu da coluna da esquerda e nao fica mais sob o Fonte")
        # O Prompt na COLUNA 2 da mesma faixa: alinhado ao Curador e sob a
        # Execucao, que ocupa a coluna 2 da faixa de cima.
        self.assertEqual(
            linhas[-1].split()[1], "prompt",
            "o Prompt saiu da coluna da direita e nao fica mais sob a Execucao")

    def test_the_base_rule_stretches_the_cells_instead_of_leaving_a_hole(self):
        """A grade ESTICA as celulas (`align-items: stretch`).

        E' o que mata o vao, e a razao de a 2x2 ter voltado. Medido em 1440px com
        `align-items: start`: a coluna da esquerda terminava em 2029px e a da
        direita em 1118px — 911px de buraco embaixo do Prompt. Com `stretch` as
        duas terminam na mesma linha (2599px, medido).

        `start` e' exatamente o valor que este teste existe para impedir de
        voltar: ele parece inofensivo e devolve o buraco inteiro.
        """
        self.assertRegex(
            self._regra_base(), r"align-items:\s*stretch\s*;",
            "a grade perdeu o `align-items: stretch` e o vao volta")
        self.assertNotRegex(
            self._regra_base(), r"align-items:\s*start\s*;",
            "a grade voltou para `align-items: start` — 911px de vao")

    def test_every_cell_is_a_flex_column_whose_card_fills_it(self):
        """A celula leva a altura esticada ATE' o card (`flex: 1`).

        Sem isto o `stretch` do grid estica a celula e o card continua com a
        altura do proprio conteudo — o mesmo vao, so que com um `<div>` no meio.
        O `margin-bottom: 0` mata os 14px que o `.card` traz e que sobrariam no
        pe' de cada celula.
        """
        self.assertRegex(
            self.css,
            r"\.studio-cell\s*\{[^}]*display:\s*flex\s*;"
            r"[^}]*flex-direction:\s*column\s*;",
            "a `.studio-cell` deixou de ser coluna flex")
        self.assertRegex(
            self.css,
            r"\.studio-cell\s*>\s*\.card\s*\{[^}]*flex:\s*1[^;]*;"
            r"[^}]*margin-bottom:\s*0\s*;",
            "o card deixou de preencher a celula (ou voltou a margem de 14px)")

    def test_the_slack_lands_where_it_can_be_used(self):
        """A sobra vai para onde ela SERVE, nao para um vao.

        Medido em 1440px, coluna de 564px: a Execucao tem 472px numa faixa de
        668px, e o Prompt 618px numa faixa de 1319px. Os dois destinos:

        - Execucao: as acoes descem para o rodape do card (`margin-top: auto`),
          em vez de sobrar vao embaixo dos botoes.
        - Prompt: o textarea cresce. E' o card certo para isso — o arquivo
          `prompts/curador.txt` tem 349 linhas, entao editor alto e' USO, nao
          enfeite. A corrente e' card > form-section > field > textarea, e o
          teste cobra os quatro elos: quebrar um so ja faz o textarea voltar ao
          tamanho natural e o vao reaparecer.
        """
        self.assertRegex(
            self.css,
            r"\.execution-card\s+\.execution-actions\s*\{\s*margin-top:\s*auto\s*;",
            "as acoes da Execucao perderam o `margin-top: auto`")
        for seletor in (
            r"\.prompt-card\s*\{\s*display:\s*flex\s*;",
            r"\.prompt-card\s+\.form-section\s*\{[^}]*flex:\s*1[^;]*;",
            r"\.prompt-card\s+\.form-section\s*>\s*\.field:first-child\s*\{"
            r"[^}]*flex:\s*1[^;]*;",
            r"\.prompt-card\s+\.form-section\s*>\s*\.field:first-child\s+textarea\s*\{"
            r"[^}]*flex:\s*1[^;]*;",
        ):
            with self.subTest(seletor=seletor):
                self.assertRegex(
                    self.css, seletor,
                    f"elo quebrado na corrente do Prompt: {seletor}")

    def test_every_cell_has_its_area_declared(self):
        """Cada `.studio-cell-*` recebe uma `grid-area` com o proprio nome.

        Celula sem `grid-area` cai no auto-placement e vai parar em qualquer
        faixa livre — foi o que jogou a cabeca para o rodape numa tentativa.
        """
        for celula in self.CELULAS:
            area = celula.replace("studio-cell-", "")
            with self.subTest(celula=celula):
                self.assertRegex(
                    self.css,
                    rf"\.{celula}\s*\{{\s*grid-area:\s*{area}\s*;",
                    f"a `{celula}` nao declara `grid-area: {area}`")

    def test_the_form_dissolves_into_the_grid(self):
        """O form vira `display: contents`.

        Sem isso ele seria UM item do grid e as quatro celulas ficariam
        empilhadas dentro dele — o grid nunca as veria, e nao haveria grade.
        """
        self.assertRegex(
            self.css,
            r"\.main-grid\s*>\s*form#clip-form\s*\{\s*display:\s*contents\s*;",
            "o form perdeu o `display: contents` e o grid nao ve as celulas")

    def test_the_narrow_breakpoint_stacks_the_cells(self):
        """Abaixo de 1100px a grade cai para UMA coluna.

        Duas colunas em ~1000px apertam o Fonte, que tem `.field-row.triple`
        (Pasta, Quantidade, Download) e quebra feio. O corte tem de existir, e
        tem de trocar as AREAS junto com as colunas — trocar so os tracks
        deixaria as areas antigas posicionando as celulas.
        """
        # Ha varios `@media (max-width: 1100px)` no arquivo; o do corpo e o que
        # mexe no `.main-grid`. Filtrar pelo conteudo, nao pela posicao.
        faixas = re.findall(
            r"@media \(max-width: 1100px\)\s*\{((?:[^{}]|\{[^}]*\})*)\n\}",
            self.css, re.S)
        corpo = next((f for f in faixas if ".main-grid" in f), None)
        self.assertIsNotNone(corpo, "o @media (max-width: 1100px) do .main-grid sumiu")
        self.assertRegex(
            corpo,
            r"grid-template-columns:\s*minmax\(0, 1fr\)\s*;",
            "o @media de 1100px nao colapsou para uma coluna")
        self.assertRegex(corpo, r"grid-template-areas:",
                         "o @media de 1100px trocou as colunas mas nao as areas")

    def test_no_other_rule_overrides_the_columns(self):
        """Nenhuma outra regra `.main-grid` declara colunas fora do padrao.

        Havia uma segunda regra de colunas num breakpoint antigo (1160px) que
        sobrevivia e reescrevia os tracks por baixo da grade nova. Declarar
        colunas em dois lugares sem declarar as AREAS junto quebra o layout.

        A regra de Ajustes e ESCOPADA (`[data-page="ajustes"] .main-grid`) e nao
        entra nesta conta: ela nao disputa especificidade com a grade do Estudio
        — so a propria pagina de Ajustes a recebe. Aqui contam apenas as regras
        SEM escopo, que valem para o Estudio.
        """
        # Tira as regras escopadas antes de contar. Filtrar pelo escopo e mais
        # seguro que um lookbehind, que erra quando a regra vem indentada dentro
        # de um `@media` (o caso do breakpoint de 1280px).
        sem_escopo = re.sub(
            r'\[data-page="[^"]+"\]\s+\.main-grid[^{]*\{[^}]*\}', "", self.css)
        blocos = re.findall(r"\.main-grid[^{]*\{([^}]*)\}", sem_escopo, re.S)
        com_colunas = [b for b in blocos if "grid-template-columns" in b]
        self.assertEqual(
            len(com_colunas), 2,
            "o numero de regras de `.main-grid` SEM escopo com "
            f"`grid-template-columns` mudou ({len(com_colunas)}); confira se "
            "sobrou breakpoint antigo")
        # A grade de Ajustes tem a propria declaracao escopada; o contrato dela
        # (as QUATRO colunas iguais, a area de cada card) vive em
        # `AjustesTrioTests`, que e a classe dona desta pagina.


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
            routes_providers.USER_PROVIDERS_PATH,
            (server.REPO_ROOT / user_providers.USERS_PATH).resolve()
            if not Path(user_providers.USERS_PATH).is_absolute()
            else Path(user_providers.USERS_PATH).resolve(),
        )

    def test_the_module_default_now_points_at_the_server_file(self):
        """A mesclagem passa pelo modulo, entao ele tem de ler o mesmo arquivo."""
        from viralclipper import user_providers

        self.assertEqual(Path(user_providers.USERS_PATH).resolve(),
                         routes_providers.USER_PROVIDERS_PATH)

    def test_the_store_file_is_not_committed(self):
        """É dado do usuario, nao config do projeto: nao pode aparecer no git."""
        gitignore = server.REPO_ROOT / ".gitignore"
        if not gitignore.is_file():
            self.skipTest("sem .gitignore")
        texto = gitignore.read_text(encoding="utf-8")
        self.assertIn("provedores-usuario.toml", texto,
                      "o arquivo do usuario nao esta no gitignore")


class ManualApiKeyTests(unittest.TestCase):
    """A chave colada no card, sem depender de exportar uma variavel.

    ``api_key_env`` guarda o NOME de uma variavel: certo para quem exporta uma
    vez no perfil, errado para quem so quer colar a chave e clicar em testar. O
    caminho novo tem quatro propriedades que, se perderem, quebram em silencio:

    1. a chave colada satisfaz ``requires_key`` sozinha (sem ``api_key_env``);
    2. a chave e gravada e lida de volta do TOML do usuario;
    3. a chave NAO volta para o navegador — a lista manda so ``has_key``;
    4. a chave chega ao run via ambiente, e uma variavel ja exportada vence.
    """

    def setUp(self):
        import tempfile

        from viralclipper import user_providers

        self.user_providers = user_providers
        self.tmp = Path(tempfile.mkdtemp()) / "provedores-usuario.toml"
        # Redireciona o arquivo do modulo E a constante do modulo de rotas (o
        # dono do nome desde a extracao): duas constantes para o mesmo arquivo e
        # a armadilha que a UserProviderStoreTests ja documenta.
        self._mod_path = user_providers.USERS_PATH
        self._srv_path = routes_providers.USER_PROVIDERS_PATH
        user_providers.USERS_PATH = self.tmp
        routes_providers.USER_PROVIDERS_PATH = self.tmp

    def tearDown(self):
        self.user_providers.USERS_PATH = self._mod_path
        routes_providers.USER_PROVIDERS_PATH = self._srv_path

    def _entrada(self, **over):
        base = {
            "name": "meu-endpoint", "label": "Meu", "model": "m",
            "base_url": "https://api.exemplo.com/v1", "note": "nota",
            "requires_key": True,
        }
        base.update(over)
        return base

    # ------------------------------------------------------------- o validador

    def test_a_pasted_key_satisfies_requires_key_on_its_own(self):
        """Sem ``api_key_env``, a chave colada ja e uma chave."""
        prov, motivo = self.user_providers.validate(
            self._entrada(api_key="sk-cole-aqui")
        )
        self.assertEqual(motivo, "")
        self.assertIsNotNone(prov)
        self.assertEqual(prov.api_key, "sk-cole-aqui")
        self.assertEqual(prov.api_key_env, "")

    def test_requires_key_with_neither_channel_is_refused(self):
        """Sem variavel E sem chave, o run nao teria o que enviar."""
        prov, motivo = self.user_providers.validate(self._entrada())
        self.assertIsNone(prov)
        self.assertIn("chave", motivo)

    def test_a_blank_key_is_absence_not_an_empty_secret(self):
        """``api_key=""`` e ``api_key="   "`` significam "nao dei chave".

        Tratar espaço como chave enviaria um Authorization vazio e o 401 nao
        diria de onde veio.
        """
        prov, motivo = self.user_providers.validate(self._entrada(api_key="   "))
        self.assertIsNone(prov)
        self.assertIn("chave", motivo)

    # --------------------------------------------------------------- o arquivo

    def test_the_key_round_trips_through_the_toml(self):
        prov, _ = self.user_providers.validate(self._entrada(api_key="sk-secreta"))
        self.user_providers.save([prov], self.tmp)
        voltou = self.user_providers.load(self.tmp)
        self.assertEqual(len(voltou), 1)
        self.assertEqual(voltou[0].api_key, "sk-secreta")

    def test_a_provider_without_a_key_writes_no_key_line(self):
        """Nao poluir o TOML de quem nunca usou o campo.

        Uma linha ``api_key = ""`` em todo provedor obrigaria o leitor a saber
        que vazio significa ausente.
        """
        prov, _ = self.user_providers.validate(
            self._entrada(name="local-x", api_key="", api_key_env="",
                          requires_key=False, base_url="http://127.0.0.1:1/v1")
        )
        texto = self.user_providers.dump([prov])
        self.assertNotIn("api_key = ", texto)
        self.assertIn('api_key_env = ""', texto)

    # ------------------------------------------------------------- o navegador

    def test_the_payload_never_ships_the_literal_key(self):
        """A lista do painel diz SE ha chave, nunca QUAL e.

        Devolver o segredo poria a chave no DOM e no historico do devtools a
        cada recarregamento, sem ganho: o campo so precisa saber que existe uma.
        """
        prov, _ = self.user_providers.validate(self._entrada(api_key="sk-secreta"))
        self.user_providers.save([prov], self.tmp)
        payload = routes_providers._providers_payload()
        meu = next(p for p in payload["providers"] if p["name"] == "meu-endpoint")
        self.assertNotIn("api_key", meu, "a chave vazou para o navegador")
        self.assertTrue(meu["has_key"])
        self.assertNotIn("sk-secreta", json.dumps(payload),
                         "o literal da chave apareceu em algum campo do payload")

    def test_an_edit_that_does_not_retype_the_key_keeps_it(self):
        """Abrir o provedor para corrigir a nota nao pode apagar o segredo.

        O painel nunca recebe a chave de volta, entao um save de edicao manda
        ``api_key=""``; isso tem de ser "nao mexi", nao "apague".
        """
        prov, _ = self.user_providers.validate(self._entrada(api_key="sk-secreta"))
        self.user_providers.save([prov], self.tmp)
        handler = object.__new__(server.Handler)
        enviado: dict = {}
        handler._send_json = lambda payload, code=200: enviado.update(
            payload, _code=code)
        # Edicao que so muda a nota: sem api_key no payload.
        handler._handle_save_provider(
            {"provider": self._entrada(note="nota nova", api_key="")}
        )
        self.assertEqual(enviado.get("_code"), 200, enviado.get("error"))
        voltou = self.user_providers.load(self.tmp)
        self.assertEqual(voltou[0].note, "nota nova")
        self.assertEqual(voltou[0].api_key, "sk-secreta",
                         "a edicao apagou a chave salva")

    # ------------------------------------------------------------------- o run

    def test_the_saved_key_reaches_the_run_environment(self):
        """``build_provider`` le do ambiente; e onde a chave colada entra."""
        prov, _ = self.user_providers.validate(self._entrada(api_key="sk-do-painel"))
        self.user_providers.save([prov], self.tmp)
        self.assertNotIn("MEU_KEY", os.environ)
        cfg = ClipConfig(url="x", ranker_provider="meu-endpoint",
                         ranker_api_key_env="MEU_KEY")
        var = server._export_saved_key(cfg)
        self.addCleanup(os.environ.pop, "MEU_KEY", None)
        self.assertEqual(var, "MEU_KEY")
        self.assertEqual(os.environ["MEU_KEY"], "sk-do-painel")

    def test_an_exported_variable_wins_over_a_pasted_key(self):
        """A maquina que tem o segredo de verdade nao pode ser ofuscada."""
        prov, _ = self.user_providers.validate(self._entrada(api_key="sk-do-painel"))
        self.user_providers.save([prov], self.tmp)
        os.environ["MEU_KEY"] = "sk-do-ambiente"
        self.addCleanup(os.environ.pop, "MEU_KEY", None)
        cfg = ClipConfig(url="x", ranker_provider="meu-endpoint",
                         ranker_api_key_env="MEU_KEY")
        self.assertIsNone(server._export_saved_key(cfg))
        self.assertEqual(os.environ["MEU_KEY"], "sk-do-ambiente")

    def test_a_provider_that_needs_no_key_publishes_nothing(self):
        """Endpoint local: publicar uma chave ali seria efeito colateral."""
        prov, _ = self.user_providers.validate(
            self._entrada(name="local-y", api_key="qualquer", requires_key=False,
                          base_url="http://127.0.0.1:1/v1")
        )
        self.user_providers.save([prov], self.tmp)
        self.assertNotIn("MEU_KEY", os.environ)
        cfg = ClipConfig(url="x", ranker_provider="local-y",
                         ranker_api_key_env="MEU_KEY")
        self.assertIsNone(server._export_saved_key(cfg))
        self.assertNotIn("MEU_KEY", os.environ)

    # --------------------------------------------------------------- a interface

    def test_the_key_field_and_its_reveal_button_exist(self):
        html = page_source("index.html")
        self.assertIn('id="prov-api-key"', html)
        self.assertIn('type="password"', html)
        self.assertIn('id="btn-prov-key-reveal"', html)

    def test_the_reveal_button_is_wired_to_something(self):
        """Um botao no HTML sem listener e um controle que nao faz nada.

        Custou um ciclo: o campo e o botao existiam, o clique nao mudava nada, e
        o E2E pegou (o `type` continuava `password`).
        """
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        self.assertIn("#btn-prov-key-reveal", js)
        self.assertIn("setProviderKeyVisible", js)
        corpo = fn_body(js, "setProviderKeyVisible")
        self.assertIn("password", corpo)
        self.assertIn("type", corpo)

    def test_the_key_field_follows_the_requires_key_toggle(self):
        """Campo que so vale sob condicao tem de desabilitar sob a condicao."""
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        corpo = fn_body(js, "syncProviderKeyField")
        self.assertIn("#prov-api-key", corpo)
        self.assertIn("disabled", corpo)
        self.assertIn("prov-requires-key", js,
                      "o interruptor nao chama o sincronizador do campo")

    def test_the_js_reads_the_key_field_but_never_writes_it_back(self):
        """Ler o campo e escrever o valor salvo nele sao coisas diferentes.

        O segundo poria o segredo no DOM apos um reload — o que o payload
        justamente evita ao mandar so ``has_key``.
        """
        js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        corpo = fn_body(js, "fillProviderForm")
        self.assertIn("has_key", corpo, "a edicao nao avisa que ja existe chave")
        self.assertIn("#prov-api-key').value = ''", corpo,
                      "o formulario repoe a chave salva no campo")


class AjustesTrioTests(unittest.TestCase):
    """Transcricao, Renderizacao e Prompt dividem a largura da pagina /ajustes.

    Sao os tres passos que produzem o corte — o texto que entra, o visual que
    sai e o criterio que escolhe os trechos — e por isso ficam lado a lado. O
    grid da pagina tem QUATRO colunas iguais: o trio mais o Resumo. A Selecao
    ocupa a faixa de cima, na largura toda.

    O que este teste trava e a ARVORE, as AREAS do grid e as regras CSS de
    contencao — nao a largura (isso e medicao de navegador, feita no
    `probe_p.js`): uma grade sem as regras de contencao deixa `.field-row.triple`
    em tres colunas de ~80px e os rotulos quebram — o navegador nao reclama, so
    fica feio.
    """

    #: Os tres cards que formam o trio, na ordem em que aparecem.
    CARDS = ("Transcrição", "Renderização", "Prompt do curador")

    #: As quatro areas do grid, na ordem da segunda faixa.
    AREAS = ("trans", "rend", "prompt", "resumo")

    @classmethod
    def setUpClass(cls):
        cls.html = (server.WEB_DIR / "ajustes.html").read_text(encoding="utf-8")
        cls.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    def _sem_comentarios_css(self):
        return re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)

    def _trecho_trio(self):
        """O recorte do `.trio`, do abre ao `<!-- /.trio -->`."""
        if 'class="trio"' not in self.html:
            raise AssertionError("o `.trio` sumiu de ajustes.html")
        return self.html[self.html.index('class="trio"'):self.html.index("<!-- /.trio -->")]

    def test_the_three_cards_are_inside_the_trio(self):
        trecho = self._trecho_trio()
        for card in self.CARDS:
            self.assertIn(card, trecho, f"o card {card} saiu do trio lado a lado")

    def test_the_card_order_is_transcricao_renderizacao_prompt(self):
        """A ordem importa: o texto entra, o render sai, o prompt decide."""
        trecho = self._trecho_trio()
        self.assertLess(trecho.index("Transcrição"), trecho.index("Renderização"))
        self.assertLess(trecho.index("Renderização"), trecho.index("Prompt do curador"))

    def test_the_trio_opens_and_closes_exactly_once(self):
        self.assertEqual(self.html.count('class="trio"'), 1,
                         "o `.trio` foi duplicado")
        self.assertEqual(self.html.count("<!-- /.trio -->"), 1,
                         "o marcador de fim do `.trio` sumiu ou duplicou")

    def test_the_resumo_is_the_fourth_column(self):
        """O Resumo deixou de ser um aside lateral: e a 4a coluna do grid.

        Ele tem de continuar DENTRO do `.main-grid` (como irmao direto) e ser
        depois do trio — senao nao entra na grade.
        """
        fim_trio = self.html.index("<!-- /.trio -->")
        fim_grid = self.html.index("</div>", self.html.index("</aside>"))
        self.assertIn("Resumo", self.html[fim_trio:fim_grid],
                      "o Resumo nao esta depois do trio, dentro do grid")

    def test_the_prompt_does_not_swallow_what_comes_after_it(self):
        """A `</form>` fecha o trio: nada de card de fora entrar por engano."""
        fim = self.html.index("<!-- /.trio -->")
        self.assertIn("</form>", self.html[fim:],
                      "o trio nao fecha antes do form")

    def test_the_balance_of_divs_is_intact(self):
        """A arvore fechou: -1 em qualquer ponto = `</div>` sobrando.

        O navegador conserta HTML malformado em silencio e a tela parece certa,
        entao a contagem e a unica prova barata de que o wrapper nao desalinhou
        o aninhamento. `<form>` nao entra na conta (nao e `<div>`).
        """
        corpo = re.sub(r"<!--.*?-->", "", self.html, flags=re.S)
        depth = 0
        for token in re.findall(r"<div\b|</div>", corpo):
            depth += 1 if token == "<div" else -1
            self.assertGreaterEqual(depth, 0, "`</div>` a mais — arvore quebrada")
        self.assertEqual(depth, 0, "sobrou `<div>` sem fechar")

    def test_the_form_has_no_box_of_its_own(self):
        """`display: contents` sobe os cards ao grid; sem ele tudo empilha."""
        css = self._sem_comentarios_css()
        self.assertRegex(
            css,
            r'\[data-page="ajustes"\]\s+\.main-grid\s*>\s*form#ajustes-form\s*\{\s*display:\s*contents',
            "o form de Ajustes perdeu o `display: contents`",
        )

    def test_the_css_declares_four_equal_columns(self):
        css = self._sem_comentarios_css()
        self.assertRegex(
            css,
            r'\[data-page="ajustes"\]\s+\.main-grid\s*\{[^}]*'
            r"grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)",
            "o grid de Ajustes perdeu as quatro colunas iguais",
        )

    def test_every_area_has_an_owner(self):
        """Cada area declarada tem um seletor que a ocupa."""
        css = self._sem_comentarios_css()
        for area in self.AREAS:
            self.assertRegex(
                css,
                r"\[data-page=\"ajustes\"\][^{]*\{[^}]*grid-area:\s*" + area,
                f"a area `{area}` ficou sem dono",
            )

    def test_the_css_containers_the_field_rows_inside_the_narrow_card(self):
        """Sem isto o `.triple` fica com ~80px por coluna e o rotulo quebra.

        O par de regras e o que faz o card de ~272px funcionar: uma coluna por
        linha dentro do trio, e a volta do `.triple` quando ele empilha.
        """
        sem_comentarios = self._sem_comentarios_css()
        self.assertRegex(
            sem_comentarios,
            r"\.trio\s+\.field-row[^{]*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)",
            "os `.field-row` dentro do `.trio` nao foram para uma coluna",
        )
        self.assertRegex(
            sem_comentarios,
            r"\.trio\s+\.field-row\.triple\s*\{[^}]*repeat\(3,",
            "o `.triple` nao volta a tres colunas quando o trio empilha",
        )

    def test_the_css_stacks_below_the_breakpoint(self):
        """Abaixo do limite a grade volta a uma coluna, senao os cards apertam."""
        sem_comentarios = self._sem_comentarios_css()
        i = sem_comentarios.find("@media (max-width: 1280px)")
        self.assertNotEqual(i, -1, "o `@media` que empilha o grid sumiu")
        bloco = sem_comentarios[i:i + 600]
        self.assertRegex(
            bloco,
            r'\[data-page="ajustes"\]\s+\.main-grid\s*\{[^}]*'
            r"grid-template-columns:\s*minmax\(0,\s*1fr\)",
            "o grid de Ajustes nao empilha no breakpoint",
        )


class QueueCardTests(unittest.TestCase):
    """O card da fila fala o estado do job — e so ele.

    O markup e GERADO em `index.js` (`renderQueue`), nao vive no `index.html`.
    Por isso o contrato que vale e o par (classe de estado no card + regra que
    pinta aquele estado). A cor nunca e escrita pelo JS: o JS poe a CLASSE e o
    CSS decide. Se alguem trocar o `className` por um `style` inline, o card
    fica com a cor de um estado so e este teste cai.
    """

    @classmethod
    def setUpClass(cls):
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        cls.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    @staticmethod
    def _sem_comentarios_css(css):
        return re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    def _bloco(self, seletor):
        """Devolve o corpo da PRIMEIRA regra cujo seletor bate ao inicio da linha.

        Ancora no seletor completo + `{`, e nao em `[^}]*<prop>:`, porque essa
        forma casa tambem a regra dentro de um `@media` com a mesma cabeca e
        mascararia a corrupcao da regra base (foi assim que a sabotagem passou
        duas vezes no grid do corpo).
        """
        sem = self._sem_comentarios_css(self.css)
        achado = re.search(
            r"^" + re.escape(seletor) + r"\s*\{([^}]*)\}", sem, re.M
        )
        self.assertIsNotNone(achado, "a regra `%s` sumiu do index.css" % seletor)
        return achado.group(1)

    def test_the_running_state_is_a_class_not_an_inline_style(self):
        """O estado mora na classe do card; a cor fica no CSS."""
        self.assertRegex(
            self.js,
            r"card\.className\s*=\s*'job-card '\s*\+\s*\(job\.status",
            "o card deixou de carregar o status como classe",
        )
        # Um `style.background`/`style.borderColor` no card significaria cor
        # decidida no JS: o `fail` e o `running` divergiriam na mao.
        bloco = self.js[self.js.index("function renderQueue"):]
        bloco = bloco[:bloco.index("\n  }")] if "\n  }" in bloco else bloco
        self.assertNotRegex(
            bloco,
            r"card\.style\.|\.style\.background",
            "o JS voltou a pintar o card em vez de usar a classe de estado",
        )

    def test_every_state_has_its_own_border(self):
        """`running`, `done` e `fail` tem de dar bordas DIFERENTES.

        Se as tres cairem no mesmo token, o card perde a leitura de relance e
        o usuario tem de ler o texto do badge para saber o que aconteceu.
        """
        bordas = {}
        for estado, token in (
            ("running", "--accent-primary"),
            ("done", "--success"),
            ("fail", "--danger"),
        ):
            corpo = self._bloco(".job-card." + estado)
            self.assertIn(
                token, corpo,
                "o card `%s` nao usa o token de cor do seu estado" % estado,
            )
            bordas[estado] = token
        self.assertEqual(len(set(bordas.values())), 3, "os tres estados colorem igual")

    def test_the_badge_pulses_only_while_running(self):
        """O ponto do badge so anima no `running` — pulsar parado vira ruido."""
        # A pulsacao vive no PONTO (`::before`), nao na caixa do badge: a caixa
        # so carrega cor. Olhar a regra base daria falso negativo.
        corpo = self._bloco(".job-badge.running::before")
        self.assertIn("animation:", corpo, "o badge do job rodando parou de pulsar")
        self.assertIn("render-live", corpo, "a pulsacao deixou de reusar o keyframe")
        # O badge do `done` nao pode herdar a animacao por acidente: nem na
        # caixa, nem no ponto (que pode nem existir como regra propria — o
        # importante e que, se existir, nao anime).
        self.assertNotIn("animation:", self._bloco(".job-badge.done"))
        ponto_done = re.search(
            r"^\.job-badge\.done::before\s*\{([^}]*)\}",
            self._sem_comentarios_css(self.css), re.M,
        )
        if ponto_done is not None:
            self.assertNotIn("animation:", ponto_done.group(1))

    def test_the_progress_bar_stays_honest(self):
        """Rodando = indeterminado; concluido/falhou = 100% e sem animacao.

        O backend nao manda percentual, entao o cliente NUNCA inventa um. A
        barra que anda sozinha e a varredura (`queue-progress`), e ela para
        quando o job resolve — senao um card concluido continuaria "trabalhando".
        """
        rodando = self._bloco(".job-card.running .job-progress-fill")
        self.assertIn("queue-progress", rodando)
        completo = self._bloco(".job-progress.complete .job-progress-fill")
        self.assertIn("width: 100%", completo)
        self.assertIn("animation: none", completo)
        falhou = self._bloco(".job-progress.failed .job-progress-fill")
        self.assertIn("width: 100%", falhou)
        self.assertIn("animation: none", falhou)
        # `idle` (na fila) nao pode mostrar progresso nenhum: herdar os 38% da
        # base fazia um card parado parecer ter andado.
        parado = self._bloco(".job-progress.idle .job-progress-fill")
        self.assertRegex(parado, r"width:\s*0\b")
        self.assertIn("animation: none", parado)
        self.assertIn(
            "'idle'", self.js,
            "o card da fila deixou de receber o estado `idle`",
        )
        # Nenhum setter de largura em porcentagem no JS: nao ha progresso falso.
        # A busca e frouxa DE PROPOSITO: `fill.style.width = 62 + '%'` escapa de
        # um padrao que so aceite o digito colado no `%`.
        bloco = self.js[self.js.index("function renderQueue"):]
        bloco = bloco[:bloco.index("\n  }")]
        self.assertNotRegex(
            bloco,
            r"style\.width\s*=",
            "o JS passou a inventar uma largura de progresso",
        )

    def test_the_render_stage_only_shows_while_there_is_no_preview(self):
        """A faixa "EM PRODUCAO" e do vazio: com previa pronta ela nao entra."""
        self.assertIn("job-render-stage", self.js)
        self.assertIn("EM PRODUÇÃO", self.js)
        self.assertRegex(
            self.js,
            r"if\s*\(!\(job\.previewFiles \|\| \[\]\)\.length && job\.status === 'running'\)",
            "a faixa de producao deixou de ser condicional a ausencia de previa",
        )

    def test_the_scan_line_covers_the_card_without_capturing_clicks(self):
        """O brilho da varredura e decorativo: nao intercepta o clique da previa."""
        corpo = self._bloco(".job-card.running .job-poster::after")
        self.assertIn("pointer-events: none", corpo)

    def test_the_reduced_motion_branch_kills_every_animation(self):
        """Quem pediu menos movimento nao pode levar varredura nem pulso."""
        sem = self._sem_comentarios_css(self.css)
        # Ha MAIS DE UM `@media (prefers-reduced-motion)` no arquivo (utilitarios,
        # skeleton, fila). Ancorar no PRIMEIRO pega o dos utilitarios e o teste
        # passa sem nunca olhar a fila — por isso o alvo e o bloco que cita `.job-`.
        blocos = [
            m.start() for m in re.finditer(r"@media \(prefers-reduced-motion: reduce\)", sem)
        ]
        alvo = None
        for inicio in blocos:
            fim = sem.find("\n}", inicio)
            if ".job-" in sem[inicio:fim]:
                alvo = sem[inicio:fim]
                break
        self.assertIsNotNone(alvo, "a fila perdeu o ramo de movimento reduzido")
        for seletor in (
            ".job-card.running .job-progress-fill",
            ".job-card.running .job-poster::after",
            ".job-badge.running::before",
        ):
            self.assertIn(seletor, alvo, "`%s` segue animando com movimento reduzido" % seletor)
        # E os que aparecem tem de estar desligados, nao so listados.
        self.assertRegex(alvo, r"\.job-render-glow[\s\S]{0,160}?animation: none")
        self.assertRegex(
            alvo,
            r"\.job-card\.running \.job-poster::after\s*\{[^}]*animation: none",
            "a varredura do poster continua ligada com movimento reduzido",
        )

    def test_the_card_keeps_three_columns_until_the_first_breakpoint(self):
        """Poster | conteudo | previas. O card empilha por medida, nao por chute."""
        corpo = self._bloco(".job-card")
        self.assertRegex(
            corpo,
            r"grid-template-columns:\s*112px\s+minmax\(0,\s*1fr\)\s+minmax\(\d+px,\s*\d+px\)",
            "as tres colunas do card mudaram de forma inesperada",
        )
        # Nos dois breakpoints as colunas tem de reduzir, nao sumir.
        sem = self._sem_comentarios_css(self.css)
        self.assertRegex(sem, r"@media \(max-width: 760px\)[\s\S]{0,400}?\.job-card\s*\{")
        self.assertRegex(sem, r"@media \(max-width: 420px\)[\s\S]{0,400}?\.job-card\s*\{")


class JobLifecycleTests(unittest.TestCase):
    """A fila nao pode acumular fantasma nem mentir sobre a etapa.

    Medido ao vivo em 2026-10-05: `/status` devolvia **8 jobs identicos** em
    `running` para a mesma URL, com **1 so** realmente rodando no
    `/run/progress`. Os outros sete eram threads mortas que nunca virariam
    `done`. E o card mostrava `small` (o modelo de transcricao) no lugar da
    etapa, porque o job nascia sem `progress` e o front caia no `meta`.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = Path(server.__file__).read_text(encoding="utf-8")
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")

    def setUp(self):
        # O modulo guarda estado global; um teste limpa o que o outro sujou.
        self._anterior = list(server._state["jobs"])

    def tearDown(self):
        server._state["jobs"] = self._anterior

    def test_the_job_is_born_with_a_stage_not_the_model_name(self):
        """`progress` nasce com a 1a etapa; `meta` guarda o modelo, nao a fase.

        O card escolhe o texto com `job.progress || job.meta`. Sem `progress`
        ele mostra o `meta`, que e `"small"` — o nome do modelo de transcricao
        aparecendo como se fosse a etapa do job.
        """
        self.assertIn('"progress": _RUN_STAGES[0][1]', self.src)
        # E o `meta` continua sendo o modelo (a informacao nao se perdeu).
        self.assertIn('"meta": ("plan-only · " if plan_only else "")', self.src)

    def test_starting_a_run_drops_the_ghosts(self):
        """Um run novo poda os `running` orfaos: sem isso a lista so cresce.

        So os terminais sobrevivem — eles tem resultado a mostrar. Os que
        ficaram presos em `running` (thread morta) sao descartados.
        """
        with server._lock:
            server._state["jobs"] = [
                {"url": "u1", "status": "running"},   # fantasma
                {"url": "u2", "status": "running"},   # fantasma
                {"url": "u3", "status": "done", "title": "ok"},
                {"url": "u4", "status": "fail", "meta": "erro"},
            ]
        # Reproduz a poda que `_run_job` faz ao registrar um job novo.
        with server._lock:
            server._state["jobs"] = [
                j for j in server._state["jobs"]
                if j.get("status") in ("done", "fail")
            ]
            restantes = [j["url"] for j in server._state["jobs"]]
        self.assertEqual(restantes, ["u3", "u4"], "os fantasmas nao foram podados")
        # E a poda tem de estar NO codigo, nao so no teste.
        self.assertRegex(
            self.src,
            r'_state\["jobs"\] = \[\s*j for j in _state\["jobs"\]\s*if j\.get\("status"\) in \("done", "fail"\)',
            "o `_run_job` deixou de podar os jobs terminais",
        )

    def test_the_stage_writer_feeds_the_card_too(self):
        """`_mark_stage` atualiza o job ativo, senao o card trava na 1a etapa."""
        inicio = self.src.index("def _mark_stage")
        corpo = self.src[inicio:self.src.index("\ndef ", inicio + 10)]
        self.assertIn('ativo["progress"] = _RUN_STAGES[index][1]', corpo,
                      "a etapa publicada nao chega ao card da fila")

    def test_the_merge_does_not_resurrect_an_unknown_running_job(self):
        """O front nao re-anexa um `running` que o servidor nao devolveu.

        Re-anexar era o que empilhava um card por poll quando o `/status`
        parava de trazer o job.
        """
        self.assertIn("job.localOnly", self.js)
        self.assertIn("r.jobs.some((remoto) => remoto.url === job.url)", self.js)
        # A linha antiga, incondicional, nao pode voltar.
        self.assertNotIn("if (job.status === 'running' && !used.has(index)) merged.push(job)", self.js)

    def test_a_fresh_job_is_kept_until_the_server_publishes_it(self):
        """O job recem-criado sobrevive ao primeiro merge (`localOnly`)."""
        self.assertRegex(self.js, r"localOnly:\s*true")
        self.assertRegex(self.js, r"delete job\.localOnly",
                         "a marca de 'so local' nunca e removida")


class QueueChromeTests(unittest.TestCase):
    """Resumo, filtros e estado vazio contextual da fila.

    O que a pagina promete ao usuario: quantos jobs ha em cada estado, um
    filtro por estado com a contagem, e uma frase propria quando o filtro
    esconde a fila inteira. Tudo isso ancorado no que o JS realmente pinta.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        cls.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    def test_the_summary_has_one_box_per_state(self):
        self.assertIn('id="queue-summary"', self.html)
        for key in ("processing", "queued", "done", "fail"):
            with self.subTest(estado=key):
                self.assertIn(f'data-stat="{key}"', self.html)
                self.assertIn(f'id="stat-{key}"', self.html)

    def test_the_filters_cover_every_state_plus_all(self):
        for key in ("all", "processing", "queued", "done", "fail"):
            with self.subTest(filtro=key):
                self.assertIn(f'data-filter="{key}"', self.html)
        # O ativo e declarado pelo `aria-pressed`, nao por uma classe: o mesmo
        # atributo serve ao CSS e ao leitor de tela, entao nao divergem.
        self.assertIn('aria-pressed="true"', self.html)
        self.assertIn('aria-pressed="false"', self.html)

    def test_one_classifier_decides_both_the_count_and_the_filter(self):
        """Contador e filtro usam a MESMA funcao — senao discordam.

        Se cada um tivesse a sua tabela de status, um job `canceled` cairia
        num grupo para o contador e em nenhum para o filtro (a lista mostraria
        menos itens do que o numero ao lado do botao).
        """
        self.assertIn("function queueGroup(job)", self.js)
        self.assertIn("function queueCounts(jobs)", self.js)
        self.assertRegex(self.js, r"counts\[queueGroup\(job\)\] \+= 1")
        self.assertRegex(self.js, r"queueGroup\(job\) === filtro")

    def test_the_order_is_stable_and_puts_active_first(self):
        """Ativo, fila, resultado, falha — e estavel dentro do grupo."""
        self.assertRegex(self.js, r"QUEUE_ORDER = \{\s*processing: 0,\s*queued: 1,\s*done: 2,\s*fail: 3")
        # O desempate por indice e o que mantem a ordem ao re-renderizar.
        self.assertRegex(self.js, r"return d !== 0 \? d : a\.index - b\.index")

    def test_an_empty_filter_says_which_one(self):
        """Filtro sem itens nao pode se passar pela fila vazia."""
        self.assertIn('Nenhum job neste filtro', self.js)
        self.assertIn('Nenhum job na fila ainda', self.js)

    def test_the_filter_is_kept_in_state_not_in_the_dom(self):
        """O render recria a lista a cada tick; a escolha tem de sobreviver."""
        self.assertRegex(self.js, r"queueFilter:\s*'all'")
        self.assertRegex(self.js, r"state\.queueFilter = escolha")

    def test_the_summary_shrinks_before_it_scrolls(self):
        """Em 375px o resumo rola DENTRO do container, nunca a pagina.

        `max-content` porque o rotulo em caixa alta nao quebra (quebrar no
        meio da palavra era o defeito visivel). O scroll fica delimitado.
        """
        sem = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        bloco = sem[sem.index("@media (max-width: 420px)"):]
        bloco = bloco[:bloco.index("\n}")]
        self.assertIn(".queue-summary", bloco)
        self.assertRegex(bloco, r"overflow-x:\s*auto")
        self.assertRegex(bloco, r"grid-template-columns:\s*repeat\(4,\s*max-content\)")
        # E o rotulo nao pode voltar a quebrar no meio da palavra. A checagem
        # roda no CSS SEM COMENTARIOS: o proprio comentario explica o defeito
        # citando a propriedade, e ler o comentario daria falso positivo.
        limpo = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        inicio = limpo.index(".queue-stat-label")
        self.assertNotIn("overflow-wrap: anywhere", limpo[inicio:inicio + 200])

    def test_a_filter_with_no_items_is_dimmed_but_still_clickable(self):
        """Apagar um filtro sem itens e visual, nao funcional."""
        self.assertIn(".queue-filter.is-empty", self.css)
        self.assertRegex(self.css, r"\.queue-filter\.is-empty\s*\{[^}]*opacity:")
        self.assertNotIn("pointer-events: none", self.css[self.css.index(".queue-filter.is-empty"):self.css.index(".queue-filter.is-empty") + 120])

    def test_the_list_is_a_live_region(self):
        """Mudanca de estado e anunciada, sem roubar o foco."""
        self.assertRegex(self.html, r'id="queue-list"[^>]*aria-live="polite"')
        # Sem `aria-atomic`: so o trecho que muda e lido, nao a lista toda.
        self.assertNotIn('aria-live="assertive"', self.html)

    def test_the_touch_target_grows_on_a_coarse_pointer(self):
        """44px e o minimo da WCAG 2.2 — mas so onde o dedo e o apontador."""
        self.assertRegex(
            self.css,
            r"@media \(pointer: coarse\)\s*\{\s*\.queue-filter\s*\{[^}]*min-height:\s*44px",
        )


class JobResultSummaryTests(unittest.TestCase):
    """O que um card CONCLUIDO mostra — e so quando ha dado real.

    `finish()` grava `title`, `clips` (ate 3) e `meta` no job. Nada disso existe
    enquanto ele roda. O contrato aqui e: o resumo dos cortes nasce do `job.clips`
    que o servidor mandou, nunca de um numero que o front inventa.
    """

    @classmethod
    def setUpClass(cls):
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        cls.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")
        cls.server = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")

    def test_the_summary_reads_the_clips_the_server_sent(self):
        """A fonte e `job.clips` — nao uma contagem derivada de outro campo."""
        self.assertIn("const clipsDoJob = Array.isArray(job.clips) ? job.clips : []", self.js)
        # A classe e posta pelo `className` (sem o ponto — o ponto e do seletor
        # CSS). Procurar `.job-clips` no JS daria falso negativo.
        self.assertRegex(self.js, r"resumo\.className = 'job-clips'")
        self.assertIn(".job-clips", self.css)

    def test_the_summary_only_exists_for_a_finished_job_with_clips(self):
        """Job em andamento nao pode exibir um resumo vazio."""
        self.assertRegex(
            self.js,
            r"if \(job\.status === 'done' && clipsDoJob\.length\)",
        )
        # E o container so entra no card se tiver filho: sem isso um job `fail`
        # ganharia uma faixa `job-clips` de altura zero, mas com margem.
        self.assertRegex(self.js, r"if \(resumo\.children\.length\) content\.append")

    def test_each_chip_carries_the_real_duration(self):
        """A duracao vem do renderizador, nao de um valor de exemplo."""
        self.assertRegex(self.js, r"queueClock\(clip\.duration\)")
        self.assertIn("String(clipIndex + 1).padStart(2, '0')", self.js)

    def test_a_plan_only_clip_is_not_dressed_as_a_watchable_one(self):
        """`rendered:false` = corte pontuado sem arquivo. O chip tem de dizer."""
        self.assertRegex(self.js, r"clip\.rendered === false \? ' · só análise'")
        self.assertIn(".job-clip.is-plan", self.css)
        self.assertRegex(self.css, r"\.job-clip\.is-plan\s*\{[^}]*border-style:\s*dashed")

    def test_the_finished_job_keeps_its_own_clock(self):
        """A escada e zerada no proximo run; o job tem de guardar o total.

        Sem `job["elapsed"]` gravado no `finish`, a duracao de um job concluido
        desaparecia no instante em que o usuario disparava o video seguinte.
        """
        self.assertRegex(self.server, r'job\["started_at"\] = started')
        self.assertRegex(self.server, r'job\["elapsed"\] = final')
        # O lado do card le `job.elapsed`, o mesmo campo que `followRun` alimenta.
        self.assertRegex(self.js, r"job\.elapsed \? queueClock\(job\.elapsed\)")

    def test_the_summary_wraps_instead_of_clipping_a_chip(self):
        """Tres chips mais a contagem nao cabem em 375px — quebrar, nao cortar."""
        ini = self.css.index(".job-clips ")
        bloco = self.css[ini:ini + 900]
        self.assertRegex(bloco, r"flex-wrap:\s*wrap")
        self.assertRegex(bloco, r"font-variant-numeric:\s*tabular-nums")
        # Um chip que quebra no meio da duracao fica ilegivel: cada chip e uma
        # unidade. `nowrap` mantem o par "Corte 02 · 00:47" inteiro.
        self.assertRegex(bloco, r"\.job-clip\s*\{[^}]*white-space:\s*nowrap")


class GallerySearchTests(unittest.TestCase):
    """A secao "Clips gerados": contagem viva, busca e bloco vazio dedicado.

    A secao tem tres estados que precisam se distinguir na tela: nada ainda,
    um render em andamento (esqueleto) e resultados — filtrados ou nao. O que
    os testes travam:

    * o vazio e um BLOCO IRMAO da grade, nao um placeholder escrito dentro
      dela (a grade e reescrita pelo poll de /status a cada tique);
    * contagem, grade, bloco vazio e regiao viva saem do MESMO funil, senao
      discordam depois de um filtro;
    * a secao usa os tokens do painel escuro — a paleta clara da referencia
      (`--clips-*`) nao pode vazar para a pagina.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        cls.css = (server.WEB_DIR / "index.css").read_text(encoding="utf-8")

    def test_the_gallery_has_the_new_chrome(self):
        """Eyebrow, titulo, contador e status: a estrutura da secao nova."""
        self.assertIn('class="clips-section"', self.html)
        self.assertIn('class="section-eyebrow"', self.html)
        self.assertIn('id="gallery-title"', self.html)
        self.assertIn('id="gallery-count"', self.html)
        self.assertIn('id="gallery-status"', self.html)
        # O titulo e o `aria-labelledby` da secao — renomear um sem o outro
        # deixa a regiao sem nome acessivel.
        self.assertRegex(self.html, r"clips-section[^>]*aria-labelledby=\"gallery-title\"")

    def test_the_eyebrow_is_the_live_section_heading(self):
        """A eyebrow fica DENTRO do bloco que carrega o titulo.

        Um rotulo solto antes do <h2> le como paragrafo; aqui ele pertence ao
        mesmo grupo, para o leitor de tela anunciar "SEUS RESULTADOS, Clips
        gerados" como uma unidade.
        """
        ini = self.html.index('class="clips-section-heading"')
        bloco = self.html[ini:ini + 400]
        self.assertIn("section-eyebrow", bloco)
        self.assertIn('id="gallery-title"', bloco)

    def test_the_search_box_filters_the_grid(self):
        """O campo existe, tem rotulo e o JS o escuta no `input`."""
        self.assertIn('id="gallery-search"', self.html)
        self.assertRegex(self.html, r'id="gallery-search"[^>]*aria-label=')
        # `input`, e nao `change`: a lista encolhe enquanto se digita.
        self.assertRegex(self.js, r"\$\('#gallery-search'\)\.addEventListener\('input'")
        self.assertRegex(self.js, r"setGalleryQuery\(")

    def test_the_search_matches_title_hook_and_score(self):
        """O termo casa o que o usuario ve no cartao, nao so o id do arquivo."""
        ini = self.js.index("function clipMatchesQuery(")
        bloco = self.js[ini:ini + 400]
        self.assertIn("clip.title", bloco)
        self.assertIn("clip.hook", bloco)
        self.assertIn("clip.score", bloco)
        # Termo vazio casa tudo: o filtro nunca esconde a lista sem que a
        # pessoa tenha digitado algo.
        self.assertRegex(bloco, r"if \(!q\) return true")

    def test_the_live_region_announces_what_the_filter_left(self):
        """Contagem, grade e anuncio saem do mesmo lugar.

        Se a contagem e o bloco vazio fossem calculados em pontos diferentes,
        um filtro poderia mostrar "0 de 3" com a grade ainda cheia — ou o
        contrario.
        """
        self.assertIn("function paintGallery(", self.js)
        self.assertRegex(self.js, r"\$\('#gallery-status'\)")
        self.assertRegex(self.js, r"\$\('#gallery-count'\)")
        # A regiao viva tem papel e modo declarados no HTML, nao no JS: sem
        # `role` o `aria-live` sozinho nao basta em todo leitor.
        self.assertRegex(self.html, r'id="gallery-status"[^>]*role="status"')
        self.assertRegex(self.html, r'id="gallery-status"[^>]*aria-live="polite"')

    def test_every_gallery_render_goes_through_the_one_funnel(self):
        """Nenhum ponto escreve na grade sem passar pelo funil.

        Antes havia quatro: `renderClips`, `renderLibrary`, o esqueleto de
        render e o poll de /status. Cada um tinha a sua ideia de "vazio" — dois
        escondiam o bloco dedicado e pintavam um `empty-state` dentro da grade.
        """
        self.assertEqual(self.js.count("function paintGallery("), 1)
        # Os dois renderizadores de lista chamam o funil com a lista filtrada.
        self.assertRegex(self.js, r"paintGallery\(\s*\n?\s*galleryFilters\(state\.clips")
        self.assertRegex(self.js, r"paintGallery\(\s*\n?\s*galleryFilters\(files")

    def test_the_empty_block_is_a_sibling_not_a_child(self):
        """O vazio e irmao da grade — senao o poll o apaga.

        `#gallery` tem o `innerHTML` trocado a cada tique de /status; um bloco
        vazio dentro dela sobreviveria por um tique e sumiria no seguinte.
        """
        grade = self.html.index('id="gallery"')
        vazio = self.html.index('id="gallery-empty"')
        self.assertGreater(vazio, grade)
        # O bloco nasce escondido (o JS decide quando ele vale) e tem titulo e
        # descricao proprios, para a copy de "nada ainda" e a de "nada
        # encontrado" nao serem a mesma frase.
        self.assertRegex(self.html, r'id="gallery-empty"[^>]*hidden')
        self.assertIn('id="gallery-empty-title"', self.html)
        self.assertIn('id="gallery-empty-description"', self.html)

    def test_the_empty_copy_knows_the_two_kinds_of_empty(self):
        """Vazio por nada gerado != vazio por filtro sem resultado."""
        ini = self.js.index("function paintGallery(")
        bloco = self.js[ini:ini + 2200]
        self.assertRegex(bloco, r"total === 0")
        self.assertRegex(bloco, r"Nada encontrado")
        # A frase do filtro cita o termo digitado e o total, para o usuario
        # entender que a lista existe e so a busca que nao achou.
        self.assertRegex(bloco, r"corresponde a <strong>")

    def test_the_searched_term_cannot_inject_markup(self):
        """O termo digitado entra como texto, nunca como HTML.

        A descricao do vazio e escrita com `innerHTML` (ela carrega <strong> e
        <code>), entao o termo precisa passar por escape antes de entrar.
        """
        self.assertIn("function escapeHtml(", self.js)
        self.assertRegex(self.js, r"escapeHtml\(busca\)")

    def test_the_grid_keeps_the_api_contract_class_names(self):
        """A grade continua `.gallery` com o id `#gallery`.

        O servidor, o CSS antigo e os testes de clip dependem desse par; o
        `clips-grid` da referencia entra como classe ADICIONAL, nao como
        substituta. Por isso as duas classes tem de conviver no mesmo
        atributo — trocar `.gallery` por `.clips-grid` deixaria a grade sem o
        `display: grid` de que ela depende e sem os seletores ja escritos.
        """
        self.assertRegex(
            self.html,
            r'class="clips-grid gallery"[^>]*id="gallery"|id="gallery"[^>]*class="clips-grid gallery"')
        # A regra antiga da grade tem de continuar valendo por si.
        self.assertRegex(self.css, r"\.gallery\s*\{[^}]*display:\s*grid")

    def test_escape_clears_the_field_and_not_only_the_state(self):
        """`Escape` zera o input, nao so o filtro.

        Limpar apenas o estado deixaria o texto digitado na caixa enquanto a
        grade volta inteira — o campo mentiria sobre o que esta mostrando.
        """
        ini = self.js.index("$('#gallery-search').addEventListener('keydown'")
        bloco = self.js[ini:ini + 260]
        self.assertRegex(bloco, r"ev\.key === 'Escape'")
        self.assertRegex(bloco, r"ev\.target\.value = ''")

    def test_the_library_toggle_survives_the_redesign(self):
        """`#btn-library` e `#gallery-sub` continuam existindo e alternando.

        A secao nova trazia um `clips:open-library` solto, sem nada escutando —
        isso perdia a alternancia entre os cortes do job e a pasta de saida.
        Nao basta as duas funcoes existirem no arquivo: o clique tem de
        ESCOLHER entre elas pelo estado do proprio botao.

        O ramo que abre a pasta passa por `openLibrary()` (que emite o evento
        do contrato) e nao por `showLibrary()` direto: assim o clique e um
        emissor externo do evento fazem a mesma coisa por um ponto so.
        """
        self.assertIn('id="btn-library"', self.html)
        self.assertIn('id="gallery-sub"', self.html)
        self.assertRegex(self.js, r"indexOf\('cortes deste job'\)")
        self.assertIn("showJobClips()", self.js)
        self.assertIn("showLibrary()", self.js)
        # O clique ramifica: um caminho volta aos cortes, o outro abre a pasta
        # pelo caminho publico (que emite `clips:open-library`).
        ini = self.js.index("$('#btn-library').addEventListener('click'")
        bloco = self.js[ini:ini + 320]
        self.assertRegex(bloco, r"if \(showingLibrary\)\s*\{\s*showJobClips\(\);\s*\}"
                                r"\s*else\s*\{\s*openLibrary\(\);\s*\}")
        # Cada modo reescreve o proprio rotulo e a propria legenda: sem isso o
        # botao continuaria dizendo "Ver pasta" depois de ja estar na pasta.
        self.assertIn("$('#btn-library').textContent = 'Ver cortes deste job'", self.js)
        self.assertIn("$('#btn-library').textContent = 'Ver pasta de saída'", self.js)

    def test_the_section_uses_the_panel_theme_not_the_reference_palette(self):
        """Nada de `--clips-*` no HTML, e nenhum `:root` novo na secao.

        A referencia abria um `<style>` com a sua propria paleta clara e um
        `:root` dentro da pagina. `:root` e o <html>: aquele bloco nao
        redefinia so a secao, redefinia `--accent-primary` e `--radius-*` para
        a pagina inteira — um cartao branco sobre o painel escuro.
        """
        self.assertNotIn("--clips-", self.html)
        self.assertNotIn("<style>", self.html)
        self.assertNotIn("<script>", self.html)
        # O que a secao pinta sai dos tokens do painel.
        ini = self.css.index(".clips-search-input")
        bloco = self.css[ini:ini + 600]
        self.assertIn("var(--text-primary)", bloco)
        self.assertIn("var(--border-subtle)", bloco)

    def test_the_panel_palette_lives_in_the_shared_sheet(self):
        """A paleta da aplicacao esta no /shared.css, e nowhere mais.

        Este teste ANTES afirmava o contrario: que o `:root` do index.css
        reescrevia a paleta do compartilhado (`#090b10` + `#ff4fae`). Ele
        contradizia o `test_no_token_definition_stayed_behind_in_the_pages`, que
        proibe `--accent-primary` e `--bg-deep` no css da pagina -- os dois
        olhavam o mesmo unico bloco `:root` do index.css e um dos dois tinha de
        falhar sempre.

        A contradicao era o defeito, nao o contrato. Havia duas paletas de verdade:
        o compartilhado carregava a base indigo (#6366f1) e o index a sobrescrevia
        com a do painel (magenta). Como so o index e o ajustes carregam o
        index.css, a Biblioteca ficava indigo enquanto as outras duas ficavam
        magenta -- tres paginas, dois produtos.

        Agora a paleta e unica e mora no compartilhado, e este teste fecha a porta
        para a duplicata voltar.
        """
        # O index.css nao pode mais ter um bloco `:root {` de verdade. A busca e
        # por `:root {` e nao pela palavra solta porque os COMENTARIOS do arquivo
        # citam `:root` ao explicar por que ele saiu -- e um `assertNotIn(":root")`
        # reprovaria o proprio texto que documenta a regra.
        self.assertIsNone(
            re.search(r":root\s*\{", self.css),
            "o index.css voltou a definir :root -- a paleta da aplicacao vive no "
            "/shared.css; duas verdades, e a da pagina manda")
        # E o valor certo precisa estar no compartilhado, nas tres paginas.
        compartilhado = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")
        for cor in ("#090b10", "#ff4fae"):
            with self.subTest(cor=cor):
                self.assertIn(
                    cor, compartilhado,
                    f"{cor} sumiu do /shared.css: a galeria perderia a paleta "
                    "do painel sem nenhuma falha de sintaxe acusar")

    def test_the_reference_palette_is_shimmed_to_the_panel(self):
        """Se a referencia voltar com o <style>, ela nao pinta claro.

        O shim remapeia cada `--clips-*` para o token equivalente do painel.
        `--clips-bg-page` fica de fora de proposito: o fundo da pagina nunca e
        da secao.
        """
        nome = "--clips-bg:"
        self.assertIn(nome, self.css)
        ini = self.css.index(nome)
        bloco = self.css[ini:ini + 700]
        self.assertIn("var(--bg-card)", bloco)
        self.assertIn("--clips-accent: var(--accent-primary)", bloco)
        self.assertNotIn("--clips-bg-page", self.css)

    def test_the_empty_block_really_hides(self):
        """`.clips-empty[hidden]` vence o `display: flex` da regra base.

        Sem essa linha o `hidden` do HTML nao esconde nada num elemento que o
        CSS declara como `display: flex` — o bloco vazio ficaria por cima da
        grade com resultados.
        """
        self.assertRegex(self.css, r"\.clips-empty\[hidden\]\s*\{\s*display:\s*none")

    def test_the_count_is_not_announced_twice(self):
        """O numero vive na regiao viva; o span visivel e `aria-hidden`.

        Sem isso o leitor de tela le "3 cortes" duas vezes — uma do span e uma
        do anuncio.
        """
        self.assertRegex(self.html, r'id="gallery-count"[^>]*aria-hidden="true"')

    def test_every_element_the_js_hides_also_hides_in_the_css(self):
        """O atributo `hidden` tem de vencer o `display` que a regra declara.

        `hidden` so vale display:none enquanto nenhuma outra regra reafirmar o
        `display` — e uma declaracao de autor vence a do user-agent. Estes tres
        elementos ja declaram `flex`/`inline-flex`, entao precisam do `[hidden]`
        explicito. Sem ele o JS liga o atributo, o atributo entra no DOM e o
        elemento continua na tela: foi assim que a barra de busca ficou visivel
        sobre uma galeria vazia.
        """
        # Os quatro estao no MESMO grupo de seletores (separados por virgula)
        # fechando em `display: none`.
        ini = self.css.index(".clips-toolbar[hidden]")
        bloco = self.css[ini:ini + 320]
        for seletor in (".clips-toolbar", ".clips-search-clear",
                        ".clip-play-btn", ".clips-empty"):
            with self.subTest(seletor=seletor):
                self.assertIn(seletor + "[hidden]", bloco)
        self.assertRegex(bloco, r"display:\s*none")

    def test_the_gallery_elements_declare_a_display_that_needs_the_hidden_rule(self):
        """A prova de que o `[hidden]` NAO e decorativo.

        Se algum dia estas regras deixarem de declarar `display`, o teste de
        cima vira letra morta sem que ninguem perceba — este ancora a razao.
        """
        for seletor, valor in ((".clips-toolbar", "flex"),
                               (".clips-search-clear", "inline-flex")):
            with self.subTest(seletor=seletor):
                # O corpo da regra acaba na PRIMEIRA chave de fechamento. Uma
                # janela de N caracteres nao serve: ela ultrapassa o `}` e cai
                # no comentario seguinte, que cita `display: flex` como texto —
                # e o teste passaria lendo o comentario em vez da regra.
                ini = self.css.index(seletor + " {")
                fim = self.css.index("}", ini)
                corpo = self.css[ini:fim]
                self.assertRegex(corpo, r"display:\s*" + valor)

    def test_the_search_describes_itself_with_the_static_hint(self):
        """`aria-describedby` aponta para a dica ESTATICA, nao para a regiao viva.

        A `#gallery-status` nasce vazia e so ganha texto depois do primeiro
        render. Apontar a descricao do campo para ela daria ao campo uma
        descricao em branco ate o JS rodar — e um alvo vazio nao descreve nada.
        A dica certa e a que ja tem texto no HTML.
        """
        self.assertRegex(self.html, r'id="gallery-search"[^>]*aria-describedby="gallery-search-hint"')
        self.assertNotRegex(self.html, r'id="gallery-search"[^>]*aria-describedby="gallery-status"')
        # A dica existe, tem conteudo e nao depende do JS para ter texto.
        alvo = re.search(
            r'id="gallery-search-hint"[^>]*>(.*?)</p>', self.html, re.S)
        self.assertIsNotNone(alvo, "a dica do campo de busca nao existe")
        self.assertTrue(re.sub(r"<[^>]+>", "", alvo.group(1)).strip(),
                        "a dica do campo de busca esta vazia")
        # A regiao viva continua declarada — agora so como anuncio.
        self.assertRegex(self.html, r'id="gallery-status"[^>]*role="status"')


class GalleryPosterAndApiTests(unittest.TestCase):
    """Posters de verdade no servidor + a API `window.clipsGallery.setClips`.

    O contrato da referencia promete `{title, src, poster, duration}`. Duas
    coisas faltavam para ele valer aqui: `poster` nao existia (o servidor nunca
    gerava imagem, e `thumb` era `None` fixo) e `window.clipsGallery` nao
    existia (a galeria so era escrita pelo poll). O que os testes travam:

    * o poster e um arquivo REAL ao lado do mp4, e `thumb` o reflete em vez de
      ser um literal;
    * a rota que serve o clip serve a imagem com o tipo certo — um jpg enviado
      como octet-stream morre no nosniff;
    * `setClips` normaliza o caminho da referencia (`/outputs/`) para a rota
      real (`/clips/`) e escreve pelo MESMO funil, para nao nascer um segundo
      dono de `#gallery`.
    """

    @classmethod
    def setUpClass(cls):
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        cls.src = Path(server.__file__).read_text(encoding="utf-8")

    def test_the_poster_path_is_derived_from_the_video(self):
        """O nome do jpg nasce do mp4 numa unica funcao.

        Escritor e leitor precisam concordar no nome; dois literais separados e
        como o painel acaba apontando para um poster que nunca foi escrito.
        """
        ini = self.src.index("def _poster_path(")
        bloco = self.src[ini:self.src.index("def _write_poster(")]
        self.assertIn("with_suffix", bloco)
        self.assertIn('".jpg"', bloco)

    def test_the_poster_is_written_with_ffmpeg(self):
        """O poster e um frame extraido por ffmpeg, nao um placeholder."""
        ini = self.src.index("def _write_poster(")
        bloco = self.src[ini:self.src.index("def _clip_to_payload(")]
        self.assertIn("require_binary", bloco)
        self.assertIn('"ffmpeg"', bloco)
        # O valor importa: `-frames:v 0` nao escreve quadro nenhum e o ffmpeg
        # ainda sai com codigo 0, entao a assercao precisa da linha inteira.
        self.assertIn('"-frames:v", "1"', bloco)
        # Uma falha ao gerar o poster NAO derruba o clip: o `except` amplo e o
        # `return None` sao obrigatorios — um `except` estreito deixaria a
        # excecao subir e mataria um render que deu certo.
        self.assertIn("except Exception", bloco)
        self.assertIn('logger.warn(', bloco)
        # E o teste de existencia do arquivo e o que impede devolver um nome de
        # poster que nao foi escrito: `.exists()` solto em qualquer lugar nao
        # basta, o guarda precisa estar ligado no ramo que devolve o nome.
        self.assertRegex(bloco, r"if not poster\.exists\(\):\s*\n\s*return None")
        self.assertIn("return poster.name", bloco)

    def test_the_payload_reads_the_poster_instead_of_assuming_it(self):
        """`thumb` so aponta para um arquivo que existe.

        Um `thumb` fixo nomeando um jpg ausente vira imagem quebrada na grade —
        a mesma regra que o `video` ja seguia.
        """
        ini = self.src.index("def _clip_to_payload(")
        bloco = self.src[ini:self.src.index("def _download_worker(")]
        self.assertIn("_poster_path(", bloco)
        # O `if` real: sem o guarda ligado a rel/path o ramo do poster nunca
        # roda e `thumb` fica None mesmo com o jpg no disco. Ancorar no
        # `.exists()` nao bastava — ele aparece tambem no ramo do video, entao
        # desligar ESTE `if` passava batido.
        self.assertRegex(bloco, r"if rel and path is not None:")
        self.assertIn(".exists()", bloco)
        self.assertNotIn('"thumb": None,', bloco)

    def test_the_poster_is_produced_during_the_finish_pass(self):
        """`finish` chama o gerador para cada clip renderizado."""
        ini = self.src.index("clips = [_clip_to_payload(")
        bloco = self.src[ini:ini + 900]
        self.assertIn("_write_poster(", bloco)
        self.assertIn('clip["thumb"]', bloco)

    def test_the_clip_route_serves_images_with_the_right_type(self):
        """A rota do clip deixa de ser mp4/octet-stream fixo.

        Video e poster saem pelo mesmo caminho, entao o tipo tem de vir da
        tabela por sufixo: `image/jpeg` cortado para octet-stream nao pinta.
        """
        ini = self.src.index('if path.startswith("/api/clips/")')
        # Ate o fim do ramo (o proximo `# Static assets`), e nao uma janela de
        # N caracteres: a janela fixa ja deixou uma assercao ler o ramo errado.
        bloco = self.src[ini:self.src.index("# Static assets inside web/")]
        self.assertIn("asset_content_type(candidate)", bloco)
        self.assertNotIn('"video/mp4" if', bloco)
        # E a tabela precisa mesmo conhecer o jpg.
        self.assertIn('".jpg": "image/jpeg"', self.src)

    def test_the_reference_api_exists_on_window(self):
        """`window.clipsGallery.setClips` e exposto com o nome do contrato."""
        self.assertIn("window.clipsGallery", self.js)
        ini = self.js.index("window.clipsGallery")
        bloco = self.js[ini:ini + 300]
        self.assertIn("setClips", bloco)

    def test_the_api_normalises_the_reference_paths(self):
        """`/outputs/x.mp4` vira `clips/x.mp4`; `/clips/x.mp4` nao duplica.

        O snippet da referencia aponta para `/outputs/`, que nao existe neste
        servidor. Sem a normalizacao o card nasce apontando para o vazio.
        """
        ini = self.js.index("function clipsNormalizeSrc(")
        bloco = self.js[ini:self.js.index("function clipsNormalizeForeign(")]
        self.assertIn("/^outputs", bloco)
        self.assertIn("/^clips", bloco)
        self.assertIn("CLIPS_BASE", bloco)
        # E o prefixo real precisa ser o que a rota do servidor atende.
        self.assertRegex(self.js, r"const CLIPS_BASE = 'clips/'")

    def test_the_api_posts_through_the_single_funnel(self):
        """`setClips` chama `renderClips`, e nao escreve em `#gallery` direto.

        O funil existe justamente porque tres pontos escreviam na grade com
        ideias diferentes de "vazio". Uma API nova que montasse a grade por
        fora recriaria o problema que o funil resolveu.
        """
        ini = self.js.index("function setClipsDaReferencia(")
        bloco = self.js[ini:self.js.index("window.clipsGallery")]
        self.assertIn("renderClips(", bloco)
        self.assertNotIn("#gallery", bloco)
        # E a fonte e a mesma que o poll usa.
        self.assertIn("state.clips", bloco)

    def test_the_api_discards_entries_it_cannot_play(self):
        """Entrada sem fonte e descartada, nao vira card morto.

        Uma lista com item invalido nao pode derrubar a galeria inteira nem
        pintar um cartao que nao toca.
        """
        ini = self.js.index("function clipsNormalizeForeign(")
        bloco = self.js[ini:self.js.index("function setClipsDaReferencia(")]
        self.assertIn("return null", bloco)
        ini2 = self.js.index("function setClipsDaReferencia(")
        bloco2 = self.js[ini2:self.js.index("window.clipsGallery")]
        self.assertIn(".filter(Boolean)", bloco2)

    def test_the_card_uses_the_poster_when_there_is_one(self):
        """O cartao passa `thumb` como `poster` do <video>.

        Gerar o poster no servidor nao serve de nada se a grade o ignora: o
        card continua preto ate o metadata carregar.
        """
        ini = self.js.index("function renderClips(")
        bloco = self.js[ini:self.js.index("function formatSize(")]
        self.assertIn("posterSrc", bloco)
        self.assertIn("c.thumb", bloco)
        # A concatenacao inteira, e nao so o nome do atributo: trocar o
        # ternario por `false` deixa `poster=` na string e a assercao frouxa
        # passava com o poster desligado.
        self.assertIn("(posterSrc ? ' poster=\"' + posterSrc + '\"' : '')", bloco)


class GalleryOpenLibraryEventTests(unittest.TestCase):
    """O contrato `clips:open-library` da referencia, apontado para a acao real.

    O snippet da referencia navega para `/outputs`, rota que NAO existe neste
    servidor: a pasta de saida ja e uma vista in-page. O que os testes travam:

    * o botao `#btn-library` despacha o evento em vez de chamar a funcao de
      render direto, para o clique e um emissor externo fazerem a mesma coisa
      por um ponto so;
    * o listener abre a pasta in-page e NAO navega — um `location.assign`
      trocaria uma tela que funciona por um 404;
    * o listener nao se re-chama: se ele emitisse o mesmo evento, o fluxo
      entraria em recursao.
    """

    @classmethod
    def setUpClass(cls):
        cls.js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")

    def test_the_button_dispatches_the_event(self):
        """O clique emite `clips:open-library`, e nao chama `showLibrary` direto."""
        ini = self.js.index("$('#btn-library').addEventListener('click'")
        bloco = self.js[ini:ini + 400]
        self.assertIn("openLibrary()", bloco)
        self.assertNotIn("showLibrary()", bloco)

    def test_open_library_dispatches_the_contract_event(self):
        """A funcao publica emite o evento com o nome exato do contrato."""
        ini = self.js.index("function openLibrary(")
        bloco = self.js[ini:self.js.index("document.addEventListener('clips:open-library'")]
        self.assertIn("dispatchEvent", bloco)
        self.assertIn("'clips:open-library'", bloco)

    def test_the_listener_opens_the_folder_without_navigating(self):
        """O listener abre in-page; nada de `location.assign('/outputs')`.

        A rota da referencia nao existe aqui, e navegar por causa do nome do
        evento trocaria a galeria por um 404.
        """
        ini = self.js.index("document.addEventListener('clips:open-library'")
        bloco = self.js[ini:ini + 400]
        self.assertIn("showLibrary()", bloco)
        self.assertNotIn("location.assign", bloco)
        self.assertNotIn("/outputs", bloco)

    def test_the_listener_does_not_redispatch_the_event(self):
        """O handler nao emite o proprio evento: seria recursao.

        `openLibrary` emite; o listener abre. Se o listener tambem emitisse —
        direto ou chamando `openLibrary`, que emite — cada abertura chamaria a
        si mesma sem parar.
        """
        ini = self.js.index("document.addEventListener('clips:open-library'")
        bloco = self.js[ini:ini + 400]
        self.assertNotIn("dispatchEvent", bloco)
        self.assertNotIn("openLibrary(", bloco)


class FooterRedesignTests(unittest.TestCase):
    """O rodape novo: marca, navegacao, atalho da CLI e o copiar.

    O rodape deixou de ser markup estatico do index.html: ele e' montado por
    `renderFooter()` em comum.js -- como o rail -- e vive nas TRES paginas.

    O motivo e' o mesmo que fez o rail subir para comum.js: a navegacao do
    rodape era uma SEGUNDA copia das rotas, escrita a mao no HTML
    (`btn-rail-cortes` -> `/`, `btn-rail-scrap` -> `/scrap`). Duas listas da
    mesma coisa divergem -- o rail ja chegou a ter tres nomes para o mesmo
    destino. Agora o rodape deriva de RAIL_PAGES.

    O que os testes travam:

    * o rodape esta nas tres paginas (era o que faltava: so o index o tinha,
      e como ele era a unica entrada para `/docs`, a ajuda ficava
      inalcancavel de Ajustes e Biblioteca);
    * a estrutura nova existe e os destinos sobreviveram -- o rodape continua
      sendo a saida de quem chega ao fim da pagina;
    * as cores vem dos tokens do painel, e nao de uma paleta propria;
    * o botao de copiar copia o TEXTO DO ALVO e anuncia numa regiao viva, em
      vez de guardar uma copia da string no JS.
    """

    PAGES = ("index.html", "publicar.html", "ajustes.html", "scrap.html")

    @classmethod
    def setUpClass(cls):
        # O markup E o comportamento moram em comum.js; o estilo, em
        # shared.css (o rodape esta nas tres paginas, entao o css nao pode
        # ficar no css de uma delas).
        cls.comum = (server.WEB_DIR / "comum.js").read_text(encoding="utf-8")
        cls.css = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")
        cls.index_js = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")

    def test_every_page_carries_the_footer(self):
        """As TRES paginas tem o rodape -- e era isso que faltava.

        Enquanto o rodape so existia no index, `/docs` (a unica ajuda do
        produto) ficava inalcancavel de Ajustes e Biblioteca: o rodape era a
        unica entrada para ele.
        """
        for name in self.PAGES:
            body = (server.WEB_DIR / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertRegex(body, r'<footer[^>]*class="site-footer"')
                self.assertRegex(body, r'<footer[^>]*aria-label=')
                self.assertIn("data-footer", body)

    def test_the_new_structure_is_in_place(self):
        """Os blocos da referencia existem, com os nomes que o CSS espera."""
        # `site-footer` NAO entra nesta lista: ele e' a classe do proprio
        # `<footer>`, que fica no HTML de cada pagina (coberto por
        # test_every_page_carries_the_footer). O template de comum.js cria o
        # CONTEUDO do rodape, nao o elemento.
        for marca in ("footer-container", "footer-main",
                      "footer-brand", "footer-brand__icon", "footer-brand__name",
                      "footer-bottom", "footer-caption", "footer-cli", "footer-copy"):
            with self.subTest(classe=marca):
                self.assertIn(marca, self.comum)

    def test_the_footer_is_rendered_by_the_shared_module(self):
        """Quem monta o rodape e' o comum.js, nao a pagina.

        O comportamento segue o markup: um rodape montado pelo modulo comum mas
        ligado so pelo index.js deixaria Ajustes e Biblioteca com um rodape
        morto -- botoes que nao levam a lugar nenhum.
        """
        self.assertIn("function renderFooter(", self.comum)
        self.assertIn("querySelectorAll('[data-footer]')", self.comum)
        self.assertIn("global.renderFooter = renderFooter", self.comum)
        # E o index.js nao pode ter voltado a ligar os botoes do rodape.
        for alvo in ("btn-copy-cli", "btn-rail-cortes", "btn-rail-scrap",
                     "btn-docs-foot", "footer-cli-command", "footer-feedback"):
            with self.subTest(alvo=alvo):
                self.assertNotIn(alvo, self.index_js)

    def test_the_navigation_derives_from_rail_pages(self):
        """A navegacao do rodape vem de RAIL_PAGES, e nao de copias no HTML.

        Era o ponto da mudanca: `btn-rail-cortes` e `btn-rail-scrap` repetiam
        os caminhos do rail. Derivando, uma pagina nova entra no rodape sozinha.
        """
        ini = self.comum.index("function footerNavHtml(")
        bloco = self.comum[ini:self.comum.index("function footerHtml(")]
        self.assertIn("RAIL_PAGES.map(", bloco)
        self.assertIn("data-footer-page=", bloco)
        # Nenhum caminho de pagina escrito a mao no rodape.
        for rota in ("/biblioteca", "/ajustes"):
            with self.subTest(rota=rota):
                self.assertNotIn("'" + rota + "'", bloco)

    def test_the_help_link_survived_and_is_not_a_rail_page(self):
        """`/docs` nao e' destino do rail: e' ajuda, e tem de continuar aqui.

        Ele fica fora de RAIL_PAGES de proposito -- nao e' uma etapa do fluxo.
        Se sumisse, a unica ajuda do produto ficaria sem entrada.
        """
        ini = self.comum.index("function footerNavHtml(")
        bloco = self.comum[ini:self.comum.index("function footerHtml(")]
        self.assertIn('data-footer-action="docs"', bloco)

    def test_the_brand_links_home_without_a_second_route_list(self):
        """A marca e um link para `/`, e nao um quarto <button> com handler."""
        ini = self.comum.index('class="footer-brand"')
        bloco = self.comum[ini:ini + 200]
        self.assertIn('href="/"', bloco)
        self.assertIn("aria-label=", bloco)

    def test_the_cli_command_lives_in_one_place(self):
        """O comando da CLI aparece UMA vez no modulo comum.

        Duas copias do mesmo comando divergem na primeira edicao -- e o alvo do
        `aria-describedby` e' o `<code>` que a pessoa ve na tela.
        """
        self.assertEqual(self.comum.count("python -m viralclipper"), 1)

    def test_the_footer_uses_the_panel_tokens(self):
        """Os `--footer-*` sao apelidos dos tokens do painel, nao uma paleta.

        A referencia declarava `--footer-bg: #0b1120` e um brilho indigo
        proprios: o rodape virava um bloco de cor diferente do resto da pagina.
        """
        ini = self.css.index(".site-footer {")
        bloco = self.css[ini:self.css.index(".site-footer,", ini)]
        for token in ("--footer-bg", "--footer-text", "--footer-muted",
                      "--footer-accent", "--footer-border"):
            with self.subTest(token=token):
                self.assertRegex(bloco, re.escape(token) + r":\s*var\(--")
        self.assertNotIn("#0b1120", bloco.lower())
        self.assertNotIn("--clips-", bloco)

    def test_the_footer_does_not_inherit_the_page_padding(self):
        """`padding: 0` neutraliza o `footer { padding }` que o scrap declarava.

        O scrap.css tinha `padding: 26px 0 40px` no ELEMENTO `footer`. Como
        `.site-footer` (classe) vence, mas nao declarava padding, o rodape da
        Biblioteca ficaria 26/40px mais alto que o das outras duas paginas --
        divergencia silenciosa, so visivel comparando as telas.
        """
        ini = self.css.index(".site-footer {")
        bloco = self.css[ini:self.css.index("\n}", ini)]
        self.assertRegex(bloco, r"padding:\s*0")

    def test_the_copy_button_copies_the_target_text(self):
        """O handler le o texto do `<code>`, e nao uma string embutida.

        Duas copias do mesmo comando divergem na primeira edicao; a fonte e' o
        proprio `#footer-cli-command`, que o `aria-describedby` ja aponta.
        """
        ini = self.comum.index("const cmdEl = host.querySelector('#footer-cli-command')")
        bloco = self.comum[ini:self.comum.index("copiar.addEventListener(")]
        self.assertIn("footer-cli-command", bloco)
        copia = self.comum[self.comum.index("copiar.addEventListener("):]
        copia = copia[:copia.index("function renderFooter(")]
        self.assertIn("cmdEl.textContent", copia)
        self.assertIn("writeText", copia)
        # Nao pode ter o comando escrito a mao DENTRO do handler.
        self.assertNotIn("python -m viralclipper", copia)

    def test_the_copy_button_announces_and_degrades(self):
        """Sucesso, indisponibilidade e falha viram texto na regiao viva.

        Ha TRES desfechos, e cada um tem a sua mensagem: sem secure-context /
        sem clipboard (nem tenta), falha da API, e sucesso. Um generico
        "nao foi possivel" esconderia o caso em que copiar a mao e a unica saida.
        """
        ini = self.comum.index("const feedbackEl = host.querySelector('#footer-feedback')")
        bloco = self.comum[ini:self.comum.index("function renderFooter(")]
        self.assertIn("catch", bloco)
        # O teste de disponibilidade decide a mensagem ANTES de tentar copiar.
        # Ancorar no `if` real: as tres mensagens existem no texto de qualquer
        # forma, entao so a condicao prova que o ramo e alcancavel.
        self.assertRegex(
            bloco,
            r"if \(!window\.isSecureContext \|\| !navigator\.clipboard"
            r" \|\| !navigator\.clipboard\.writeText\) \{")
        self.assertRegex(bloco, r"anunciar\('[^']*indispon[^']*'\)")
        self.assertRegex(bloco, r"anunciar\('[^']*copiad[^']*'\)")
        self.assertRegex(bloco, r"anunciar\('[^']*copie[^']*'\)")
        # O alvo da regiao viva e' criado pelo template, com os atributos certos.
        self.assertRegex(self.comum, r'id="footer-feedback"[^>]*role="status"')
        self.assertRegex(self.comum, r'id="footer-feedback"[^>]*aria-live="polite"')

    def test_the_copy_button_cannot_be_clicked_twice_at_once(self):
        """O botao desabilita durante a copia e volta no `finally`.

        Dois cliques rapidos disparariam duas escritas concorrentes no
        clipboard, cujo resultado e indefinido. E o botao PRECISA voltar a
        ficar habilitado mesmo quando a copia falha, senao um erro unico
        deixaria o controle morto para sempre.
        """
        copia = self.comum[self.comum.index("copiar.addEventListener("):]
        copia = copia[:copia.index("function renderFooter(")]
        self.assertRegex(copia, r"\.disabled = true")
        self.assertIn("finally", copia)
        self.assertRegex(copia, r"\.disabled = false")
        # E o CSS nao pode deixar o estado desabilitado sem sinal visual.
        self.assertIn(".site-footer .footer-copy:disabled", self.css)

    def test_the_announcement_clears_itself(self):
        """O aviso some sozinho, senao deixa de ser feedback.

        Um "copiado" permanente vira parte do rodape: quem olhasse depois nao
        saberia se a copia foi agora ou cinco minutos atras. O timer e cancelado
        a cada anuncio para um aviso novo nao ser apagado pelo anterior.
        """
        ini = self.comum.index("const anunciar = (texto)")
        bloco = self.comum[ini:self.comum.index("copiar.addEventListener(")]
        self.assertIn("clearTimeout", bloco)
        self.assertIn("setTimeout", bloco)
        self.assertRegex(bloco, r"feedbackEl\.textContent = ''")

    def test_the_copy_button_names_itself_and_its_target(self):
        """O botao so-icone tem rotulo e uma descricao com texto.

        Um botao so com SVG nao diz o que faz; e `aria-describedby` apontando
        para um alvo vazio nao descreve nada -- o alvo aqui e o proprio comando.
        """
        ini = self.comum.index('class="footer-copy"')
        bloco = self.comum[ini:ini + 260]
        self.assertIn("aria-label=", bloco)
        self.assertIn('aria-describedby="footer-cli-command"', bloco)
        # O alvo e' o `<code>`, e ele e' preenchido por COMANDO_CLI -- nao por um
        # literal solto dentro do template (que seria uma copia a mais).
        self.assertRegex(self.comum, r'<code id="footer-cli-command">.*?COMANDO_CLI')
        self.assertRegex(self.comum, r"const COMANDO_CLI = 'python -m viralclipper'")

    def test_the_live_region_reserves_its_line(self):
        """A regiao viva reserva a linha com `min-height`, sem sair do fluxo.

        Ela nasce sem texto; se nao reservasse altura, o rodape inteiro pularia
        ao copiar. A referencia resolve com `min-height` -- e nao com
        `display: none` -- porque a regiao viva precisa continuar existindo para
        o leitor de tela anunciar. Esconder o proprio alvo do anuncio seria
        consertar o pulo quebrando o aviso.
        """
        ini = self.css.index(".site-footer .footer-feedback {")
        bloco = self.css[ini:ini + 200]
        self.assertIn("min-height", bloco)
        self.assertNotIn("display: none", bloco)

    def test_the_footer_styles_are_scoped_to_the_footer(self):
        """Todo seletor do rodape comeca por `.site-footer`.

        `footer-link`, `footer-nav` e companhia sao nomes genericos: um
        `.footer-link` solto pegaria qualquer elemento com a classe em qualquer
        lugar da pagina vestindo a regra do rodape. O escopo por ancestral e o
        que a condicao "os estilos ficam restritos ao rodape" significa em
        codigo.

        O bloco vai ate o FIM do shared.css: ele foi anexado la, porque o rodape
        passou a estar nas tres paginas.
        """
        ini = self.css.index("/* ---------- FOOTER ----------")
        bloco = self.css[ini:]
        # Tira comentarios para ler so as regras.
        sem_comentario = re.sub(r"/\*.*?\*/", "", bloco, flags=re.S)
        for m in re.finditer(r"(?m)^\s*([.a-zA-Z][^{}:]*)\{", sem_comentario):
            seletor = m.group(1).strip()
            with self.subTest(seletor=seletor):
                for parte in seletor.split(","):
                    parte = parte.strip()
                    # `.site-footer::before` e o proprio rodape; os demais
                    # precisam do ancestral `.site-footer`.
                    if parte.startswith(".site-footer"):
                        continue
                    self.fail("seletor sem escopo do rodape: " + repr(parte))
        self.assertIn(".site-footer .footer-container", bloco)

    def test_the_touch_targets_are_big_enough(self):
        """Links e o botao de copiar tem alvo de toque de 44px (WCAG 2.5.8).

        O rodape e onde se clica com o polegar no celular: um alvo de 20px
        obriga a mirar. `min-height` no link e `height` fixo no botao.
        """
        ini = self.css.index(".site-footer .footer-link {")
        bloco = self.css[ini:ini + 400]
        self.assertRegex(bloco, r"min-height:\s*44px")
        ini2 = self.css.index(".site-footer .footer-copy {")
        bloco2 = self.css[ini2:ini2 + 400]
        self.assertRegex(bloco2, r"height:\s*44px")
        self.assertRegex(bloco2, r"width:\s*44px")

    def test_the_footer_respects_reduced_motion(self):
        """`prefers-reduced-motion` desliga transicoes do rodape.

        As transicoes de cor/borda sao decorativas; quem pediu para o sistema
        reduzir movimento nao deve receber animacao so porque ela e curta.

        O seletor precisa ser o MESMO do elemento (`.site-footer .footer-link`),
        e nao `.site-footer *`: aquele tem especificidade menor que a regra base
        e perderia na cascata -- foi o defeito medido no navegador, com
        `transitionDuration` ainda em 0.18s.
        """
        ini = self.css.index("prefers-reduced-motion: reduce",
                             self.css.index("/* ---------- FOOTER ----------"))
        bloco = self.css[ini:ini + 300]
        self.assertIn("transition: none", bloco)
        self.assertIn("animation: none", bloco)
        self.assertIn(".site-footer .footer-link", bloco)
        self.assertNotRegex(bloco, r"\{\s*\n\s*\.site-footer \*,")

    def test_every_element_the_js_reaches_is_created_by_the_template(self):
        """Os ids que o JS liga sao criados pelo proprio template.

        Antes esta garantia era "o id existe no HTML". Agora o markup nasce do
        template, entao o que importa e' que `renderFooter` consiga ACHAR cada
        alvo -- um id que o `host.querySelector` procura e o template nao cria
        seria um botao mudo, sem nenhum sinal.
        """
        for alvo in ("btn-copy-cli", "footer-cli-command", "footer-feedback"):
            with self.subTest(alvo=alvo):
                self.assertIn('id="' + alvo + '"', self.comum)
                self.assertIn("#" + alvo, self.comum)


class JumpLinksTests(unittest.TestCase):
    """Atalhos internos nas tres paginas.

    O componente nasceu na Biblioteca (`.scrap-jump-links`), onde a pagina e'
    longa e tem duas paradas obvias. O Estudio empilha nove blocos e o Ajustes
    quatro cards, e nenhum dos dois tinha atalho. O componente subiu para o
    shared.css e perdeu o prefixo `scrap-`: um seletor com o nome de uma pagina
    usado em outra e' uma mentira que ninguem revisa.

    O que os testes travam:

    * as TRES paginas tem atalhos, com `aria-label`;
    * todo `href="#x"` aponta para um `id` que EXISTE na mesma pagina -- um
      atalho para ancora inexistente nao da erro: o navegador nao faz nada e
      nao ha sinal no console. Quem clica so conclui que o botao esta quebrado;
    * o rotulo do atalho compartilha a palavra-chave do heading de destino, para
      o atalho nao criar um segundo nome para o mesmo bloco;
    * as regras vivem no shared.css, e nao duplicadas nos css de pagina.
    """

    PAGES = ("index.html", "publicar.html", "ajustes.html", "scrap.html")

    @classmethod
    def setUpClass(cls):
        cls.shared = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")

    def page(self, name: str) -> str:
        return (server.WEB_DIR / name).read_text(encoding="utf-8")

    def nav(self, name: str) -> str:
        body = self.page(name)
        found = re.search(r'<nav class="jump-links".*?</nav>', body, re.S)
        self.assertIsNotNone(found, f"{name}: nav.jump-links nao existe")
        return found.group(0)

    @staticmethod
    def sem_comentario(texto: str) -> str:
        """Tira comentarios de HTML e de CSS.

        Necessario porque a documentacao da mudanca CITA os nomes antigos: sem
        isso, o proprio comentario que explica a remocao faria o teste da
        remocao falhar.
        """
        texto = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
        return re.sub(r"/\*.*?\*/", "", texto, flags=re.S)

    @staticmethod
    def palavras(texto: str) -> set:
        """Palavras de 4+ letras, sem acento e em minusculas."""
        import unicodedata

        plano = unicodedata.normalize("NFKD", texto.lower())
        plano = "".join(c for c in plano if not unicodedata.combining(c))
        return set(re.findall(r"[a-z]{4,}", plano))

    def test_every_page_has_jump_links(self):
        for name in self.PAGES:
            body = self.page(name)
            with self.subTest(page=name):
                self.assertIn('class="jump-links"', body)
                self.assertRegex(body, r'<nav class="jump-links"[^>]*aria-label=')

    def test_every_target_exists_in_the_same_page(self):
        """Nenhum atalho aponta para ancora que nao existe.

        `href="#nao-existe"` nao gera erro, nao aparece no console e nao move a
        pagina. E' o pior desfecho: falha silenciosa que parece bug do botao.
        """
        for name in self.PAGES:
            body = self.page(name)
            alvos = re.findall(r'href="#([^"]+)"', self.nav(name))
            self.assertGreater(len(alvos), 0, f"{name}: atalhos sem destino")
            ids = set(re.findall(r'\bid="([^"]+)"', body))
            for alvo in alvos:
                with self.subTest(page=name, alvo=alvo):
                    self.assertIn(alvo, ids,
                                  f"{name}: atalho para #{alvo}, que nao existe")

    def test_the_label_shares_the_destination_word(self):
        """O rotulo do atalho usa a palavra do bloco de destino.

        O atalho nao pode batizar o bloco de outro jeito: seria mais uma
        nomenclatura a aprender, que e' exatamente o problema que a pagina raiz
        tinha com os tres nomes.

        A Biblioteca e' o unico caso em que o rotulo NAO repete o heading --
        la os atalhos sao verbos ("Buscar midia" -> "Buscar conteudo", "Ver
        resultados" -> "Resultados") porque a pagina e' um fluxo de acoes. O
        teste aceita isso e cobra o que importa nos dois estilos: a palavra
        central do destino aparece no rotulo.
        """
        for name in self.PAGES:
            body = self.page(name)
            for alvo, rotulo in re.findall(
                    r'href="#([^"]+)"[^>]*>(.*?)</a>', self.nav(name), re.S):
                # Fora a seta (`<span aria-hidden>`), que e' decorativa.
                limpo = re.sub(r'<span aria-hidden="true">.*?</span>', "",
                               rotulo, flags=re.S)
                texto = re.sub(r"<[^>]+>", "", limpo).strip()
                with self.subTest(page=name, alvo=alvo, rotulo=texto):
                    heading = self.heading_for(body, alvo)
                    comuns = self.palavras(texto) & self.palavras(heading)
                    self.assertTrue(
                        comuns,
                        f"{name}: atalho '{texto}' e heading '{heading}' "
                        f"nao tem palavra em comum")

    @staticmethod
    def heading_for(body: str, alvo: str) -> str:
        """O texto do heading que o atalho aponta.

        O id pode estar no proprio heading (Estudio, Biblioteca) ou no card que
        o contem (Ajustes) -- nos dois casos o heading e' o que a pessoa le.
        """
        direto = re.search(
            r'<h[1-3][^>]*\bid="' + re.escape(alvo) + r'"[^>]*>(.*?)</h[1-3]>',
            body, re.S)
        if direto:
            return re.sub(r"<[^>]+>", "", direto.group(1)).strip()
        card = re.search(
            r'\bid="' + re.escape(alvo) + r'"[^>]*>.*?<h2[^>]*>(.*?)</h2>',
            body, re.S)
        if card:
            return re.sub(r"<[^>]+>", "", card.group(1)).strip()
        raise AssertionError(f"#{alvo} nao tem heading nenhum para ancorar")

    def test_the_component_lives_in_the_shared_sheet(self):
        """As regras estao no shared.css, e nao no css de uma pagina.

        Mesma razao do rodape: o componente esta nas tres paginas. No css de
        uma delas, a Biblioteca (que usa scrap.css) nao o teria.
        """
        self.assertIn(".jump-links {", self.shared)
        self.assertIn(".jump-links a {", self.shared)
        self.assertIn(".jump-links a:hover", self.shared)
        for name in ("index.css", "scrap.css"):
            folha = self.sem_comentario(
                (server.WEB_DIR / name).read_text(encoding="utf-8"))
            with self.subTest(sheet=name):
                self.assertNotIn(".jump-links {", folha)

    def test_the_old_scrap_prefix_is_gone(self):
        """Nenhum resquicio de `scrap-jump-links` fora dos comentarios."""
        for name in ("scrap.html", "scrap.css", "index.html", "ajustes.html",
                     "index.css", "shared.css"):
            texto = self.sem_comentario(
                (server.WEB_DIR / name).read_text(encoding="utf-8"))
            with self.subTest(arquivo=name):
                self.assertNotIn("scrap-jump-links", texto)

    def test_the_shortcuts_respect_reduced_motion(self):
        """O seletor do reduced-motion e' o do elemento, nao um curinga.

        `.jump-links *` tem especificidade menor que `.jump-links a` e perderia
        na cascata -- foi o defeito MEDIDO no rodape, onde a transicao de 180ms
        continuava correndo com o sistema pedindo movimento reduzido.
        """
        ini = self.shared.index("@media (prefers-reduced-motion: reduce) {"
                                "\n  .jump-links a")
        bloco = self.shared[ini:ini + 120]
        self.assertIn("transition: none", bloco)
        self.assertNotIn(".jump-links *", bloco)

    def test_the_page_sheets_did_not_keep_a_second_copy(self):
        """O scrap.css nao pode ter voltado a declarar o componente.

        Ele ainda tem uma regra de movimento reduzido que citava
        `.jump-links a`; ela saiu de proposito. O componente tem UMA fonte, no
        shared.css -- duas fontes para a mesma coisa divergem, que foi o que
        fez o rodape ter valores diferentes por pagina.
        """
        folha = self.sem_comentario(
            (server.WEB_DIR / "scrap.css").read_text(encoding="utf-8"))
        self.assertNotIn(".jump-links", folha)


class StudioToLibraryLinkTests(unittest.TestCase):
    """A volta do Estudio para a Biblioteca.

    O unico canal entre as paginas era Biblioteca -> Estudio (o `?url=`). Quem
    estava no Estudio sem o link na mao so chegava na Biblioteca pela rail, no
    topo de uma pagina de scroll longo -- e no celular a rail e' um hamburguer.

    O que os testes travam:

    * o Estudio tem um link para a Biblioteca;
    * ele mora no card FONTE, e nao no rodape: e' o campo que ele alimenta,
      entao e' ali que a pessoa procura quando o campo esta vazio;
    * ele NAO e' `class="hint"`. `.hint` e' o texto que o `aria-describedby` do
      campo anuncia; uma saida lida como requisito do campo e' pior que
      nenhuma saida;
    * o contrato `?url=` esta no docstring do server.py, porque era implicito --
      so o codigo sabia que existia.
    """

    def setUp(self):
        self.body = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_the_studio_links_to_the_library(self):
        self.assertIn('href="/biblioteca"', self.body)

    def test_the_link_lives_in_the_source_card(self):
        """Dentro do card Fonte -- o campo que ele alimenta."""
        # Ancorado no `href`, e nao no nome da classe. O que este teste mede e'
        # a POSICAO do link, e a classe ja mudou uma vez (`field-alt` ->
        # `field-alt-fora`) derrubando estes dois testes sem que nada na tela
        # tivesse se movido. Um teste de posicao nao deveria ter nome de estilo.
        card = self.body.index('class="card card-fonte"')
        link = self.body.index('href="/biblioteca"')
        self.assertGreater(link, card, "o link saiu do card Fonte")
        # E nao pode ter ido parar no rodape, que e' montado por comum.js.
        comum = (server.WEB_DIR / "comum.js").read_text(encoding="utf-8")
        self.assertNotIn('href="/biblioteca"', comum)

    def test_the_link_is_not_a_field_hint(self):
        """Nao usa `.hint`: isto nao descreve o campo, e' uma saida."""
        # A classe do <p> que envolve o link e' comparada TOKEN a token, e nao
        # por substring. Um `assertNotIn("hint", bloco)` sobre o texto inteiro
        # reprovaria por causa de uma palavra que aparece no meio do paragrafo
        # -- o que se quer e que a CLASSE nao traga `hint`, nao que a frase nao
        # contenha a letra.
        ini = self.body.index('href="/biblioteca"')
        abre = self.body.rindex("<p", 0, ini)
        m = re.match(r'<p class="([^"]*)"', self.body[abre:])
        self.assertIsNotNone(m, "o link ficou sem <p class> envolvendo")
        classes = m.group(1).split()
        self.assertNotIn(
            "hint", classes,
            "o link virou dica do campo: `.hint` e' o texto que o "
            "`aria-describedby` do campo anuncia, e uma saida lida como "
            "requisito do campo e' pior que nenhuma saida")

    def test_the_handoff_contract_is_documented(self):
        """O `?url=` deixou de ser implicito."""
        fonte = (server.WEB_DIR / "server.py").read_text(encoding="utf-8")
        doc = fonte[:fonte.index('"""', 3)]
        self.assertIn("Page contracts", doc)
        self.assertIn("?url=", doc)
        self.assertIn("index.js", doc)


class ApiNamespaceTests(unittest.TestCase):
    """A separacao entre PAGINA e DADO -- o contrato que o item 4 fixou.

    Antes, as ~26 rotas de dados conviviam com as 4 de pagina sem nenhum
    agrupamento visivel: `/library`, `/status`, `/thumb/` e `/clips/` ficavam
    lado a lado com `/ajustes` e `/docs`, e nada dizia qual era qual. Pior,
    `/prompts/curador` e `/scrap/thumb` existiam como GET **e** como POST no
    MESMO caminho, sem nenhuma pista de que um lia e o outro escrevia.

    Agora o prefixo e' a regra: `/...` e' pagina (HTML, o que o usuario ve na
    barra e guarda), `/api/...` e' dado e acao (JSON, chamado so pelo JS que
    este mesmo servidor entrega). Nenhuma rota que devolve JSON mora na raiz.

    O que os testes travam:

    * toda rota que nao e' uma das paginas conhecidas comeca com `/api/`;
    * as paginas sao exatamente as cinco (mais os aliases `.html` e o
      redirect do endereco antigo da Biblioteca);
    * `/api/ajustes` e `/api/prompts/curador` sao PUT, nao POST -- o verbo
      carrega o significado;
    * o docstring lista as rotas. Ele documentava SEIS de ~30, e era a
      primeira coisa que alguem lia para entender a API.
    """

    #: As paginas. `/scrap` e' o endereco antigo, que so redireciona.
    PAGES = ("/", "/index.html", "/publicar", "/publicar.html",
             "/biblioteca", "/biblioteca.html",
             "/ajustes", "/ajustes.html", "/docs", "/scrap", "/scrap.html")

    def rotas(self, metodo: str) -> list[str]:
        """As rotas que o handler compara, lidas do proprio codigo.

        As rotas sao `if` no corpo dos `do_*`, nao uma tabela -- entao o teste
        le o codigo. E' o que pega o caso real: acrescentar uma rota e
        esquecer o prefixo.
        """
        import inspect

        src = inspect.getsource(getattr(server.Handler, metodo))
        achadas = re.findall(r'path\s*(?:==|!=)\s*"([^"]+)"', src)
        achadas += re.findall(r'path\.startswith\("([^"]+)"\)', src)
        # As paginas sao declaradas em CONJUNTO (`path in {"/ajustes",
        # "/ajustes.html"}`), porque as duas grafias servem a mesma coisa.
        for grupo in re.findall(r'path\s+in\s+\{([^}]+)\}', src):
            achadas += re.findall(r'"([^"]+)"', grupo)
        return achadas

    def test_no_data_route_lives_outside_the_prefix(self):
        """Nenhuma rota de dados na raiz -- so as paginas ficam la.

        `/status` respondendo no mesmo nivel de `/ajustes` e' o que fazia a
        superficie parecer maior do que e': 30 rotas sem hierarquia parecem 30
        coisas para conhecer, e nao duas (pagina e dado).
        """
        for metodo in ("do_GET", "do_POST", "do_PUT"):
            for rota in self.rotas(metodo):
                if rota in self.PAGES:
                    continue
                with self.subTest(metodo=metodo, rota=rota):
                    self.assertTrue(
                        rota.startswith("/api/"),
                        f"{metodo} serve {rota} fora de /api/")

    def test_the_pages_are_the_five_plus_the_old_address(self):
        """O que mora na raiz e' uma pagina, e nada mais.

        Se uma rota de dados reaparecer aqui, o teste acima falha; este falha
        se uma pagina NOVA aparecer sem entrar na lista -- e a lista e' o que
        o teste acima usa para permitir a excecao.
        """
        raiz = [r for r in self.rotas("do_GET") if not r.startswith("/api/")]
        self.assertEqual(sorted(raiz), sorted(self.PAGES),
                         "a raiz ganhou ou perdeu uma rota de pagina")

    def test_the_replace_endpoints_are_put(self):
        """Substituir o objeto inteiro e' PUT. Ler o mesmo recurso e' GET.

        Antes eram POST no MESMO caminho do GET, o que obrigava a abrir o
        handler para saber se a chamada substituia ou acrescentava: `POST
        /ajustes` era indistinguivel de "criar um ajuste novo".
        """
        do_put = self.rotas("do_PUT")
        self.assertIn("/api/ajustes", do_put)
        self.assertIn("/api/prompts/curador", do_put)

        # E nao podem continuar no POST: o verbo e' a informacao.
        do_post = self.rotas("do_POST")
        self.assertNotIn("/api/ajustes", do_post,
                         "salvar ajustes voltou a ser POST")
        self.assertNotIn("/api/prompts/curador", do_post,
                         "salvar o prompt voltou a ser POST")

    def test_reading_and_replacing_share_the_path(self):
        """`GET /api/ajustes` e `PUT /api/ajustes` sao o par ler/escrever.

        E' o que o PUT compra: o mesmo caminho, o verbo dizendo o que a
        chamada faz, sem precisar de `/ajustes.json` para desambiguar.
        """
        do_get = self.rotas("do_GET")
        self.assertIn("/api/ajustes", do_get)
        self.assertNotIn("/api/ajustes.json", do_get,
                         "o sufixo .json voltou: o recurso e' um so")
        self.assertIn("/api/prompts/curador", do_get)

    def test_the_write_path_parses_the_body_in_one_place(self):
        """POST e PUT leem o corpo pela MESMA funcao.

        Duplicar o bloco faria as duas rotas divergirem na primeira mudanca --
        uma responderia 400 e a outra estouraria com um traceback.
        """
        import inspect

        for metodo in ("do_POST", "do_PUT"):
            src = inspect.getsource(getattr(server.Handler, metodo))
            with self.subTest(metodo=metodo):
                self.assertIn("self._read_payload()", src)
                self.assertNotIn("json.loads(", src,
                                 "o parsing do corpo voltou a ser copiado")

    def test_the_docstring_names_every_data_route(self):
        """O docstring lista as rotas de dados -- todas.

        Ele documentava SEIS (`/`, `/status`, `/run`, `/run/progress`,
        `/clips/<id>`, `/browse/native`) de cerca de trinta, e era a primeira
        coisa que alguem lia para entender a API. Um docstring parcial e' pior
        que nenhum: ele parece completo.
        """
        doc = server.__doc__ or ""
        for metodo in ("do_GET", "do_POST", "do_PUT"):
            for rota in self.rotas(metodo):
                if not rota.startswith("/api/"):
                    continue
                # `/api/thumb/` e `/api/clips/` sao prefixos; o docstring
                # escreve o parametro (`/api/thumb/<name>`), entao a comparacao
                # e' pelo trecho sem a barra final.
                alvo = rota.rstrip("/")
                with self.subTest(rota=rota):
                    self.assertIn(alvo, doc,
                                  f"o docstring nao menciona {rota}")

    def test_the_docstring_documents_the_pages_and_the_redirect(self):
        """As paginas e o endereco antigo tambem estao no docstring."""
        doc = server.__doc__ or ""
        for pagina in ("/biblioteca", "/ajustes", "/publicar", "/docs"):
            with self.subTest(pagina=pagina):
                self.assertIn(pagina, doc)
        self.assertIn("301", doc, "o redirect do endereco antigo nao esta documentado")

    def test_the_frontend_prefixes_the_api_in_one_place(self):
        """O `/api` entra no helper, e nao em cada chamada.

        Sao ~15 call sites no index.js e ~5 no ajustes.js. Um esquecido viraria
        um 404 silencioso: o `api()` devolve `{error}` e a tela so mostra
        "servidor fora do ar" -- sem pista de qual rota faltou.
        """
        index = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        ajustes = (server.WEB_DIR / "ajustes.js").read_text(encoding="utf-8")
        scrap = (server.WEB_DIR / "scrap.js").read_text(encoding="utf-8")
        for nome, fonte in (("index.js", index), ("ajustes.js", ajustes)):
            with self.subTest(arquivo=nome):
                self.assertIn("const API_BASE = API + '/api';", fonte)
        self.assertIn('var API = "/api";', scrap)
        # E nenhuma chamada pode ter o prefixo escrito a mao (seria o segundo
        # lugar de onde ele vem).
        for nome, fonte in (("index.js", index), ("ajustes.js", ajustes)):
            with self.subTest(arquivo=nome):
                self.assertNotIn("api('/api/", fonte)
                self.assertNotIn('api("/api/', fonte)

    def test_the_clip_url_is_built_in_one_place(self):
        """`/api/clips/` e' uma constante so, e as chamadas passam por ela.

        Havia QUATRO pontos montando `'clips/' + ...` a mao. Quando o prefixo
        mudou, os quatro quebrariam juntos -- e o sintoma seria um video que
        nao toca, sem erro no console e sem teste de unidade que pegasse.
        """
        index = self.sem_comentario(
            (server.WEB_DIR / "index.js").read_text(encoding="utf-8"))
        self.assertIn("const CLIP_URL_BASE = '/api/clips/';", index)
        self.assertIn("function clipsPath(", index)
        # Nenhuma montagem a mao do caminho do clip sobrou. Comparado sem os
        # comentarios: o proprio comentario que explica a remocao CITA o
        # `'clips/' +`, e reprovaria o teste da remocao.
        self.assertNotIn("'clips/' +", index)
        self.assertNotIn('"clips/" +', index)
        # E as quatro que existiam passam pela funcao.
        self.assertGreaterEqual(index.count("clipsPath("), 5)

    @staticmethod
    def sem_comentario(fonte: str) -> str:
        """Tira comentarios de linha inteira e de bloco.

        Um teste que procura codigo ausente nao pode enxergar a documentacao
        da remocao: o comentario que explica POR QUE o `'clips/' +` saiu
        contem o `'clips/' +`.
        """
        linhas = []
        for linha in fonte.split("\n"):
            t = linha.strip()
            if t.startswith("//") or t.startswith("/*") or t.startswith("*"):
                continue
            linhas.append(linha)
        return "\n".join(linhas)

class TouchTargetTests(unittest.TestCase):
    """Alvo de toque: 24px e' o minimo da norma, 44px e' o conforto.

    Medido com sonda de retangulos no navegador servido pela 7755, antes do
    conserto: o link "Ajustar" tinha 15px de altura e o resumo de cookies
    19px — os dois abaixo do minimo WCAG 2.2 (2.5.8) como alvos solteiros.
    Editar/Excluir (30px) e as abas do Scrap (37-38px) passavam na norma e
    ficavam apertados no polegar.

    As regras de conforto vivem em `pointer: coarse`, que e' onde o idiomade
    pagina ja' existia (`.queue-filter` ja' fazia isto): inflar alvo de mouse
    nao e' conforto, e' ruido visual. O minimo de 24px, ao contrario, vale
    SEMPRE — inclusive no desktop, porque alvo pequeno e' alvo pequeno.
    """

    def css(self, nome: str) -> str:
        return (server.WEB_DIR / nome).read_text(encoding="utf-8")

    def test_the_selection_edit_link_is_never_below_24px(self):
        """O "Ajustar" da Execucao: 15px no desktop, em QUALQUER ponteiro."""
        linhas = [l for l in self.css("index.css").splitlines()
                  if l.strip().startswith(".execution-selection-edit {")]
        self.assertEqual(len(linhas), 1, "a regra do Ajustar sumiu ou duplicou")
        self.assertIn("min-height: 24px", linhas[0],
                      "alvo solteiro de 15px: reprova no 2.5.8")

    def test_the_cookies_summary_is_never_below_24px(self):
        """O disclosure "Acessar video restrito": media 19px no desktop."""
        css = self.css("index.css")
        ini = css.index(".cookies-box > summary {")
        bloco = css[ini:css.index("}", ini)]
        self.assertIn("min-height: 24px", bloco)
        self.assertIn("box-sizing: border-box", bloco,
                      "sem border-box o marker do disclosure engole a altura")

    def test_the_touch_block_sits_after_the_page_rules(self):
        """O bloco coarse do index precisa ser o ULTIMO do arquivo.

        `.btn { min-height: 40px }` e `.prov-item-actions .btn { 30px }`
        tem a mesma especificidade das regras daqui: se este bloco subisse
        para o meio do arquivo, a pagina voltaria a valer e o alvo de
        Editar/Excluir voltaria a 30px no celular — e o teste de texto
        continuaria achando "min-height: 44px" no arquivo. Por isso o
        corte e' de POSICAO, nao de presenca.
        """
        css = self.css("index.css")
        ini = css.rindex("@media (pointer: coarse) {")
        self.assertGreater(ini, css.index(".prov-item-actions .btn {"),
                           "o bloco coarse veio antes das regras de pagina")
        ramo = css[ini:]
        self.assertIn(".btn { min-height: 44px; }", ramo)
        self.assertIn(".prov-item-actions .btn { min-height: 44px;", ramo)
        self.assertIn(".cookies-box > summary { min-height: 44px; }", ramo)

    def test_the_queue_filter_keeps_the_coarse_rule_it_already_had(self):
        """Regressao do idiomade: o `.queue-filter` coarse de 44px ja' existia.

        Este teste nao conserta nada — ele fixa o padrao que os outros
        seguem, para que um refactor de responsivo nao apague a regra que
        ninguem lembra estar la'.
        """
        self.assertIn(
            "@media (pointer: coarse) { .queue-filter { min-height: 44px; padding: 10px 14px; } }",
            self.css("index.css"))

    def test_the_jump_links_stay_compact_on_mouse(self):
        """O conforto e' do toque: no mouse o chip continua 36px.

        Sem esta trava o teste de cima poderia ser "cumprido" subindo o
        valor base — e a linha de atalhos do desktop incharia sem motivo.
        """
        css = self.css("shared.css")
        bloco = css[css.index(".jump-links a {"):css.index("}", css.index(".jump-links a {"))]
        self.assertIn("min-height: 36px", bloco)
        self.assertIn(".jump-links a { min-height: 44px; }", css)

    def test_the_scrap_tabs_win_the_cascade_on_touch(self):
        """As abas do Scrap: 44px no toque, mesmo contra as regras de cima.

        `.tab` sozinho perde para `.scrap-grid .tab` (mais especificidade),
        entao o seletor coarse tem que repetir o peso das regras que ele
        sobrescreve. Se alguem simplificar para `.tab { min-height: 44px }`
        o teste reprova — e o defect voltaria em silencio.
        """
        css = self.css("scrap.css")
        ini = css.rindex("@media (pointer: coarse) {")
        ramo = css[ini:]
        self.assertIn("min-height: 44px", ramo)
        self.assertIn(".scrap-grid .tab", ramo)
        self.assertIn(".search-mode-group .tab", ramo)
        self.assertIn('.search-card .tabs[aria-label="Plataforma"] .tab', ramo)


class NormalizeRouteTests(unittest.TestCase):
    """POST /api/transcript/normalize -- limpa o transcript que o usuario colou.

    Era uma das rotas de dados sem NENHUM teste. O que ela tem de proprio e' a
    validacao de entrada: o corpo vem de um `<textarea>`, entao string vazia, so'
    espaco e um valor que nao e' string (a pagina monta JSON, entao pode chegar
    lista ou numero) tem de virar 400 antes de chegar ao parser.
    """

    def setUp(self):
        self.sent: dict = {}
        self.handler = object.__new__(server.Handler)
        self.handler._send_json = lambda payload, code=200: self.sent.update(payload, _code=code)

    def _call(self, payload):
        server.Handler._handle_normalize(self.handler, payload)
        return self.sent

    def test_a_missing_transcript_is_refused(self):
        self.assertEqual(self._call({}).get("_code"), 400)
        self.assertEqual(self.sent.get("error"), "transcript is required")

    def test_whitespace_only_is_refused(self):
        """Um textarea com um Enter dentro nao e' um transcript."""
        self.assertEqual(self._call({"transcript": "  \n\t "}).get("_code"), 400)
        self.assertEqual(self._call({"transcript": ""}).get("_code"), 400)

    def test_a_non_string_transcript_is_refused(self):
        """Sem a checagem de tipo o parser estouraria e a pagina veria um 500."""
        for ruim in (123, ["a"], {"b": 1}, None, True):
            with self.subTest(valor=ruim):
                self.sent.clear()
                self.assertEqual(self._call({"transcript": ruim}).get("_code"), 400)

    def test_a_valid_transcript_comes_back_normalized(self):
        """200 com o contrato inteiro: `normalized` + `cues` + `stats`.

        Os tempos tem de sair do SRT como NUMERO: e' o `start` que a pagina usa
        para ordenar e para saltar no player.
        """
        srt = "1\n00:00:01,000 --> 00:00:03,000\nOla mundo\n"
        sent = self._call({"transcript": srt})
        self.assertEqual(sent.get("_code"), 200)
        self.assertEqual(
            sorted(k for k in sent if k != "_code"), ["cues", "normalized", "stats"]
        )
        self.assertEqual(len(sent["cues"]), 1)
        self.assertAlmostEqual(sent["cues"][0]["start"], 1.0, places=6)
        self.assertAlmostEqual(sent["cues"][0]["end"], 3.0, places=6)
        self.assertIn("Ola mundo", sent["normalized"])
        self.assertEqual(sent["stats"]["cues"], 1)

    def test_a_transcript_the_parser_rejects_is_a_400_not_a_500(self):
        """`ClipperError` e' entrada ruim, nao defeito do servidor.

        Um 500 aqui faria a pagina mostrar "erro inesperado" para algo que ela
        mesma pode corrigir.
        """
        from unittest import mock

        with mock.patch.object(
            server.transcript_import, "normalize_transcript",
            side_effect=ClipperError("nao consegui ler"),
        ):
            sent = self._call({"transcript": "qualquer coisa"})
        self.assertEqual(sent.get("_code"), 400)
        self.assertEqual(sent.get("error"), "nao consegui ler")


class RemoveProviderRouteTests(unittest.TestCase):
    """POST /api/providers/remove -- apaga um provedor do usuario.

    Era a segunda rota de dados sem teste. O que ela precisa garantir: nao
    apagar um provedor de FABRICA (que voltaria no proximo `load`, deixando o
    botao sem efeito visivel) e nao responder 200 quando a gravacao falhou.
    """

    def setUp(self):
        self.sent: dict = {}
        self.handler = object.__new__(server.Handler)
        self.handler._send_json = lambda payload, code=200: self.sent.update(payload, _code=code)

    def _call(self, payload):
        server.Handler._handle_remove_provider(self.handler, payload)
        return self.sent

    def test_a_missing_name_is_refused(self):
        self.assertEqual(self._call({}).get("_code"), 400)
        self.assertEqual(self.sent.get("error"), "name is required")
        self.assertEqual(self._call({"name": "   "}).get("_code"), 400)

    def test_a_factory_provider_cannot_be_removed(self):
        """Apagar um de fabrica nao "nao faz nada": ele volta no proximo load.

        Por isso a resposta e' 400 explicito em vez de um 200 silencioso -- senao
        o usuario clicaria de novo achando que o botao esta quebrado.
        """
        from unittest import mock

        from viralclipper import user_providers

        with mock.patch.object(user_providers, "remove") as removed:
            sent = self._call({"name": "openai"})
        self.assertEqual(sent.get("_code"), 400)
        self.assertIn("nao pode ser removido", sent.get("error", ""))
        removed.assert_not_called()

    def test_a_user_provider_is_removed_and_the_list_comes_back(self):
        """200 com `ok`, o nome removido e a lista ATUALIZADA.

        A pagina redesenha os cards a partir desta resposta: devolver so' `ok`
        deixaria a lista velha na tela ate um refresh, e o provedor removido
        continuaria parecendo ativo.
        """
        from unittest import mock

        from viralclipper import user_providers

        with mock.patch.object(user_providers, "remove", return_value=[]) as removed, \
                mock.patch.object(routes_providers, "_providers_payload",
                                  return_value={"providers": [], "active": "x"}):
            sent = self._call({"name": "  meu-provedor  "})
        removed.assert_called_once()
        # O nome vai SEM espaco e a gravacao vai para o arquivo do USUARIO --
        # nunca para o de fabrica.
        self.assertEqual(removed.call_args[0][0], "meu-provedor")
        self.assertEqual(removed.call_args[0][1], routes_providers.USER_PROVIDERS_PATH)
        self.assertEqual(sent.get("_code"), 200)
        self.assertTrue(sent.get("ok"))
        self.assertEqual(sent.get("removed"), "meu-provedor")
        self.assertIn("providers", sent)

    def test_a_write_failure_is_a_500_not_a_silent_200(self):
        """Se a gravacao falha, o provedor continua no arquivo.

        Responder 200 faria a pagina sumir com o card e o provedor voltar no
        proximo restart -- o usuario acharia que removeu.
        """
        from unittest import mock

        from viralclipper import user_providers

        with mock.patch.object(user_providers, "remove",
                               side_effect=ClipperError("disco cheio")):
            sent = self._call({"name": "meu-provedor"})
        self.assertEqual(sent.get("_code"), 500)
        self.assertEqual(sent.get("error"), "disco cheio")
        self.assertNotIn("ok", sent)


class SkipLinkAndDescriptionTests(unittest.TestCase):
    """Duas entradas de P3: uma descricao por pagina e um primeiro alvo focavel.

    As duas custam uma linha e so existem se alguem cobrar: `meta
    description` ausente nao quebra nada em app local, e o skip link nao faz
    falta para quem usa mouse. Quem paga a conta e' o outro usuario — o que
    chega pelo buscador, e o que navega por teclado visitando os itens do
    rail (z-index 60) e do header sticky (50) antes de chegar no trabalho.
    """

    PAGINAS = ("index.html", "scrap.html", "ajustes.html", "publicar.html")

    def fonte(self, nome: str) -> str:
        return (server.WEB_DIR / nome).read_text(encoding="utf-8")

    def test_every_page_declares_a_description(self):
        """Uma descricao por pagina, com conteudo de verdade.

        `content` vazio ou curto derruba o teste: um meta que o buscador
        ignora e' o mesmo que nao ter meta, e a linha so vale pela frase que
        ela carrega.
        """
        for nome in self.PAGINAS:
            with self.subTest(pagina=nome):
                t = self.fonte(nome)
                achou = re.search(r'<meta name="description" content="([^"]+)">', t)
                self.assertIsNotNone(achou, "meta description ausente")
                texto = achou.group(1).strip()
                self.assertGreaterEqual(len(texto), 60, "descricao curta demais")
                self.assertRegex(texto, r"[.!?]$")

    def test_the_skip_link_is_the_first_focusable_thing_on_every_page(self):
        """Nada focavel antes do salto — nem o rail, nem o menu do header.

        A comparacao e' contra o PRIMEIRO focavel do documento inteiro
        (comentarios de HTML tirados: um `<a>` de exemplo dentro de
        `<!-- -->` reprovaria o teste sem existir na pagina).
        """
        for nome in self.PAGINAS:
            with self.subTest(pagina=nome):
                t = re.sub(r"<!--.*?-->", "", self.fonte(nome), flags=re.S)
                i_salto = t.index('<a class="skip-link"')
                primeiro = re.search(r"<(a|button|input|select|textarea|summary)\b", t)
                self.assertIsNotNone(primeiro)
                self.assertEqual(primeiro.start(), i_salto,
                                 "algo focavel veio antes do skip link")
                # E o alvo do salto existe na mesma pagina.
                self.assertIn('id="conteudo"', t)
                self.assertIn('href="#conteudo"', t)

    def test_main_accepts_the_focus_the_skip_link_hands_over(self):
        """`tabindex="-1"`: sem ele o salto rolava a pagina e deixava o
        foco no link, e o proximo Tab voltava para o rail de onde saiu."""
        for nome in self.PAGINAS:
            with self.subTest(pagina=nome):
                self.assertIn('<main class="wrap" id="conteudo" tabindex="-1">',
                              self.fonte(nome))

    def test_the_skip_link_is_invisible_until_it_is_focused(self):
        """Fora do foco: fora da viewport. No foco: na tela, acima do rail.

        O `:focus` (e nao `:focus-visible`) e' a chave: o link so recebe
        foco de tabulacao, nunca de clique, entao nao ha risco de ele
        aparecer para o usuario de mouse — e quando aparece, e' porque
        precisa ser lido.
        """
        css = (server.WEB_DIR / "shared.css").read_text(encoding="utf-8")
        ini = css.index(".skip-link {")
        bloco = css[ini:css.index("}", ini)]
        self.assertIn("top: -60px", bloco)
        self.assertIn("z-index: 80", bloco,
                      "abaixo do rail (60) o salto apareceria por baixo do menu")
        self.assertIn(".skip-link:focus { top: 12px; }", css)
        self.assertIn("prefers-reduced-motion", css[ini - 400:ini + 600])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()