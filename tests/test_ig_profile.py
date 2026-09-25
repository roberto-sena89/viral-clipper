"""Tests for the Instagram profile lister.

Offline by construction: every seam that would touch the network —
``_fetch_lsd``, ``_request_page`` and the cookie jar — is replaced by a fake, so
the suite never talks to Instagram and never depends on a live session. The one
real-network verification lives in ``reframe_check.py``-style scripts, not here.
"""

from __future__ import annotations

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

        def fake_urlopen(request, timeout=None):
            captured["cookie"] = request.get_header("Cookie") or ""
            return self._FakeResponse()

        directory = Path(tempfile.mkdtemp(prefix="vc_wire_"))
        jar = directory / "cookies.txt"
        lines = [".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc"]
        if jar_session:
            lines.append(f"#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\t{jar_session}")
        jar.write_text(
            "# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
        cookies = ig_profile.resolve_cookies(jar, session)
        # `urlopen` e importado DENTRO da funcao (from urllib.request import ...),
        # entao o alvo do patch e o modulo de origem, nao o ig_profile.
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ig_profile._request_page(cookies, "lsd", "alvo", None, 50)
        return captured["cookie"]

    def test_the_pasted_session_travels_with_the_csrf_token(self):
        cookie = self._capture(ig_profile.normalise_session("1234:abc"))
        self.assertIn("sessionid=1234%3Aabc", cookie)
        self.assertIn("csrftoken=abc", cookie)

    def test_a_jar_session_alone_is_enough(self):
        """Sem colar nada: o que ja esta no arquivo continua valendo.

        Nao e um caso hipotetico — e o segundo run em diante, quando a sessao
        ja foi gravada pelo :func:`prepare_session` e o campo do painel volta
        vazio.
        """
        cookie = self._capture("", jar_session="9876%3Axyz")
        self.assertIn("sessionid=9876%3Axyz", cookie)
        self.assertIn("csrftoken=abc", cookie)


class PaginationTests(unittest.TestCase):
    """The cursor is not a terminator: Instagram repeats the FIRST one."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_ig_page_"))

    def _run(self, documents, **kwargs):
        calls = {"n": 0}

        def fake_page(cookies, lsd, username, cursor, page_size):
            index = calls["n"]
            calls["n"] += 1
            if index >= len(documents):
                raise AssertionError("pediu mais paginas do que o roteiro tem")
            return documents[index]

        with mock.patch.object(ig_profile, "_parse_cookie_file", return_value={"sessionid": "s"}), \
             mock.patch.object(ig_profile, "_fetch_lsd", return_value=("lsd", "<html></html>")), \
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
