"""Archive retention with a fake clock and a fake Recycle Bin (nothing is really deleted)."""

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.models.archive import ArchiveEntry
from app.watcher.archive import ArchiveKeeper
from app.watcher.indexer import Source

START = datetime(2026, 10, 1, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def source(migrated_db, tmp_path):
    src = Source(f"test:{uuid.uuid4().hex[:8]}", tmp_path.resolve(), archive="OUTDATED")
    (src.root / "OUTDATED").mkdir()
    yield src
    with SessionLocal() as s, s.begin():
        s.execute(delete(ArchiveEntry).where(ArchiveEntry.source == src.name))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def trashed():
    return []


def make_keeper(clock: Clock, trashed: list[str]) -> ArchiveKeeper:
    """30-day keeper whose 'Recycle Bin' records the path and removes it from the temp folder."""

    def fake_trash(path: str) -> None:
        trashed.append(path)
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

    return ArchiveKeeper("OUTDATED", 30, trash=fake_trash, now=clock)


@pytest.fixture
def keeper(clock, trashed):
    return make_keeper(clock, trashed)


def test_clock_starts_when_first_seen_not_file_date(source, keeper, clock, trashed):
    old = source.root / "OUTDATED" / "old-note.md"
    old.write_text("x", encoding="utf-8")
    os.utime(old, (0, 0))  # file dates from 1970: must not make it due immediately
    (item,) = keeper.register(source)
    assert item.first_seen_at == START
    assert item.due_at == START + timedelta(days=30)
    assert keeper.sweep(source) == []
    assert old.exists()


def test_items_go_to_recycle_bin_after_retention(source, keeper, clock, trashed):
    (source.root / "OUTDATED" / "a.md").write_text("a", encoding="utf-8")
    (source.root / "OUTDATED" / "Old project").mkdir()
    (source.root / "OUTDATED" / "Old project" / "b.md").write_text("b", encoding="utf-8")
    keeper.register(source)

    clock.now = START + timedelta(days=10)
    (source.root / "OUTDATED" / "late.md").write_text("c", encoding="utf-8")
    keeper.register(source)

    clock.now = START + timedelta(days=29, hours=23)
    assert keeper.sweep(source) == []

    clock.now = START + timedelta(days=30)
    assert sorted(keeper.sweep(source)) == ["Old project", "a.md"]
    assert [p.split("\\")[-1].split("/")[-1] for p in sorted(trashed)] == ["Old project", "a.md"]
    assert [i.name for i in keeper.register(source)] == ["late.md"]  # its own clock is still running

    clock.now = START + timedelta(days=40)
    assert keeper.sweep(source) == ["late.md"]
    assert keeper.register(source) == []


def test_moving_out_forgets_and_moving_back_restarts_clock(source, keeper, clock):
    note = source.root / "OUTDATED" / "maybe.md"
    note.write_text("x", encoding="utf-8")
    keeper.register(source)

    clock.now = START + timedelta(days=20)
    note.rename(source.root / "maybe.md")  # recovered
    assert keeper.register(source) == []

    clock.now = START + timedelta(days=25)
    (source.root / "maybe.md").rename(note)  # archived again
    (item,) = keeper.register(source)
    assert item.first_seen_at == START + timedelta(days=25)
    clock.now = START + timedelta(days=31)
    assert keeper.sweep(source) == []


def test_trash_failure_keeps_entry_for_next_sweep(source, clock):
    (source.root / "OUTDATED" / "locked.md").write_text("x", encoding="utf-8")

    def failing_trash(path: str) -> None:
        raise OSError("file is open in another program")

    keeper = ArchiveKeeper("OUTDATED", 30, trash=failing_trash, now=clock)
    keeper.register(source)
    clock.now = START + timedelta(days=31)
    assert keeper.sweep(source) == []
    assert [i.name for i in keeper.register(source)] == ["locked.md"]


def test_os_litter_and_missing_folder_are_ignored(source, keeper, tmp_path):
    (source.root / "OUTDATED" / "desktop.ini").write_text("x", encoding="utf-8")
    assert keeper.register(source) == []
    no_archive = Source(source.name, tmp_path / "elsewhere", archive="OUTDATED")
    assert keeper.register(no_archive) == []
