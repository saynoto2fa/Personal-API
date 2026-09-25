"""Turn filesystem events into debounced, retried indexing jobs."""

import logging
import threading
import time
from collections.abc import Callable, Hashable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.watcher.indexer import Indexer, Source

log = logging.getLogger(__name__)

RETRY_SECONDS = 60.0


class WorkQueue:
    """Jobs keyed by what they touch. Re-adding a key pushes its deadline back (debounce)."""

    def __init__(self, debounce_seconds: float) -> None:
        self.debounce_seconds = debounce_seconds
        self._lock = threading.Lock()
        self._jobs: dict[Hashable, tuple[float, Callable[[], object]]] = {}

    def add(self, key: Hashable, job: Callable[[], object], delay: float | None = None) -> None:
        due = time.monotonic() + (self.debounce_seconds if delay is None else delay)
        with self._lock:
            self._jobs[key] = (due, job)

    def pop_ready(self) -> list[tuple[Hashable, Callable[[], object]]]:
        now = time.monotonic()
        with self._lock:
            ready = sorted((k for k, (due, _) in self._jobs.items() if due <= now), key=lambda k: self._jobs[k][0])
            return [(k, self._jobs.pop(k)[1]) for k in ready]

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)


def file_job(indexer: Indexer, source: Source, rel: str) -> Callable[[], None]:
    def run() -> None:
        result = indexer.reconcile(source, rel)
        if result != "unchanged":
            log.info("%s: %s/%s", result, source.name, rel)

    return run


def dir_job(indexer: Indexer, source: Source, subdir: str, queue: WorkQueue) -> Callable[[], None]:
    def run() -> None:
        counts, failed = indexer.sync(source, subdir)
        log.info("synced folder %s/%s: %s", source.name, subdir or ".", dict(counts))
        for rel in failed:
            queue.add((source.name, "file", rel), file_job(indexer, source, rel), delay=RETRY_SECONDS)

    return run


class _Handler(FileSystemEventHandler):
    def __init__(self, indexer: Indexer, source: Source, queue: WorkQueue) -> None:
        self.indexer, self.source, self.queue = indexer, source, queue

    def _file(self, rel: str | None) -> None:
        if rel:
            self.queue.add((self.source.name, "file", rel), file_job(self.indexer, self.source, rel))

    def _dir(self, rel: str | None) -> None:
        if rel is not None:
            self.queue.add((self.source.name, "dir", rel), dir_job(self.indexer, self.source, rel, self.queue))

    def on_any_event(self, event: FileSystemEvent) -> None:
        kind = event.event_type
        if kind not in ("created", "modified", "deleted", "moved"):
            return  # opened/closed carry no new information
        src = str(event.src_path)
        dest = str(event.dest_path) if kind == "moved" else ""
        if event.is_directory:
            if kind == "modified":
                return  # a child changed; that child gets its own event
            self._dir(self.source.rel_dir(src))
            if dest:
                self._dir(self.source.rel_dir(dest))
            return
        if kind == "moved":
            old, new = self.source.rel_file(src), self.source.rel_file(dest)
            if old or new:
                job = lambda: log.info("%s: %s/%s -> %s", self.indexer.move(self.source, old, new), self.source.name, old, new)  # noqa: E731
                self.queue.add((self.source.name, "move", old, new), job)
            return
        self._file(self.source.rel_file(src))


def run_forever(
    indexer: Indexer, sources: list[Source], queue: WorkQueue, stop: threading.Event, on_started: Callable[[], None]
) -> None:
    """Watch every source and process queued jobs on this thread until `stop` is set."""
    observer = Observer()
    for source in sources:
        observer.schedule(_Handler(indexer, source, queue), str(source.root), recursive=True)
    observer.start()
    try:
        on_started()  # initial sync runs after the observer starts, so edits made meanwhile aren't missed
        while not stop.is_set():
            for key, job in queue.pop_ready():
                try:
                    job()
                except Exception as e:
                    log.error("Job %s failed, retrying in %.0fs: %s", key, RETRY_SECONDS, e)
                    queue.add(key, job, delay=RETRY_SECONDS)
            stop.wait(0.5)
    finally:
        observer.stop()
        observer.join()
