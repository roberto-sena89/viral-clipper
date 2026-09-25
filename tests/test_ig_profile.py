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

    def test_a_jar_without_sessionid_says_so(self):
        """An export that omits ``sessionid`` is the common failure.

        Chrome/Edge mark it HttpOnly and some exporters drop it, so the jar
        looks complete — 14 cookies, right domains — and the listing still
        refuses. The message has to name the missing cookie, not the file.
        """
        path = self._jar(
            self.HEADER
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n"
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tds_user_id\t42\n"
        )
        with self.assertRaises(ClipperError) as ctx:
            ig_profile._parse_cookie_file(path)
        self.assertIn("sessionid", str(ctx.exception))

    def test_a_complete_jar_is_read(self):
        path = self._jar(
            self.HEADER
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsegredo\n"
            + ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tabc\n"
        )
        jar = ig_profile._parse_cookie_file(path)
        self.assertEqual(jar["sessionid"], "segredo")
        self.assertEqual(jar["csrftoken"], "abc")


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
