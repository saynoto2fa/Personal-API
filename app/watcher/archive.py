"""Retention for each source's archive folder (OUTDATED by default).

Windows keeps a file's dates when it is moved, so a file's mtime says nothing about when it was
archived. Instead every item directly inside the archive folder (a file or a whole folder) is
recorded the first time it is seen, and sent to the Recycle Bin once it has been there for the
retention period. Items moved back out, or deleted by hand, are forgotten; an item moved back in
starts a new clock. If the watcher was off when something was archived, its clock starts when
the watcher next runs, so mistakes only ever make the hold longer.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from send2trash import send2trash
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models.archive import ArchiveEntry
from app.watcher.indexer import Source

log = logging.getLogger(__name__)

IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}  # OS litter, not archived notes


@dataclass
class ArchivedItem:
    name: str
    is_dir: bool
    first_seen_at: datetime
    due_at: datetime


class ArchiveKeeper:
    def __init__(
        self,
        folder_name: str,
        retention_days: int,
        session_factory: sessionmaker[Session] = SessionLocal,
        trash: Callable[[str], None] = send2trash,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.folder_name = folder_name
        self.retention = timedelta(days=retention_days)
        self._session = session_factory
        self._trash = trash
        self._now = now

    def _present(self, source: Source) -> dict[str, Path]:
        folder = source.root / self.folder_name
        if not folder.is_dir():
            return {}
        return {p.name: p for p in folder.iterdir() if p.name.casefold() not in IGNORED_NAMES}

    def register(self, source: Source) -> list[ArchivedItem]:
        """Start the clock for new items, forget items that left. Returns what is archived now."""
        present = self._present(source)
        with self._session() as s, s.begin():
            known = set(s.scalars(select(ArchiveEntry.name).where(ArchiveEntry.source == source.name)))
            if new := [n for n in present if n not in known]:
                s.execute(
                    pg_insert(ArchiveEntry)
                    .values([{"source": source.name, "name": n, "is_dir": present[n].is_dir(), "first_seen_at": self._now()} for n in new])
                    .on_conflict_do_nothing(index_elements=["source", "name"])
                )
                for n in new:
                    log.info("archived: %s/%s/%s (goes to the Recycle Bin in %d days)", source.name, self.folder_name, n, self.retention.days)
            if gone := [n for n in known if n not in present]:
                s.execute(delete(ArchiveEntry).where(ArchiveEntry.source == source.name, ArchiveEntry.name.in_(gone)))
            rows = s.execute(
                select(ArchiveEntry.name, ArchiveEntry.is_dir, ArchiveEntry.first_seen_at)
                .where(ArchiveEntry.source == source.name)
                .order_by(ArchiveEntry.first_seen_at, ArchiveEntry.name)
            ).all()
        return [ArchivedItem(n, d, seen, seen + self.retention) for n, d, seen in rows]

    def sweep(self, source: Source) -> list[str]:
        """Register changes, then move every item whose time is up to the Recycle Bin."""
        folder = (source.root / self.folder_name).resolve()
        trashed: list[str] = []
        for item in self.register(source):
            if item.due_at > self._now():
                continue
            path = folder / item.name
            if path.resolve().parent != folder:  # never touch anything outside the archive folder
                log.error("refusing to trash %s: not directly inside %s", path, folder)
                continue
            try:
                self._trash(str(path))
            except Exception as e:  # locked by another program, already gone, ...
                log.error("could not move %s/%s/%s to the Recycle Bin: %s", source.name, self.folder_name, item.name, e)
                continue
            with self._session() as s, s.begin():
                s.execute(delete(ArchiveEntry).where(ArchiveEntry.source == source.name, ArchiveEntry.name == item.name))
            log.info("moved to Recycle Bin after %d days: %s/%s/%s", self.retention.days, source.name, self.folder_name, item.name)
            trashed.append(item.name)
        return trashed
