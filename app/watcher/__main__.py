"""Index configured folders into documents/chunks and keep them in sync.

    python -m app.watcher            # initial sync, then watch for changes (Ctrl+C to stop)
    python -m app.watcher --once     # initial sync only, then exit
    python -m app.watcher --status   # show what is indexed
"""

import argparse
import logging
import sys
import threading
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models.knowledge import Chunk, Document
from app.watcher.embedder import OllamaEmbedder
from app.watcher.indexer import Indexer, Source
from app.watcher.watch import RETRY_SECONDS, WorkQueue, file_job, run_forever

log = logging.getLogger("app.watcher")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.watcher", description=__doc__.split("\n")[0])
    parser.add_argument("--once", action="store_true", help="run the initial sync and exit")
    parser.add_argument("--status", action="store_true", help="show indexed file and chunk counts, then exit")
    parser.add_argument("--source", action="append", metavar="NAME", help="only this source (repeatable)")
    parser.add_argument("--debounce", type=float, default=2.0, metavar="SECONDS", help="wait this long after the last change (default 2)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.status:
        return _status()

    settings = get_settings()
    configured = settings.watch_sources
    if not configured:
        log.error("No folders configured. Set WATCH_SOURCES in .env, e.g. WATCH_SOURCES='{\"vault\": \"C:/path/to/vault\"}'")
        return 2
    unknown = set(args.source or []) - set(configured)
    if unknown:
        log.error("Unknown source(s): %s. Configured: %s", ", ".join(sorted(unknown)), ", ".join(configured))
        return 2
    sources = [
        Source(name, Path(path).expanduser().resolve())
        for name, path in configured.items()
        if not args.source or name in args.source
    ]
    for source in sources:
        if not source.root.is_dir():
            log.error("Source '%s' folder does not exist: %s", source.name, source.root)
            return 2

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

    if args.once:
        initial_sync()
        if all_failed:
            log.error("%d file(s) failed; run again once the problem is fixed", len(all_failed))
            return 1
        return 0

    stop = threading.Event()

    def started() -> None:
        initial_sync()
        log.info("Watching %d source(s). Press Ctrl+C to stop.", len(sources))

    try:
        run_forever(indexer, sources, queue, stop, on_started=started)
    except KeyboardInterrupt:
        log.info("Stopping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
