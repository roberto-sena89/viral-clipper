"""Batch processing: a durable manifest over many URLs.

One URL per process is fine for a demo and wrong for a production line. The
real unit of work is a *batch*: a list of episodes, a client's back catalogue, a
week of streams. Three things break as soon as that is true, and this module
exists to fix exactly those:

* **A failure must not stop the batch.** One dead video, one age-gated URL, one
  network hiccup should cost one job, not the other forty.
* **A batch must be resumable.** Encoding an hour of video takes long enough
  that the process will be interrupted. Re-running must skip what is already
  done instead of redoing it.
* **State must survive the process.** An in-memory list of what happened dies
  with the terminal. SQLite is a file, needs no server, and is queryable.

The manifest is a single table, deliberately: status, attempts, clip count and
the last error, keyed by URL. Anything richer belongs in the run reports.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .util import ClipperError, Logger

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    url        TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    clips      INTEGER NOT NULL DEFAULT 0,
    output_dir TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status);
"""

# Matches the video id in the two URL shapes people actually paste.
_YOUTUBE_ID = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/))([A-Za-z0-9_-]{6,})"
)


@dataclass(frozen=True)
class Job:
    """One row of the manifest."""

    url: str
    status: str
    attempts: int
    clips: int
    output_dir: str
    error: str

    @property
    def is_done(self) -> bool:
        return self.status == STATUS_DONE


@dataclass
class BatchSummary:
    """What happened during one batch run."""

    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.succeeded + self.failed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_urls(path: str | Path) -> list[str]:
    """Read a batch file: one URL per line, blank lines and ``#`` ignored.

    Duplicates are dropped while keeping the first occurrence, because a batch
    file assembled by hand or by a script will contain them and processing the
    same URL twice is pure waste.
    """
    source = Path(path)
    if not source.exists():
        raise ClipperError(f"Batch file not found: {source}")

    urls: list[str] = []
    seen: set[str] = set()
    for raw in source.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        urls.append(line)
    if not urls:
        raise ClipperError(f"Batch file {source} contains no URLs.")
    return urls


def url_slug(url: str) -> str:
    """Short, stable, readable folder name for one URL.

    Uses the YouTube video id when the URL carries one, so a batch produces
    ``output/dQw4w9WgXcQ/`` instead of an opaque hash. Falls back to a digest of
    the whole URL for anything else.
    """
    match = _YOUTUBE_ID.search(url)
    if match:
        return match.group(1)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"url_{digest}"


def open_manifest(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite manifest."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def seed(connection: sqlite3.Connection, urls: Iterable[str]) -> int:
    """Insert new URLs as pending; existing rows are left untouched.

    Returns how many were added. Leaving existing rows alone is what makes
    re-running a batch cheap: finished work stays finished.
    """
    added = 0
    for url in urls:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO jobs (url, status, updated_at) VALUES (?, ?, ?)",
            (url, STATUS_PENDING, _now()),
        )
        added += cursor.rowcount
    connection.commit()
    return added


def reset_failed(connection: sqlite3.Connection) -> int:
    """Move failed jobs back to pending so a retry run picks them up."""
    cursor = connection.execute(
        "UPDATE jobs SET status = ?, error = '', updated_at = ? WHERE status = ?",
        (STATUS_PENDING, _now(), STATUS_FAILED),
    )
    connection.commit()
    return cursor.rowcount


def reset_stale_running(connection: sqlite3.Connection) -> int:
    """Move jobs stuck in ``running`` back to pending.

    A process killed mid-encode leaves rows in ``running`` forever. Without
    this, those URLs would be skipped by every future run and silently lost.
    """
    cursor = connection.execute(
        "UPDATE jobs SET status = ?, updated_at = ? WHERE status = ?",
        (STATUS_PENDING, _now(), STATUS_RUNNING),
    )
    connection.commit()
    return cursor.rowcount


def pending_jobs(connection: sqlite3.Connection) -> list[Job]:
    """Every job still waiting to run, in insertion order."""
    rows = connection.execute(
        "SELECT url, status, attempts, clips, output_dir, error FROM jobs "
        "WHERE status = ? ORDER BY rowid",
        (STATUS_PENDING,),
    ).fetchall()
    return [_job(row) for row in rows]


def all_jobs(connection: sqlite3.Connection) -> list[Job]:
    rows = connection.execute(
        "SELECT url, status, attempts, clips, output_dir, error FROM jobs ORDER BY rowid"
    ).fetchall()
    return [_job(row) for row in rows]


def _job(row: sqlite3.Row) -> Job:
    return Job(
        url=row["url"],
        status=row["status"],
        attempts=int(row["attempts"]),
        clips=int(row["clips"]),
        output_dir=row["output_dir"],
        error=row["error"],
    )


