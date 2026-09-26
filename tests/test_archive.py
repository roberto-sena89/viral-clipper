"""Tests for the profile archiver.

The downloader is injected everywhere, so the loop — folder split, the photo
skip, resumability, per-item failure — is exercised without a network, without
yt-dlp and without ffmpeg. What a real binary adds on top is checked by the
scripts in the repo root, not here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from viralclipper import archive
from viralclipper.archive import (
    ArchiveSummary,
    archive_profile,
    item_filename,
    item_path,
    safe_slug,
    select_items,
)
from viralclipper.config import ClipConfig
from viralclipper.ig_profile import POSTS_DIR, REELS_DIR, ProfileItem, ProfileListing
from viralclipper.util import ClipperError


def item(code, folder=REELS_DIR, *, caption="", media_type=2, **extra):
    kind = "reel" if folder == REELS_DIR else "post"
    return ProfileItem(
        code=code, pk="pk_" + code, url=f"https://www.instagram.com/x/{code}/",
        folder=folder, kind=kind, media_type=media_type, caption=caption, **extra,
    )


class FakeDownloader:
    """Records every call and writes the file, or fails on demand."""

    def __init__(self, fail_on=()):
        self.calls: list[tuple[str, Path]] = []
        self.fail_on = set(fail_on)
        self.lines: list[str] = []

    def __call__(self, url, target, config, logger, *, on_line=None):
        self.calls.append((url, Path(target)))
        code = url.rstrip("/").rsplit("/", 1)[-1]
        if code in self.fail_on:
            raise ClipperError("video privado")
        if on_line is not None:
            on_line("[download]  54.9% of 10.00MiB")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix(".mp4").write_bytes(b"x")
        return target.with_suffix(".mp4")


class SafeSlugTests(unittest.TestCase):
    def test_unsafe_characters_become_spaces(self):
        self.assertEqual(safe_slug('a/b\\c:d*e?f"g<h>i|j', "FB"), "a b c d e f g h i j")

    def test_empty_falls_back_to_the_shortcode(self):
        self.assertEqual(safe_slug("", "ABC123"), "ABC123")
        self.assertEqual(safe_slug("   ", "ABC123"), "ABC123")

    def test_a_reserved_device_name_is_prefixed(self):
        """``CON.mp4`` cannot be created on Windows at all."""
        self.assertEqual(safe_slug("CON", "ABC123"), "ABC123-CON")
        self.assertEqual(safe_slug("lpt1", "ABC123"), "ABC123-lpt1")

    def test_the_length_is_capped(self):
        self.assertEqual(len(safe_slug("a" * 500, "FB")), 48)

    def test_trailing_dots_are_stripped(self):
        """Windows silently drops a trailing dot, so the name would not match."""
        self.assertEqual(safe_slug("titulo...", "FB"), "titulo")


class FilenameTests(unittest.TestCase):
    def test_the_shortcode_leads_the_name(self):
        name = item_filename(item("ABC123", caption="Bom dia"))
        self.assertEqual(name, "ABC123 - Bom dia.mp4")

    def test_an_empty_caption_does_not_leave_a_dangling_dash(self):
        self.assertEqual(item_filename(item("ABC123")), "ABC123 - ABC123.mp4")

    def test_the_path_follows_the_folder(self):
        reel = item_path("out", item("R1", REELS_DIR))
        post = item_path("out", item("P1", POSTS_DIR))
        self.assertEqual(reel, Path("out") / "reels" / "R1 - R1.mp4")
        self.assertEqual(post, Path("out") / "posts" / "P1 - P1.mp4")


class ArchiveLoopTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vc_arch_"))
        self.config = ClipConfig(url="", output_dir=self.dir)

    def _listing(self, items, username="alvo"):
        return ProfileListing(username=username, items=list(items), pages=1)

    def test_reels_and_posts_land_in_their_own_folders(self):
        fetch = FakeDownloader()
        listing = self._listing([
            item("R1", REELS_DIR), item("P1", POSTS_DIR), item("R2", REELS_DIR),
        ])
        summary = archive_profile(listing, self.dir, self.config, downloader=fetch)

        self.assertEqual(summary.downloaded, 3)
        self.assertEqual(summary.reels, 2)
        self.assertEqual(summary.posts, 1)
        self.assertEqual(
            sorted(p.name for p in (self.dir / "reels").glob("*.mp4")), ["R1 - R1.mp4", "R2 - R2.mp4"]
        )
        self.assertEqual(
            sorted(p.name for p in (self.dir / "posts").glob("*.mp4")), ["P1 - P1.mp4"]
        )

    def test_a_photo_is_skipped_without_touching_the_network(self):
        """The listing already knows, so no doomed extraction is paid for.

        Asking yt-dlp for a still answers "There is no video in this post" only
        after a full round trip — one per photo, on a feed that can be mostly
        photos.
        """
        fetch = FakeDownloader()
        listing = self._listing([
            item("PHOTO", POSTS_DIR, media_type=1),
            item("R1", REELS_DIR),
        ])
        summary = archive_profile(listing, self.dir, self.config, downloader=fetch)

        self.assertEqual(summary.photos, 1)
        self.assertEqual(summary.downloaded, 1)
        self.assertEqual([url for url, _ in fetch.calls], ["https://www.instagram.com/x/R1/"])

    def test_a_leftover_fragment_is_not_taken_for_a_download(self):
        """An interrupted item must be retried, not skipped for good.

        A run killed between the download and the merge leaves
        ``<name>.fdash-<id><v|a>.<ext>`` on disk, and a bare suffix test reads
        that as the finished reel: the item is skipped forever while the folder
        holds half a download.
        """
        folder = self.dir / "reels"
        folder.mkdir(parents=True)
        (folder / "R1 - R1.fdash-4626875630869503v.mp4").write_bytes(b"video")
        (folder / "R1 - R1.fdash-1320620103357609a.m4a").write_bytes(b"audio")

        fetch = FakeDownloader()
        summary = archive_profile(
            self._listing([item("R1", REELS_DIR)]), self.dir, self.config, downloader=fetch
        )

        self.assertEqual(summary.skipped, 0)
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual(summary.downloaded, 1)

    def test_a_second_pass_skips_what_is_on_disk(self):
        listing = self._listing([item("R1", REELS_DIR), item("R2", REELS_DIR)])
        first = archive_profile(listing, self.dir, self.config, downloader=FakeDownloader())
        self.assertEqual(first.downloaded, 2)

        second_fetch = FakeDownloader()
        second = archive_profile(listing, self.dir, self.config, downloader=second_fetch)
        self.assertEqual(second.skipped, 2)
        self.assertEqual(second.downloaded, 0)
        self.assertEqual(second_fetch.calls, [])

    def test_overwrite_ignores_what_is_on_disk(self):
        listing = self._listing([item("R1", REELS_DIR)])
        archive_profile(listing, self.dir, self.config, downloader=FakeDownloader())

        fetch = FakeDownloader()
        summary = archive_profile(
            listing, self.dir, self.config, downloader=fetch, overwrite=True
        )
        self.assertEqual(summary.downloaded, 1)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(len(fetch.calls), 1)

    def test_an_edited_caption_still_counts_as_already_there(self):
        """Resumability is by shortcode, so a renamed tail must not re-download."""
        archive_profile(
            self._listing([item("R1", REELS_DIR, caption="Bom dia")]),
            self.dir, self.config, downloader=FakeDownloader(),
        )
        (self.dir / "reels" / "R1 - Bom dia.mp4").rename(
            self.dir / "reels" / "R1 - outro titulo.mp4"
        )
        fetch = FakeDownloader()
        summary = archive_profile(
            self._listing([item("R1", REELS_DIR, caption="Bom dia")]),
            self.dir, self.config, downloader=fetch,
        )
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(fetch.calls, [])

    def test_one_failure_does_not_stop_the_run(self):
        fetch = FakeDownloader(fail_on={"R2"})
        listing = self._listing([item("R1", REELS_DIR), item("R2", REELS_DIR), item("R3", REELS_DIR)])
        summary = archive_profile(listing, self.dir, self.config, downloader=fetch)

        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.downloaded, 2)
        self.assertEqual(summary.errors, [("R2", "video privado")])
        self.assertTrue((self.dir / "reels" / "R3 - R3.mp4").is_file())

    def test_progress_reports_every_phase_in_order(self):
        seen: list[tuple[int, str, str]] = []
        listing = self._listing([
            item("R1", REELS_DIR),
            item("PHOTO", POSTS_DIR, media_type=1),
            item("R2", REELS_DIR),
        ])
        archive_profile(
            listing, self.dir, self.config, downloader=FakeDownloader(fail_on={"R2"}),
            on_progress=lambda pos, total, code, phase: seen.append((pos, code, phase)),
        )
        self.assertEqual(seen, [
            (1, "R1", "item"), (1, "R1", "done"),
            (2, "PHOTO", "photo"),
            (3, "R2", "item"), (3, "R2", "failed"),
        ])

    def test_a_broken_progress_callback_cannot_break_the_run(self):
        """The bar is decoration; a raise there would lose the whole archive."""
        def explode(*_args):
            raise RuntimeError("callback quebrado")

        listing = self._listing([item("R1", REELS_DIR)])
        summary = archive_profile(
            listing, self.dir, self.config, downloader=FakeDownloader(), on_progress=explode
        )
        self.assertEqual(summary.downloaded, 1)

    def test_the_lines_report_the_folders(self):
        summary = archive_profile(
            self._listing([item("R1", REELS_DIR), item("P1", POSTS_DIR)]),
            self.dir, self.config, downloader=FakeDownloader(),
        )
        report = "\n".join(summary.lines())
        self.assertIn("Perfil @alvo: 2 item(ns) na lista", report)
        self.assertIn("baixados : 2", report)
        self.assertIn("pastas   : 1 em reels/, 1 em posts/", report)
        self.assertIn("destino", report)


class SelectItemsTests(unittest.TestCase):
    def setUp(self):
        self.listing = ProfileListing(username="alvo", items=[
            item("R1", REELS_DIR, play_count=10),
            item("P1", POSTS_DIR, play_count=900),
            item("R2", REELS_DIR, play_count=500),
            item("P2", POSTS_DIR, play_count=5),
        ])

    def test_no_filter_keeps_everything_in_order(self):
        kept = select_items(self.listing)
        self.assertEqual([i.code for i in kept], ["R1", "P1", "R2", "P2"])

    def test_a_kind_filter_keeps_only_that_folder(self):
        self.assertEqual([i.code for i in select_items(self.listing, kinds=["reels"])],
                         ["R1", "R2"])
        self.assertEqual([i.code for i in select_items(self.listing, kinds=["posts"])],
                         ["P1", "P2"])

    def test_the_limit_applies_per_folder(self):
        """A feed dominated by reels must not starve the posts out of the run."""
        kept = select_items(self.listing, limit=1)
        self.assertEqual(sorted(i.code for i in kept), ["P1", "R1"])

    def test_viral_orders_by_engagement_before_the_cap(self):
        kept = select_items(self.listing, kinds=["reels"], order="viral", limit=1)
        self.assertEqual([i.code for i in kept], ["R2"])

    def test_viral_falls_back_to_likes_then_comments(self):
        listing = ProfileListing(username="alvo", items=[
            ProfileItem(code="A", url="", folder=REELS_DIR, kind="reel",
                        play_count=None, like_count=5, comment_count=0),
            ProfileItem(code="B", url="", folder=REELS_DIR, kind="reel",
                        play_count=None, like_count=5, comment_count=9),
        ])
        kept = select_items(listing, order="viral", limit=1)
        self.assertEqual([i.code for i in kept], ["B"])

    def test_an_unknown_order_is_chronological(self):
        """Only "viral" reorders; anything else keeps Instagram's order.

        With the per-folder cap of 1 the first of each folder is what survives,
        which is exactly the newest of each — the chronological answer.
        """
        kept = select_items(self.listing, order="qualquer", limit=1)
        self.assertEqual([i.code for i in kept], ["R1", "P1"])

    def test_the_input_list_is_not_mutated(self):
        before = [i.code for i in self.listing.items]
        select_items(self.listing, order="viral", limit=1)
        self.assertEqual([i.code for i in self.listing.items], before)


class SummaryTests(unittest.TestCase):
    def test_the_photo_line_only_appears_when_there_were_photos(self):
        self.assertFalse(any("pulados" in line for line in ArchiveSummary().lines()))
        self.assertTrue(any("pulados" in line for line in ArchiveSummary(photos=3).lines()))

    def test_only_ten_errors_are_listed(self):
        summary = ArchiveSummary(errors=[(f"i{n}", "x") for n in range(14)])
        lines = summary.lines()
        self.assertEqual(sum(1 for line in lines if line.startswith("    ! ")), 10)
        self.assertIn("... e mais 4 falha(s)", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
