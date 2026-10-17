"""Tests for the Instagram profile lister.

Offline by construction: every seam that would touch the network —
``_fetch_lsd``, ``_request_page`` and the cookie jar — is replaced by a fake, so
the suite never talks to Instagram and never depends on a live session. The one
real-network verification lives in ``reframe_check.py``-style scripts, not here.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from viralclipper import ig_profile
from viralclipper.ig_profile import (
    POSTS_DIR,
    REELS_DIR,
    ProfileItem,
    ProfileListing,
    _item_from_node,
    _page_items,
    list_profile,
    profile_username,
)
from viralclipper.util import ClipperError


def node(code, *, product_type="feed", media_type=2, pk=None, **extra):
    """One GraphQL edge node, shaped like the real payload."""
    body = {
        "code": code,
        "pk": pk if pk is not None else "pk_" + code,
        "product_type": product_type,
        "media_type": media_type,
        "like_count": 10,
        "taken_at": 1_700_000_000,
    }
    body.update(extra)
    return body


def page(codes, *, cursor=None, product_type="feed", media_type=2):
    """A GraphQL document with one edge per code."""
    connection = {
        "edges": [
            {"node": node(c, product_type=product_type, media_type=media_type)}
            for c in codes
        ],
        "page_info": {"end_cursor": cursor},
    }
    return {"data": {ig_profile.ROOT_FIELD: connection}}


class ProfileUsernameTests(unittest.TestCase):
    """The gate that decides whether a URL is a catalogue or a single item.

    A post and a profile are the same URL shape, so getting this wrong means a
    reel code is handed to the profile lister, which answers "0 itens" for an
    account that exists — a failure that looks like an empty profile.
    """

    CASES = [
        ("https://www.instagram.com/salmareis/", "salmareis"),
        ("https://www.instagram.com/salmareis", "salmareis"),
        ("https://instagram.com/salmareis/reels/", "salmareis"),
        ("https://www.instagram.com/salmareis/?hl=pt-br", "salmareis"),
        ("@salmareis", "salmareis"),
        # Single items: a code is not an account.
        ("https://www.instagram.com/p/ABC123/", None),
        ("https://www.instagram.com/reel/ABC123/", None),
        ("https://www.instagram.com/reels/ABC123/", None),
        ("https://www.instagram.com/tv/ABC123/", None),
        ("https://www.instagram.com/stories/salmareis/123/", None),
        ("https://www.instagram.com/explore/tags/funk/", None),
        ("https://www.instagram.com/", None),
        # Another site's profile must stay with its own extractor.
        ("https://www.youtube.com/@NASA/videos", None),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
        # A bare word could be a YouTube channel; only "@name" is unambiguous.
        ("NASA", None),
        ("", None),
    ]

    def test_the_table(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(profile_username(raw), expected)


class FolderSplitTests(unittest.TestCase):
    """`product_type` decides the folder; `media_type` only decides the video."""

    def test_a_clip_is_a_reel(self):
        item = _item_from_node(node("AAA", product_type="clips"))
        self.assertEqual(item.folder, REELS_DIR)
        self.assertTrue(item.is_reel)
        self.assertIn("/reel/AAA/", item.url)

    def test_a_feed_video_is_a_post(self):
        item = _item_from_node(node("BBB", product_type="feed", media_type=2))
        self.assertEqual(item.folder, POSTS_DIR)
        self.assertTrue(item.has_video)
        self.assertIn("/p/BBB/", item.url)

    def test_a_still_is_a_post_without_video(self):
        item = _item_from_node(node("CCC", product_type="feed", media_type=1))
        self.assertEqual(item.folder, POSTS_DIR)
        self.assertFalse(item.has_video)
        self.assertTrue(item.is_photo)

    def test_a_carousel_with_a_video_child_keeps_its_video(self):
        """The case that made the whole "posts" tab download nothing.

        A carousel arrives as ``media_type == 8``; reading only that number
        labels it "no video" and the item is skipped without ever being tried.
        """
        payload = node(
            "DDD",
            product_type="feed",
            media_type=8,
            carousel_media=[
                {"media_type": 1},
                {"media_type": 2, "video_versions": [{"url": "https://cdn/x.mp4"}]},
            ],
        )
        item = _item_from_node(payload)
        self.assertEqual(item.folder, POSTS_DIR)
        self.assertTrue(item.has_video)
        self.assertFalse(item.is_photo)

    def test_a_carousel_of_photos_has_no_video(self):
        payload = node(
            "EEE", product_type="feed", media_type=8,
            carousel_media=[{"media_type": 1}, {"media_type": 1}],
        )
        item = _item_from_node(payload)
        self.assertFalse(item.has_video)
        self.assertTrue(item.is_photo)

    def test_a_node_without_code_or_pk_is_dropped(self):
        self.assertIsNone(_item_from_node({"media_type": 2}))


class PageParsingTests(unittest.TestCase):
    def test_a_page_without_the_connection_names_the_doc_id(self):
        with self.assertRaises(ClipperError) as ctx:
            _page_items({"data": {"outra_coisa": {}}})
        self.assertIn("doc_id", str(ctx.exception))

    def test_edges_yield_items_and_the_cursor(self):
        items, cursor = _page_items(page(["A", "B"], cursor="CUR1"))
        self.assertEqual([i.code for i in items], ["A", "B"])
        self.assertEqual(cursor, "CUR1")

    def test_a_null_cursor_is_none(self):
        _, cursor = _page_items(page(["A"], cursor=None))
        self.assertIsNone(cursor)


class CookieJarTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_ig_"))

    def _jar(self, body: str) -> Path:
        target = self.dir / "cookies.txt"
        target.write_text(body, encoding="utf-8")
        return target

    HEADER = "# Netscape HTTP Cookie File\n"

    def test_a_missing_file_says_which_path(self):
        with self.assertRaises(ClipperError) as ctx:
            ig_profile._parse_cookie_file(self.dir / "nao_existe.txt")
        self.assertIn("nao_existe.txt", str(ctx.exception))

    def test_a_jar_without_sessionid_says_which_cookie_and_how_to_get_it(self):
        """An export that omits ``sessionid`` is the common failure.

        Chrome/Edge mark it HttpOnly and some exporters drop it, so the jar
        looks complete — 14 cookies, right domains — and the listing still
        refuses. The message has to name the missing cookie, and say where the
        value is, because "exporte os cookies de novo" is what produced the jar.
        """
        path = self._jar(
            self.HEADER
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n"
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tds_user_id\t42\n"
        )
        with self.assertRaises(ClipperError) as ctx:
            ig_profile.resolve_cookies(path)
        message = str(ctx.exception)
        self.assertIn("sessionid", message)
        self.assertIn("HttpOnly", message)
        self.assertIn("--ig-session", message)
        self.assertIn("DevTools", message)

    def test_a_complete_jar_is_read(self):
        path = self._jar(
            self.HEADER
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsegredo\n"
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n"
        )
        jar = ig_profile._parse_cookie_file(path)
        self.assertEqual(jar["sessionid"], "segredo")
        self.assertEqual(jar["csrftoken"], "abc")
        self.assertEqual(ig_profile.resolve_cookies(path)["sessionid"], "segredo")

    def test_the_reader_no_longer_demands_the_session(self):
        """``_parse_cookie_file`` has to read an incomplete jar.

        It is the reader :func:`save_session` uses to fix one, so a check here
        would make the fix unreachable.
        """
        path = self._jar(self.HEADER + ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n")
        self.assertNotIn("sessionid", ig_profile._parse_cookie_file(path))

    def test_a_supplied_session_completes_the_jar(self):
        path = self._jar(self.HEADER + ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n")
        cookies = ig_profile.resolve_cookies(path, "1234%3Aabc")
        self.assertEqual(cookies["sessionid"], "1234%3Aabc")
        self.assertEqual(cookies["csrftoken"], "abc")


class SessionValueTests(unittest.TestCase):
    """O valor colado do DevTools, e o que vai no header."""

    def test_a_decoded_value_is_encoded(self):
        """DevTools mostra ``:``; o header carrega ``%3A``."""
        self.assertEqual(ig_profile.normalise_session("1234:abc"), "1234%3Aabc")

    def test_an_encoded_value_passes_through(self):
        """Colar do header nao pode virar dupla codificacao."""
        self.assertEqual(ig_profile.normalise_session("1234%3Aabc"), "1234%3Aabc")

    def test_the_whole_pair_is_accepted(self):
        """Colar a linha inteira ``sessionid=...`` e o que a tabela oferece."""
        self.assertEqual(ig_profile.normalise_session("sessionid=1234%3Aabc"), "1234%3Aabc")

    def test_a_whole_cookie_header_is_accepted(self):
        """Um clique a menos que navegar ate a linha: colar o header inteiro."""
        header = "datr=AAA; sessionid=1234%3Aabc; csrftoken=xyz"
        self.assertEqual(ig_profile.normalise_session(header), "1234%3Aabc")

    def test_the_pair_stops_at_the_semicolon(self):
        self.assertEqual(ig_profile.normalise_session("sessionid=1234%3Aabc; a=b"), "1234%3Aabc")

    def test_quotes_and_whitespace_are_stripped(self):
        self.assertEqual(ig_profile.normalise_session('  "1234%3Aabc"  '), "1234%3Aabc")

    def test_characters_a_header_cannot_carry_are_encoded(self):
        """Espaco, ``;`` e ``,`` quebrariam o header Cookie."""
        self.assertEqual(ig_profile.normalise_session("a b;c,d"), "a%20b%3Bc%2Cd")

    def test_empty_stays_empty(self):
        self.assertEqual(ig_profile.normalise_session("   "), "")


class SaveSessionTests(unittest.TestCase):
    """Gravar a sessao no jar, sem reformatar o que o usuario exportou."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_ig_save_"))
        self.path = self.dir / "cookies.txt"

    def test_appends_to_a_jar_without_the_cookie(self):
        self.path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n"
            "www.tiktok.com\tFALSE\t/\tTRUE\t0\tttwid\ttt\n",
            encoding="utf-8",
        )
        self.assertTrue(ig_profile.save_session(self.path, "1234%3Aabc"))
        text = self.path.read_text(encoding="utf-8")
        # O cookie e HttpOnly e o formato tem como registrar isso.
        self.assertIn("#HttpOnly_.instagram.com", text)
        self.assertIn("sessionid\t1234%3Aabc", text)
        # Nada do que ja estava foi perdido.
        self.assertIn("csrftoken\tabc", text)
        self.assertIn("ttwid\ttt", text)
        self.assertEqual(ig_profile.resolve_cookies(self.path)["sessionid"], "1234%3Aabc")

    def test_replaces_a_stale_value_in_place(self):
        self.path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n"
            "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tvelho\n",
            encoding="utf-8",
        )
        self.assertTrue(ig_profile.save_session(self.path, "novo"))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3, "a gravacao nao pode acrescentar linha")
        self.assertTrue(lines[2].endswith("\tsessionid\tnovo"))

    def test_the_same_value_does_not_rewrite_the_file(self):
        """Escrever a cada busca faria o arquivo mudar sozinho."""
        self.path.write_text(
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tigual\n",
            encoding="utf-8",
        )
        before = self.path.stat().st_mtime_ns
        self.assertFalse(ig_profile.save_session(self.path, "igual"))
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_a_crlf_jar_keeps_its_line_endings(self):
        """O jar do yt-dlp e CRLF; misturar os dois quebra o proximo leitor."""
        self.path.write_bytes(
            b"# Netscape HTTP Cookie File\r\n"
            b".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\r\n"
        )
        self.assertTrue(ig_profile.save_session(self.path, "1234%3Aabc"))
        raw = self.path.read_bytes()
        self.assertNotIn(b"\n\n", raw)
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
        self.assertEqual(ig_profile.resolve_cookies(self.path)["sessionid"], "1234%3Aabc")

    def test_a_missing_jar_is_created(self):
        self.assertTrue(ig_profile.save_session(self.path, "1234%3Aabc"))
        self.assertEqual(ig_profile.resolve_cookies(self.path)["sessionid"], "1234%3Aabc")

    def test_an_empty_session_writes_nothing(self):
        self.assertFalse(ig_profile.save_session(self.path, ""))
        self.assertFalse(self.path.exists())

    def test_the_tiktok_cookies_are_untouched(self):
        """O mesmo arquivo serve o yt-dlp; reescrever perderia o outro site.

        ``FALSE`` em ``domain_specified`` e o que o export real escreve para um
        host sem ponto — com ``TRUE`` o proprio ``cookiejar`` recusa a linha.
        """
        self.path.write_text(
            "# Netscape HTTP Cookie File\n"
            "www.tiktok.com\tFALSE\t/\tTRUE\t0\tmsToken\tXXX\n",
            encoding="utf-8",
        )
        ig_profile.save_session(self.path, "1234%3Aabc")
        self.assertIn("msToken\tXXX", self.path.read_text(encoding="utf-8"))


