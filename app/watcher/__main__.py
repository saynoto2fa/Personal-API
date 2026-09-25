"""Index configured folders into documents/chunks and keep them in sync.

    python -m app.watcher                  # initial sync, then watch for changes (Ctrl+C to stop)
    python -m app.watcher --once           # initial sync (and archive sweep) only, then exit
    python -m app.watcher --status         # show what is indexed
    python -m app.watcher --archive-status # show what is in the archive folder and when it is due
"""

import argparse
import logging
import logging.handlers
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models.knowledge import Chunk, Document
from app.watcher.archive import ArchiveKeeper
from app.watcher.embedder import OllamaEmbedder
from app.watcher.indexer import Indexer, Source
from app.watcher.watch import RETRY_SECONDS, WorkQueue, file_job, run_forever

log = logging.getLogger("app.watcher")

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = REPO_ROOT / "logs" / "watcher.lock"
ARCHIVE_SWEEP_SECONDS = 3600.0


def _status() -> int:
    with SessionLocal() as s:
        rows = s.execute(
            select(Document.source, func.count(func.distinct(Document.id)), func.count(Chunk.id), func.max(Document.indexed_at))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.source)
            .order_by(Document.source)
        ).all()
    if not rows:
        print("Nothing indexed yet.")
    for source, docs, chunks, last in rows:
        print(f"{source}: {docs} files, {chunks} chunks, last indexed {last:%Y-%m-%d %H:%M:%S %Z}")
    return 0


def _archive_status(keeper: ArchiveKeeper | None, sources: list[Source]) -> int:
    if keeper is None:
        print("Archive retention is off (ARCHIVE_FOLDER empty or ARCHIVE_RETENTION_DAYS=0).")
        return 0
    now = datetime.now(UTC)
    for source in sources:
        items = keeper.register(source)
        print(f"{source.name}/{keeper.folder_name}: {len(items)} item(s), cleared after {keeper.retention.days} days")
        for item in items:
            days_left = max(0, (item.due_at - now).days)
            kind = "folder" if item.is_dir else "file"
            print(f"  {item.due_at.astimezone():%Y-%m-%d %H:%M}  ({days_left:>3} days left)  {kind:6}  {item.name}")
    return 0


def _sources(settings: Settings, only: list[str] | None) -> list[Source]:
    configured = settings.watch_sources
    if not configured:
        raise SystemExit("No folders configured. Set WATCH_SOURCES in .env, e.g. WATCH_SOURCES='{\"vault\": \"C:/path/to/vault\"}'")
    if unknown := set(only or []) - set(configured):
        raise SystemExit(f"Unknown source(s): {', '.join(sorted(unknown))}. Configured: {', '.join(configured)}")
    sources = [
        Source(name, Path(path).expanduser().resolve(), archive=settings.archive_folder or None)
        for name, path in configured.items()
        if not only or name in only
    ]
    for source in sources:
        if not source.root.is_dir():
            raise SystemExit(f"Source '{source.name}' folder does not exist: {source.root}")
    return sources


def _single_instance() -> IO | None:
    """Hold a lock for the life of the process so two watchers never run at once."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_FILE, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.watcher", description=__doc__.split("\n")[0])
    parser.add_argument("--once", action="store_true", help="run the initial sync and archive sweep, then exit")
    parser.add_argument("--status", action="store_true", help="show indexed file and chunk counts, then exit")
    parser.add_argument("--archive-status", action="store_true", help="list the archive folder with due dates, then exit")
    parser.add_argument("--source", action="append", metavar="NAME", help="only this source (repeatable)")
    parser.add_argument("--debounce", type=float, default=2.0, metavar="SECONDS", help="wait this long after the last change (default 2)")
    parser.add_argument("--log-file", type=Path, metavar="PATH", help="log to this file (rotated at 1 MB) instead of the console")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    handler: logging.Handler = (
        logging.handlers.RotatingFileHandler(args.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        if args.log_file
        else logging.StreamHandler()
    )
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S" if args.log_file else "%H:%M:%S",
        handlers=[handler],
    )
    for noisy in ("httpx", "httpcore", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.status:
        return _status()

    settings = get_settings()
    try:
        sources = _sources(settings, args.source)
    except SystemExit as e:
        log.error("%s", e)
        return 2
    keeper = (
        ArchiveKeeper(settings.archive_folder, settings.archive_retention_days)
        if settings.archive_folder and settings.archive_retention_days > 0
        else None
    )
    if args.archive_status:
        return _archive_status(keeper, sources)

    lock = _single_instance()
    if lock is None:
        log.info("Another watcher is already running (lock: %s). Exiting.", LOCK_FILE)
        return 0

    embedder = OllamaEmbedder(settings.ollama_url, settings.embed_model)
    if problem := embedder.check():
        log.warning("%s Changes will be retried until it is available.", problem)
    indexer = Indexer(embedder)
    queue = WorkQueue(args.debounce)
    all_failed: list[tuple[Source, str]] = []

    def initial_sync() -> None:
        for source in sources:
            log.info("Syncing %s (%s) ...", source.name, source.root)
            counts, failed = indexer.sync(source)
            retry_note = f", {len(failed)} failed (retrying every {RETRY_SECONDS:.0f}s)" if failed else ""
            log.info("Synced %s: %s%s", source.name, dict(counts) or "nothing to index", retry_note)
            for rel in failed:
                all_failed.append((source, rel))
                queue.add((source.name, "file", rel), file_job(indexer, source, rel), delay=RETRY_SECONDS)

    def sweep_archives() -> None:
        if keeper is None:
            return
        try:
            for source in sources:
                keeper.sweep(source)
        finally:
            queue.add(("archive-sweep",), sweep_archives, delay=ARCHIVE_SWEEP_SECONDS)

    if args.once:
        initial_sync()
        if keeper:
            for source in sources:
                keeper.sweep(source)
        if all_failed:
            log.error("%d file(s) failed; run again once the problem is fixed", len(all_failed))
            return 1
        return 0

    stop = threading.Event()

    def started() -> None:
        initial_sync()
        queue.add(("archive-sweep",), sweep_archives, delay=0)
        log.info("Watching %d source(s). Press Ctrl+C to stop.", len(sources))

    try:
        run_forever(indexer, sources, queue, stop, on_started=started)
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