def mark_running(connection: sqlite3.Connection, url: str) -> None:
    connection.execute(
        "UPDATE jobs SET status = ?, attempts = attempts + 1, updated_at = ? WHERE url = ?",
        (STATUS_RUNNING, _now(), url),
    )
    connection.commit()


def mark_done(
    connection: sqlite3.Connection, url: str, *, clips: int, output_dir: str = ""
) -> None:
    connection.execute(
        "UPDATE jobs SET status = ?, clips = ?, output_dir = ?, error = '', updated_at = ? "
        "WHERE url = ?",
        (STATUS_DONE, clips, output_dir, _now(), url),
    )
    connection.commit()


def mark_failed(connection: sqlite3.Connection, url: str, error: str) -> None:
    connection.execute(
        "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE url = ?",
        (STATUS_FAILED, error[:500], _now(), url),
    )
    connection.commit()


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Job count per status, including statuses that currently have none."""
    tally = {
        STATUS_PENDING: 0,
        STATUS_RUNNING: 0,
        STATUS_DONE: 0,
        STATUS_FAILED: 0,
    }
    for row in connection.execute("SELECT status, COUNT(*) AS total FROM jobs GROUP BY status"):
        tally[str(row["status"])] = int(row["total"])
    return tally


def run_batch(
    connection: sqlite3.Connection,
    runner: Callable[[str], tuple[int, str, int, str]],
    logger: Logger,
    *,
    retry_failed: bool = False,
) -> BatchSummary:
    """Process every pending job, isolating failures.

    ``runner`` receives a URL and returns ``(exit_code, error, clip_count,
    output_dir)``. It is injected rather than imported so the loop can be tested
    without ffmpeg, yt-dlp or a network.
    """
    summary = BatchSummary()

    if retry_failed:
        retried = reset_failed(connection)
        if retried and logger:
            logger.info(f"{retried} job(s) com falha voltaram para a fila")
    stale = reset_stale_running(connection)
    if stale and logger:
        logger.warn(f"{stale} job(s) ficaram presos em execucao e voltaram para a fila")

    queue = pending_jobs(connection)
    if not queue:
        if logger:
            logger.info("Nada pendente no manifesto.")
        return summary

    if logger:
        logger.step(f"Lote com {len(queue)} URL(s) pendente(s)")

    for position, job in enumerate(queue, start=1):
        mark_running(connection, job.url)
        if logger:
            logger.step(f"[{position}/{len(queue)}] {job.url}")
        try:
            code, error, clips, output_dir = runner(job.url)
        except KeyboardInterrupt:
            # Leave the row in ``running``; the next run resets it.
            if logger:
                logger.warn("Interrompido; o manifesto permite retomar depois.")
            raise
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the batch
            code, error, clips, output_dir = 1, f"{type(exc).__name__}: {exc}", 0, ""

        summary.processed += 1
        if code == 0:
            mark_done(connection, job.url, clips=clips, output_dir=output_dir)
            summary.succeeded += 1
            if logger:
                logger.ok(f"{clips} clip(s) em {output_dir or '(pasta padrao)'}")
        else:
            mark_failed(connection, job.url, error or f"exit code {code}")
            summary.failed += 1
            if logger:
                logger.warn(f"Falhou: {error or code}")

    return summary


def format_summary(connection: sqlite3.Connection, summary: BatchSummary) -> str:
    """Human readable end-of-batch report, including what is left to do."""
    tally = counts(connection)
    lines = [
        "Resumo do lote",
        f"  processados : {summary.processed}",
        f"  concluidos  : {summary.succeeded}",
        f"  falhas      : {summary.failed}",
        f"  pendentes   : {tally[STATUS_PENDING]}",
    ]
    if tally[STATUS_FAILED]:
        lines.append(f"  com falha   : {tally[STATUS_FAILED]} (use --retry-failed)")
    return "\n".join(lines)


def write_json(connection: sqlite3.Connection, destination: str | Path) -> Path:
    """Dump the manifest so a batch can be inspected or diffed outside SQLite."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": _now(),
        "counts": counts(connection),
        "jobs": [job.__dict__ for job in all_jobs(connection)],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


__all__ = [
    "BatchSummary",
    "Job",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "all_jobs",
    "counts",
    "format_summary",
    "load_urls",
    "mark_done",
    "mark_failed",
    "mark_running",
    "open_manifest",
    "pending_jobs",
    "reset_failed",
    "reset_stale_running",
    "run_batch",
    "seed",
    "url_slug",
    "write_json",
]