class PrepareSessionTests(unittest.TestCase):
    """O caminho que o CLI e o painel usam: normaliza, grava, avisa."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_ig_prep_"))
        self.path = self.dir / "cookies.txt"

    def test_it_returns_the_normalised_value(self):
        self.assertEqual(ig_profile.prepare_session(self.path, "1:2"), "1%3A2")

    def test_it_writes_so_the_downloader_sees_the_session(self):
        """Nao e so para listar: o yt-dlp le o MESMO arquivo para baixar."""
        ig_profile.prepare_session(self.path, "1%3A2")
        self.assertEqual(ig_profile.resolve_cookies(self.path)["sessionid"], "1%3A2")

    def test_a_read_only_jar_still_lists(self):
        """Falhar ao gravar nao pode derrubar a execucao."""
        logger = mock.Mock()
        with mock.patch.object(ig_profile, "save_session", side_effect=OSError("negado")):
            value = ig_profile.prepare_session(self.path, "1%3A2", logger)
        self.assertEqual(value, "1%3A2")
        logger.warn.assert_called_once()

    def test_no_session_means_no_work(self):
        logger = mock.Mock()
        self.assertEqual(ig_profile.prepare_session(self.path, "  ", logger), "")
        logger.info.assert_not_called()
        self.assertFalse(self.path.exists())


class SessionOnTheWireTests(unittest.TestCase):
    """A sessao tem de sair no header ``Cookie``, nao so existir no dicionario.

    O caminho todo — colar, normalizar, gravar no jar, resolver — pode estar
    certo e a requisicao sair sem o cookie, e o sintoma seria o mesmo de sempre:
    o Instagram respondendo como anonimo.
    """

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"data": {}}'

    def _capture(self, session, jar_session=""):
        captured = {}

        def fake_send(method, url, *, data=None, headers=None, timeout=30):
            captured["cookie"] = (headers or {}).get("Cookie", "")
            captured["body"] = dict(data or {})
            return 200, '{"data": {}}'

        directory = Path(tempfile.mkdtemp(prefix="vc_wire_"))
        jar = directory / "cookies.txt"
        lines = [".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc"]
        if jar_session:
            lines.append(f"#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\t{jar_session}")
        jar.write_text(
            "# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
        cookies = ig_profile.resolve_cookies(jar, session)
        # `_send` e a costura unica: e ela que decide entre curl_cffi e urllib.
        # Fingir no nivel dela deixa o teste independente do transporte — que e
        # justamente o que o Instagram recusa.
        with mock.patch.object(ig_profile, "_send", side_effect=fake_send):
            ig_profile._request_page(cookies, "lsd", "dtsg", "alvo", None, 50)
        return captured

    def test_the_pasted_session_travels_with_the_csrf_token(self):
        cookie = self._capture(ig_profile.normalise_session("1234:abc"))["cookie"]
        self.assertIn("sessionid=1234%3Aabc", cookie)
        self.assertIn("csrftoken=abc", cookie)

    def test_a_jar_session_alone_is_enough(self):
        """Sem colar nada: o que ja esta no arquivo continua valendo.

        Nao e um caso hipotetico — e o segundo run em diante, quando a sessao
        ja foi gravada pelo :func:`prepare_session` e o campo do painel volta
        vazio.
        """
        cookie = self._capture("", jar_session="9876%3Axyz")["cookie"]
        self.assertIn("sessionid=9876%3Axyz", cookie)
        self.assertIn("csrftoken=abc", cookie)

    def test_the_fb_dtsg_token_goes_in_the_body(self):
        """Sem ele a chamada nao passa: medido contra o endpoint real, o
        Instagram devolve a pagina do perfil em vez do feed."""
        body = self._capture("", jar_session="9876%3Axyz")["body"]
        self.assertEqual(body.get("fb_dtsg"), "dtsg")
        self.assertEqual(body.get("jazoest"), ig_profile._jazoest("dtsg"))
        self.assertEqual(body.get("doc_id"), ig_profile.PROFILE_DOC_ID)

    def test_the_call_does_not_go_out_without_the_impersonating_client(self):
        """Sem curl_cffi a recusa tem de NOMEAR a dependencia.

        O sintoma — `error 1357054` num HTTP 200 — nao aponta para um pacote
        ausente, e a distancia entre os dois e o que faz o usuario tentar de
        novo em vez de instalar.
        """
        with mock.patch.object(ig_profile, "_curl_requests", None):
            hint = ig_profile._refusal_hint()
        self.assertIn("curl_cffi", hint)
        self.assertIn("pip install", hint)


class RefusedCallTests(unittest.TestCase):
    """O corpo do ``/graphql/query`` nao e JSON puro, e a recusa vem em 200.

    Dois defeitos opostos moram aqui. O prefixo ``for (;;);`` faz uma resposta
    BOA parecer HTML — o Instagram prefixa todo corpo com ele, e o teste
    ``startswith("{")`` reprovava a resposta certa. E a recusa chega com HTTP
    200 e um envelope ``__ar``: sem ler o envelope, o usuario recebe "o feed veio
    vazio", que nao nomeia conserto nenhum.
    """

    def _request(self, body: bytes) -> dict:
        def fake_send(method, url, *, data=None, headers=None, timeout=30):
            return 200, body.decode("utf-8")

        with mock.patch.object(ig_profile, "_send", side_effect=fake_send):
            return ig_profile._request_page(
                {"csrftoken": "abc", "sessionid": "1%3A2"}, "lsd", "dtsg", "alvo", None, 12
            )

    def test_a_plain_json_body_parses(self):
        self.assertEqual(self._request(b'{"data": {"x": 1}}'), {"data": {"x": 1}})

    def test_the_anti_hijacking_prefix_comes_off(self):
        """Sem isto, toda resposta BOA virava 'HTTP HTML'."""
        body = b'for (;;);{"data": {"x": 1}}'
        self.assertEqual(self._request(body), {"data": {"x": 1}})

    def test_the_prefix_is_optional(self):
        """Nada garante que o prefixo esteja la — os dois formatos tem de passar."""
        self.assertEqual(self._strip(b'for (;;);{"a": 1}'), '{"a": 1}')
        self.assertEqual(self._strip(b'  {"a": 1}'), '{"a": 1}')

    @staticmethod
    def _strip(raw: bytes) -> str:
        return ig_profile._strip_json_guard(raw.decode("utf-8"))

    def test_a_refused_envelope_names_the_error_and_the_doc_id(self):
        body = (
            b'for (;;);{"__ar":1,"error":1357004,'
            b'"errorSummary":"Sorry, something went wrong","payload":null}'
        )
        with self.assertRaises(ClipperError) as ctx:
            self._request(body)
        message = str(ctx.exception)
        self.assertIn("1357004", message)
        self.assertIn("doc_id", message)
        # A recusa tambem acontece quando o Instagram esta limitando: sem esta
        # segunda hipotese o usuario so tentaria de novo mais tarde por sorte.
        self.assertIn("limitando", message)

    def test_an_html_body_still_reads_as_a_refusal(self):
        with self.assertRaises(ClipperError) as ctx:
            self._request(b"<!DOCTYPE html><html>...</html>")
        self.assertIn("HTTP HTML", str(ctx.exception))


class RedirectTests(unittest.TestCase):
    """Uma sessao morta responde 302, e o 302 aponta para a propria requisicao.

    Medido: com um ``sessionid`` expirado — ou com um deliberadamente falso, que
    da no mesmo — o Instagram responde ``302`` com ``Location: /#`` e um
    ``Set-Cookie: sessionid=...; Max-Age=0``. Como ``#`` nunca sai do navegador,
    ``GET /#`` e ``GET /`` no fio, entao o alvo e a propria requisicao e o curl
    desiste em ``curl: (47) Maximum (30) redirects followed``. Essa string nao
    nomeia conserto nenhum, e era ela que o usuario lia.
    """

    def test_the_transport_does_not_follow_redirects(self):
        """Sem isto o 3xx nunca chega como dado: vira laco, vira excecao."""
        seen = {}

        class _Client:
            @staticmethod
            def request(method, url, **kwargs):
                seen.update(kwargs)
                return mock.Mock(status_code=200, text="ok")

        with mock.patch.object(ig_profile, "_curl_requests", _Client):
            self.assertEqual(ig_profile._send("GET", "https://x/"), (200, "ok"))
        self.assertIs(seen.get("allow_redirects"), False)

    def test_a_redirect_loop_becomes_a_sentence_not_curl_text(self):
        """Se um curl_cffi futuro ignorar ``allow_redirects``, o texto cru
        ainda nao pode ser o que o usuario le."""
        if ig_profile._TooManyRedirects is None:
            self.skipTest("curl_cffi ausente: sem a classe, nao ha o que traduzir")

        class _Client:
            @staticmethod
            def request(method, url, **kwargs):
                raise ig_profile._TooManyRedirects(
                    "Failed to perform, curl: (47) Maximum (30) redirects followed"
                )

        with mock.patch.object(ig_profile, "_curl_requests", _Client):
            with self.assertRaises(ClipperError) as ctx:
                ig_profile._send("GET", "https://www.instagram.com/alvo/")
        message = str(ctx.exception)
        self.assertIn("expirou", message)
        self.assertNotIn("curl:", message)

    def test_a_missing_error_class_degrades_instead_of_raising_type_error(self):
        """The class can be gone while the client is still there — the move the
        comment on the import anticipates. The guard must then catch *nothing*
        rather than ``None``: ``except None:`` is only evaluated when something
        is raised, and it dies with ``TypeError``, which replaces the original
        exception and is not an ``OSError``, so it would escape the callers'
        ``except OSError`` arms. The test above skips in this state; this one
        forces it.

        ``_REDIRECT_ERRORS = ()`` is exactly what the import block produces when
        the class is missing, so patching the derived tuple is the same state
        without re-running the import. Raising the *real* class (not a bare
        ``OSError``) is what makes the assertion bite: if ``_send`` went back to
        naming the class directly, the patch would stop mattering, the loop
        would be caught and translated, and the ``ClipperError`` would blow out
        of the ``try`` below.
        """
        real = ig_profile._TooManyRedirects
        if real is None:
            self.skipTest("curl_cffi ausente: nao ha a classe para forcar o estado")
        original = real("Failed to perform, curl: (47) Maximum (30) redirects followed")

        class _Client:
            @staticmethod
            def request(method, url, **kwargs):
                raise original

        with mock.patch.object(ig_profile, "_TooManyRedirects", None), mock.patch.object(
            ig_profile, "_REDIRECT_ERRORS", ()
        ), mock.patch.object(ig_profile, "_curl_requests", _Client):
            try:
                ig_profile._send("GET", "https://www.instagram.com/alvo/")
            except TypeError as exc:
                self.fail(f"'except None' virou TypeError: {exc}")
            except OSError as exc:
                # No class to translate with, so the original travels the path
                # it travelled before — degraded, but never a TypeError.
                self.assertIs(exc, original)
            else:
                self.fail("a excecao original sumiu: nada foi levantado")

    def test_the_guard_is_a_tuple_not_a_bare_class(self):
        """A bare class works today and becomes ``except None`` on the day the
        import moves — the exact defect this guard closes. Asserted on the
        module's own value, because the test above patches it and would hide it.
        """
        self.assertIsInstance(ig_profile._REDIRECT_ERRORS, tuple)

    def test_the_profile_page_redirect_reads_as_an_expired_session(self):
        """Sem o ramo de 3xx o corpo vazio passa adiante e o usuario ouve que
        falta o token anti-CSRF — verdade, mas com o culpado errado."""
        with mock.patch.object(ig_profile, "_send", return_value=(302, "")):
            with self.assertRaises(ClipperError) as ctx:
                ig_profile._fetch_lsd({"sessionid": "s"}, "alvo")
        message = str(ctx.exception)
        self.assertIn("instagram.com/alvo/", message)
        self.assertIn("expirou", message)
        self.assertNotIn("LSD", message)

    def test_the_graphql_redirect_reads_as_an_expired_session(self):
        with mock.patch.object(ig_profile, "_send", return_value=(302, "")):
            with self.assertRaises(ClipperError) as ctx:
                ig_profile._request_page(
                    {"csrftoken": "abc", "sessionid": "1%3A2"},
                    "lsd",
                    "dtsg",
                    "alvo",
                    None,
                    12,
                )
        message = str(ctx.exception)
        self.assertIn("expirou", message)
        self.assertIn("302", message)


class DtsgExtractionTests(unittest.TestCase):
    """O token ``fb_dtsg`` mudou de bolso dentro da propria pagina.

    ``DTSGInitialData`` continua na pagina, mas agora vem vazio; o valor de
    verdade viaja em ``MRequestConfig``. Lendo so o caminho antigo, a busca por
    perfil falha com "nao encontrei o token" mesmo com a sessao boa — o pior
    tipo de erro, porque acusa a sessao quando o problema e o extrator.
    """

    def _dtsg(self, extra: str) -> str:
        head = '<script>window.__b = {"LSD",[],{"token":"LSDTOKEN"}};</script>'
        with mock.patch.object(ig_profile, "_send", return_value=(200, head + extra)):
            return ig_profile._fetch_lsd({"sessionid": "s"}, "alvo")[1]

    def test_the_token_is_read_from_m_request_config(self):
        token = self._dtsg(
            '<script>window.__c = {"MRequestConfig",[],'
            '{"dtsg":{"token":"FROM_MREQUEST"}}};</script>'
        )
        self.assertEqual(token, "FROM_MREQUEST")

    def test_the_old_spelling_still_works_as_a_fallback(self):
        """``ig_session_check.py`` ainda alimenta a grafia antiga."""
        token = self._dtsg(
            '<script>window.__b = {"DTSGInitialData",[],{"token":"OLD_TOKEN"}};</script>'
        )
        self.assertEqual(token, "OLD_TOKEN")

    def test_an_empty_envelope_does_not_shadow_the_new_path(self):
        """A ordem importa: o envelope vazio pode vir antes, e uma busca que
        aceitasse o vazio devolveria ``None``."""
        token = self._dtsg(
            '<script>{"DTSGInitialData",[],{}}</script>'
            '<script>{"MRequestConfig",[],{"dtsg":{"token":"REAL"}}}</script>'
        )
        self.assertEqual(token, "REAL")


class UrllibFallbackTests(unittest.TestCase):
    """O ramo `urllib` (quando o curl_cffi nao esta' instalado) tem de honrar
    `allow_redirects`, senao a flag significa uma coisa no transporte primario e
    outra no de reserva — e o de reserva seguiria o mesmo laco, so' com teto 10
    em vez de 30.

    Offline por construcao: `build_opener` e' substituido, entao nada aqui abre
    socket. O que se mede e' QUAL handler o ramo instala, e que ele devolve
    `None` de `redirect_request` — que e' o que faz o urllib levantar o 3xx como
    `HTTPError` em vez de seguir. A paridade observavel dos dois transportes foi
    medida a parte, com um servidor local: 302 como dado nos dois, nos dois
    sentidos da flag.
    """

    def _run(self, allow_redirects: bool):
        seen = {}

        class _FakeResponse:
            status = 302

            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeOpener:
            # Assinatura larga de proposito: o `urlopen` real chama
            # `opener.open(url, data, timeout)` na posicao, entao um fake de
            # aridade fixa explodiria com TypeError e a sabotagem falharia pelo
            # motivo errado — em vez de mostrar que `build_opener` nunca foi
            # chamado com handler nenhum.
            def open(self, *args, **kwargs):
                return _FakeResponse()

        def fake_build_opener(*handlers):
            seen["handlers"] = handlers
            return _FakeOpener()

        with mock.patch.object(ig_profile, "_curl_requests", None), mock.patch(
            "urllib.request.build_opener", fake_build_opener
        ):
            status, text = ig_profile._send(
                "GET", "https://x/", allow_redirects=allow_redirects
            )
        return seen.get("handlers"), status, text

    def test_the_fallback_installs_a_handler_that_does_not_follow(self):
        from urllib.request import HTTPRedirectHandler

        handlers, status, _ = self._run(False)
        self.assertIsNotNone(
            handlers, "o ramo urllib nao instalou handler nenhum: ignorou a flag"
        )
        self.assertEqual(len(handlers), 1)
        handler = handlers[0]()
        # Subclasse, e nao o handler padrao: e' assim que `build_opener` troca o
        # comportamento em vez de empilhar dois.
        self.assertIsInstance(handler, HTTPRedirectHandler)
        self.assertIsNone(
            handler.redirect_request(None, None, 302, "Found", {}, "https://x/#")
        )

    def test_the_fallback_still_follows_when_asked(self):
        """`allow_redirects=True` nao pode ser esquecido pelo ramo de reserva."""
        handlers, _, _ = self._run(True)
        self.assertIsNotNone(
            handlers, "o ramo urllib nao passou por build_opener"
        )
        self.assertEqual(handlers, ())


class RedirectReasonTests(unittest.TestCase):
    """Um 3xx qualquer nao e' a recusa medida.

    `302`/`303` e' a assinatura que o Instagram da' quando recusa a sessao
    (corpo vazio, `Location` apontando para `/#`). `301`/`307`/`308` seriam um
    movimento de verdade, e chama-los de "sessao expirou" mandaria o usuario
    reexportar cookies que nunca foram o problema.
    """

    def test_the_measured_refusal_is_reported_as_an_expired_session(self):
        for status in (302, 303):
            self.assertIn("expirou", ig_profile._redirect_reason(status))

    def test_another_redirect_is_not_blamed_on_the_session(self):
        for status in (301, 307, 308):
            reason = ig_profile._redirect_reason(status)
            self.assertNotIn("expirou", reason)
            self.assertIn(str(status), reason)

    def test_the_page_and_the_graphql_call_agree_on_the_same_302(self):
        """Duas telas do mesmo dado nao podem ter duas fontes."""
        with mock.patch.object(ig_profile, "_send", return_value=(302, "")):
            with self.assertRaises(ClipperError) as ctx:
                ig_profile._fetch_lsd({"sessionid": "s"}, "alvo")
        self.assertIn("expirou", str(ctx.exception))
        self.assertIn("expirou", ig_profile._graphql_error(302, "alvo"))

    def test_the_graphql_call_does_not_blame_the_session_on_a_301(self):
        self.assertNotIn("expirou", ig_profile._graphql_error(301, "alvo"))


class PlayCountTests(unittest.TestCase):
    """The view count the timeline query does not carry.

    Measured 2026-10-17: the profile GraphQL answers ``view_count: null`` on
    every node and omits ``play_count`` entirely, so "most viral first" had
    only likes to sort on. The real number lives in
    ``/api/v1/media/<pk>/info/`` — one request per item, because no batch shape
    a web client can use exists (user feed answers 302, ``media/info`` 404).
    """

    def _items(self, *codes, photo=()):
        items = [
            ProfileItem(
                code=code,
                url=f"https://www.instagram.com/reel/{code}/",
                folder=REELS_DIR,
                kind="reel",
                media_type=2,
                pk=f"pk{code}",
            )
            for code in codes
        ]
        items.extend(
            ProfileItem(
                code=code,
                url=f"https://www.instagram.com/p/{code}/",
                folder=POSTS_DIR,
                kind="post",
                media_type=1,
                pk=f"pk{code}",
            )
            for code in photo
        )
        return items

    def _answers(self, mapping):
        """Fake ``_send`` that answers per pk, recording every URL asked."""
        seen: list[str] = []

        def fake_send(method, url, *, data=None, headers=None, timeout=30):
            seen.append(url)
            pk = url.split("/media/", 1)[1].split("/", 1)[0]
            return mapping.get(pk, (404, ""))

        return fake_send, seen

    def test_the_view_count_comes_from_the_media_record(self):
        """``play_count`` first, ``ig_play_count`` when the first is absent."""
        items = self._items("A", "B")
        fake, seen = self._answers(
            {
                "pkA": (200, json.dumps({"items": [{"play_count": 1375106}]})),
                "pkB": (200, json.dumps({"items": [{"ig_play_count": 77}]})),
            }
        )
        with mock.patch.object(ig_profile, "_send", side_effect=fake):
            filled = ig_profile.fetch_play_counts(items, {"sessionid": "s"})
        self.assertEqual(filled, 2)
        self.assertEqual([i.play_count for i in items], [1375106, 77])
        self.assertEqual(len(seen), 2)

    def test_the_request_carries_the_session_and_the_app_id(self):
        """Without the session the endpoint answers 302, and the count is gone."""
        items = self._items("A")
        captured = {}

        def fake_send(method, url, *, data=None, headers=None, timeout=30):
            captured["headers"] = headers or {}
            return 200, json.dumps({"items": [{"play_count": 1}]})

        with mock.patch.object(ig_profile, "_send", side_effect=fake_send):
            ig_profile.fetch_play_counts(items, {"sessionid": "segredo"})
        self.assertIn("sessionid=segredo", captured["headers"]["Cookie"])
        self.assertEqual(captured["headers"]["X-IG-App-ID"], ig_profile.APP_ID)

    def test_a_photo_is_not_asked_because_it_has_no_play_count(self):
        """A request spent to learn "a photo has no views" is a request wasted."""
        items = self._items("A", photo=("P",))
        fake, seen = self._answers(
            {"pkA": (200, json.dumps({"items": [{"play_count": 5}]}))}
        )
        with mock.patch.object(ig_profile, "_send", side_effect=fake):
            ig_profile.fetch_play_counts(items, {"sessionid": "s"})
        self.assertEqual(len(seen), 1)
        self.assertNotIn("pkP", " ".join(seen))

    def test_a_count_that_is_already_there_is_not_overwritten(self):
        items = self._items("A")
        items[0].play_count = 42
        fake, seen = self._answers({})
        with mock.patch.object(ig_profile, "_send", side_effect=fake):
            filled = ig_profile.fetch_play_counts(items, {"sessionid": "s"})
        self.assertEqual(filled, 0)
        self.assertEqual(seen, [])
        self.assertEqual(items[0].play_count, 42)

    def test_a_429_stops_asking_instead_of_deepening_the_hole(self):
        """Rate limited: keep what came back and stop, do not retry 100 times."""
        items = self._items("A", "B", "C", "D")
        seen: list[str] = []

        def rate_limited(method, url, *, data=None, headers=None, timeout=30):
            seen.append(url)
            return 429, ""

        with mock.patch.object(ig_profile, "_send", side_effect=rate_limited):
            filled = ig_profile.fetch_play_counts(items, {"sessionid": "s"}, workers=1)
        self.assertEqual(filled, 0)
        self.assertEqual(len(seen), 1)

    def test_an_html_answer_is_not_a_view_count(self):
        items = self._items("A")
        fake, _ = self._answers({"pkA": (200, "<!DOCTYPE html><html></html>")})
        with mock.patch.object(ig_profile, "_send", side_effect=fake):
            ig_profile.fetch_play_counts(items, {"sessionid": "s"})
        self.assertIsNone(items[0].play_count)

    def test_a_network_error_does_not_take_the_listing_down(self):
        """A missing count degrades the ranking; it must not fail the search."""
        items = self._items("A")

        def boom(*args, **kwargs):
            raise OSError("sem rede")

        with mock.patch.object(ig_profile, "_send", side_effect=boom):
            filled = ig_profile.fetch_play_counts(items, {"sessionid": "s"})
        self.assertEqual(filled, 0)
        self.assertIsNone(items[0].play_count)


class PaginationTests(unittest.TestCase):
    """The cursor is not a terminator: Instagram repeats the FIRST one."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_ig_page_"))

    def _run(self, documents, **kwargs):
        calls = {"n": 0}

        def fake_page(cookies, lsd, dtsg, username, cursor, page_size):
            index = calls["n"]
            calls["n"] += 1
            if index >= len(documents):
                raise AssertionError("pediu mais paginas do que o roteiro tem")
            return documents[index]

        with mock.patch.object(ig_profile, "_parse_cookie_file", return_value={"sessionid": "s"}), \
             mock.patch.object(
                 ig_profile, "_fetch_lsd", return_value=("lsd", "dtsg", "<html></html>")
             ), \
             mock.patch.object(ig_profile, "_request_page", side_effect=fake_page):
            listing = list_profile("alvo", self.dir / "x.txt", pause=0, **kwargs)
        return listing, calls["n"]

    def test_two_pages_then_a_repeated_cursor_stops(self):
        """Page 3 brings nothing new, so the loop ends — cursor or not."""
        listing, pages = self._run([
            page(["A", "B"], cursor="CUR1"),
            page(["C", "D"], cursor="CUR1"),  # o mesmo cursor de novo
            page(["A", "B"], cursor="CUR1"),  # repetiria para sempre
        ])
        self.assertEqual([i.code for i in listing.items], ["A", "B", "C", "D"])
        self.assertEqual(pages, 2)

    def test_a_page_that_adds_nothing_ends_the_loop(self):
        listing, pages = self._run([
            page(["A", "B"], cursor="CUR1"),
            page(["A", "B"], cursor="CUR2"),  # so repetidos
        ])
        self.assertEqual([i.code for i in listing.items], ["A", "B"])
        self.assertEqual(pages, 2)

    def test_a_null_cursor_ends_the_loop(self):
        listing, pages = self._run([page(["A"], cursor=None)])
        self.assertEqual(len(listing.items), 1)
        self.assertEqual(pages, 1)

    def test_the_limit_truncates_and_marks_it(self):
        listing, pages = self._run(
            [page(["A", "B", "C", "D"], cursor="CUR1")], limit=2
        )
        self.assertEqual([i.code for i in listing.items], ["A", "B"])
        self.assertTrue(listing.truncated)
        self.assertEqual(pages, 1)

    def test_the_same_pk_across_pages_is_counted_once(self):
        listing, _ = self._run([
            page(["A"], cursor="CUR1"),
            {"data": {ig_profile.ROOT_FIELD: {
                "edges": [{"node": node("A2", pk="pk_A")}],
                "page_info": {"end_cursor": None},
            }}},
        ])
        self.assertEqual(len(listing.items), 1)
        self.assertEqual(listing.items[0].code, "A")

    def test_the_summary_counts_reels_and_posts(self):
        listing = ProfileListing(username="alvo", pages=3, items=[
            ProfileItem(code="R", url="", folder=REELS_DIR, kind="reel"),
            ProfileItem(code="P", url="", folder=POSTS_DIR, kind="post"),
            ProfileItem(code="P2", url="", folder=POSTS_DIR, kind="post"),
        ])
        self.assertEqual(listing.summary(), "@alvo: 3 itens (1 reels, 2 posts) em 3 pagina(s)")
        self.assertEqual(len(listing.reels), 1)
        self.assertEqual(len(listing.posts), 2)


if __name__ == "__main__":
    unittest.main()
