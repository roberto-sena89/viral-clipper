"""Archive a whole Instagram profile into ``reels/`` and ``posts/`` folders.

The unit of work here is a *catalogue*, not a video: a profile has hundreds of
items, the download takes long enough to be interrupted, and the useful outcome
is "everything, sorted, resumable" rather than "the last one".

Three decisions shape this module:

* **The folder split is the deliverable.** ``reels/`` and ``posts/`` are what
  make the archive navigable, so the classification happens on the *listing*
  (from ``product_type``, which is authoritative) and the downloader only ever
  receives a folder name. If discovery ever had to change, the folders would
  not.
* **Resumability comes from the filesystem, not a database.** A media id is in
  the filename, so "already downloaded" is an ``exists()`` check. No manifest to
  corrupt, no state to migrate, and a file moved by hand is still recognised —
  which is exactly the failure mode an in-progress archive hits.
* **One failure is one item.** A private post, a deleted reel or a rate limit
  must cost that item, not the run. Each download is isolated and the summary
  reports what was skipped, so re-running picks up the stragglers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import download, ig_profile
from .config import ClipConfig
from .ig_profile import POSTS_DIR, REELS_DIR, ProfileItem, ProfileListing
from .util import ClipperError, Logger

#: Characters that are unsafe in a filename on Windows, plus the ones that make
#: a name awkward to type. A caption is attacker controlled text and ends up in
#: a path, so this is a correctness requirement, not cosmetics.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Reserved device names on Windows. A caption starting with "CON" would create
#: a file Windows cannot open at all.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}

#: How much of the caption becomes the filename. Long enough to recognise the
#: post, short enough that the full path stays under Windows' 260-character
#: limit even in a deeply nested output folder.
_SLUG_CHARS = 48


@dataclass
class ArchiveSummary:
    """What one archive run did."""

    username: str = ""
    total: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    photos: int = 0
    reels: int = 0
    posts: int = 0
    root: Path | None = None
    errors: list[tuple[str, str]] = field(default_factory=list)

    def lines(self) -> list[str]:
        lines = [
            f"Perfil @{self.username}: {self.total} item(ns) na lista",
            f"  baixados : {self.downloaded}",
            f"  ja tinha : {self.skipped}",
            f"  falhas   : {self.failed}",
            f"  pastas   : {self.reels} em {REELS_DIR}/, {self.posts} em {POSTS_DIR}/",
        ]
        if self.photos:
            lines.append(
                f"  pulados  : {self.photos} sem video (foto ou carrossel de fotos)"
            )
        if self.root is not None:
            lines.append(f"  destino  : {self.root}")
        for name, error in self.errors[:10]:
            lines.append(f"    ! {name}: {error}")
        if len(self.errors) > 10:
            lines.append(f"    ... e mais {len(self.errors) - 10} falha(s)")
        return lines


def safe_slug(text: str, fallback: str, limit: int = _SLUG_CHARS) -> str:
    """Turn a caption (or any text) into a filename component.

    Never returns an empty string and never returns something Windows refuses to
    open: ``fallback`` (the media shortcode) covers both the empty case and the
    reserved-device-name case.
    """
    cleaned = _UNSAFE.sub(" ", str(text or ""))
    # Collapse whitespace runs that the substitution just created.
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    cleaned = cleaned[:limit].strip().strip(".")
    if not cleaned:
        return fallback
    if cleaned.upper().split(".")[0] in _RESERVED:
        return f"{fallback}-{cleaned}"[:limit]
    return cleaned


def item_filename(item: ProfileItem) -> str:
    """``<shortcode> - <caption>.mp4``, stable across runs.

    The shortcode leads so the name is unique and greppable; the caption follows
    so a folder listing is readable without opening anything.
    """
    stem = safe_slug(item.caption, item.code or item.pk)
    prefix = item.code or item.pk
    return f"{prefix} - {stem}.mp4"


def item_path(root: str | Path, item: ProfileItem) -> Path:
    """Where one item belongs inside an archive rooted at ``root``."""
    return Path(root) / item.folder / item_filename(item)


def _already_there(folder: Path, item: ProfileItem) -> Path | None:
    """Existing file for this item, whatever the caption part says now.

    Matching on the shortcode prefix rather than the exact filename is what
    makes the skip check survive an edited caption, a changed slug length or a
    user who renamed the tail by hand.
    """
    if not folder.is_dir():
        return None
    prefix = item.code or item.pk
    if not prefix:
        return None
    for candidate in folder.glob(f"{prefix}*"):
        if candidate.is_file() and candidate.suffix.lower() == ".mp4":
            return candidate
    return None


def archive_profile(
    listing: ProfileListing,
    destination: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
    *,
    overwrite: bool = False,
    downloader: Callable[..., Path] | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> ArchiveSummary:
    """Download every item of ``listing`` into sorted, resumable folders.

    ``downloader`` is injected so the whole loop can be tested without a network
    or yt-dlp; production passes :func:`download.download_media`.

    ``on_progress`` is called as ``(position, total, code, phase)`` at every
    step — ``phase`` is one of ``item`` | ``skipped`` | ``photo`` | ``failed``
    | ``done`` — so a caller can paint a progress bar while the batch runs.

    ``on_line`` forwards every line yt-dlp writes while an item downloads, the
    same stream the batch download route already uses to turn ``[download]
    54.9%`` into a live percentage. Without it the archive bar freezes at
    "1 de 20" for the minutes a long reel takes, which is the one moment it is
    supposed to be useful.
    """
    fetch = downloader or download.download_media
    root = Path(destination)
    summary = ArchiveSummary(username=listing.username, total=len(listing.items), root=root)

    (root / REELS_DIR).mkdir(parents=True, exist_ok=True)
    (root / POSTS_DIR).mkdir(parents=True, exist_ok=True)

    def notify(position: int, code: str, phase: str) -> None:
        if on_progress is not None:
            try:
                on_progress(position, summary.total, code, phase)
            except Exception:  # noqa: BLE001 - progress must never break the run
                pass

    for position, item in enumerate(listing.items, start=1):
        folder = root / item.folder
        if item.folder == REELS_DIR:
            summary.reels += 1
        else:
            summary.posts += 1

        # A still has no video stream to fetch, and yt-dlp can only tell us that
        # by failing. The listing already knows, so it is skipped without a
        # network round trip. The shortcode is still recorded in the log,
        # because "which posts are photos" is a question the archive should be
        # able to answer without opening Instagram.
        if not item.has_video:
            summary.photos += 1
            notify(position, item.code or item.pk, "photo")
            if logger:
                logger.info(
                    f"[{position}/{summary.total}] {item.folder}/{item.code}: "
                    f"sem video, pulado"
                )
            continue

        existing = None if overwrite else _already_there(folder, item)
        if existing is not None:
            summary.skipped += 1
            notify(position, item.code or item.pk, "skipped")
            if logger:
                logger.info(f"[{position}/{summary.total}] já existe: {existing.name}")
            continue

        target = item_path(root, item)
        notify(position, item.code or item.pk, "item")
        if logger:
            logger.step(f"[{position}/{summary.total}] {item.folder}/{target.name}")

        try:
            # ``url`` is the bare /reel/ or /p/ link. yt-dlp resolves it without
            # the profile context, so a rate-limited profile page cannot block
            # an individual download.
            fetch_kwargs: dict = {}
            if on_line is not None:
                fetch_kwargs["on_line"] = on_line
            fetch(item.url, target.with_suffix(""), config, logger, **fetch_kwargs)
        except ClipperError as exc:
            summary.failed += 1
            summary.errors.append((item.code or item.pk, str(exc)))
            notify(position, item.code or item.pk, "failed")
            if logger:
                logger.warn(f"falhou: {item.code or item.pk}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one item must not stop the run
            summary.failed += 1
            summary.errors.append((item.code or item.pk, repr(exc)))
            notify(position, item.code or item.pk, "failed")
            if logger:
                logger.warn(f"falhou: {item.code or item.pk}: {exc!r}")
        else:
            summary.downloaded += 1
            notify(position, item.code or item.pk, "done")

    return summary


def _engagement(item: ProfileItem) -> tuple[float, float, float]:
    """Engagement score used by ``order="viral"``: plays, then likes, then comments.

    Missing counts count as zero. Uses getattr because test fakes do not
    carry every field a real GraphQL item has.
    """
    def num(value) -> float:
        return value if isinstance(value, (int, float)) else 0

    return (
        num(getattr(item, "play_count", 0)),
        num(getattr(item, "like_count", 0)),
        num(getattr(item, "comment_count", 0)),
    )


def select_items(
    listing: ProfileListing,
    *,
    kinds: Iterable[str] | None = None,
    limit: int | None = None,
    order: str = "recent",
) -> list[ProfileItem]:
    """Filter a listing by folder, preserving Instagram's chronological order.

    ``listing.items`` comes newest first. ``limit`` applies PER FOLDER, not to
    the total: with ``limit=20`` and no kind filter the result holds up to 20
    reels plus up to 20 posts (the 20 newest of each), so a feed dominated by
    reels can no longer starve the posts out of the run. With a single kind
    selected the behaviour is unchanged (20 of that kind).

    ``order="viral"`` sorts by engagement (plays, then likes, then comments)
    BEFORE the per-folder cap, so the limit keeps the most viral of each
    folder instead of the newest. The sort is stable, so ties stay newest
    first. Anything else keeps chronological order.
    """
    wanted = {str(kind).strip().lower() for kind in (kinds or []) if str(kind).strip()}
    items = [item for item in listing.items if not wanted or item.folder in wanted]
    if str(order or "").strip().lower() == "viral":
        items = sorted(items, key=_engagement, reverse=True)
    if limit is not None and limit > 0:
        kept: list[ProfileItem] = []
        per_folder: dict[str, int] = {}
        for item in items:
            seen = per_folder.get(item.folder, 0)
            if seen >= limit:
                continue
            per_folder[item.folder] = seen + 1
            kept.append(item)
        items = kept
    return items


__all__ = [
    "ArchiveSummary",
    "archive_profile",
    "item_filename",
    "item_path",
    "safe_slug",
    "select_items",
]
