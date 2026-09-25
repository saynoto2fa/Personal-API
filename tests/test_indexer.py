"""Indexer against a real database with a fake embedder.

Each test uses its own temp folder and a unique source name, and deletes only that source's
rows afterwards, so these tests never touch other indexed data.
"""

import shutil
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.models.knowledge import EMBED_DIM, Chunk, Document
from app.watcher.chunker import CHUNKER_VERSION
from app.watcher.indexer import Indexer, Source


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("ollama down")
        self.calls += 1
        return [[float(len(t) % 7)] + [0.0] * (EMBED_DIM - 1) for t in texts]


@pytest.fixture
def source(migrated_db, tmp_path):
    src = Source(f"test:{uuid.uuid4().hex[:8]}", tmp_path.resolve())
    yield src
    with SessionLocal() as s, s.begin():
        s.execute(delete(Document).where(Document.source == src.name))


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def indexer(embedder):
    return Indexer(embedder)


def _docs(source):
    with SessionLocal() as s:
        return {d.path: d for d in s.scalars(select(Document).where(Document.source == source.name))}


def _chunks(source, path):
    with SessionLocal() as s:
        return list(
            s.scalars(
                select(Chunk)
                .join(Document)
                .where(Document.source == source.name, Document.path == path)
                .order_by(Chunk.chunk_index)
            )
        )


def _chunk_total(source):
    with SessionLocal() as s:
        return s.scalar(select(func.count(Chunk.id)).join(Document).where(Document.source == source.name))


def _write(source, rel, text):
    p = source.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_index_then_skip_unchanged(source, indexer, embedder):
    _write(source, "notes/a.md", "---\ntitle: Alpha\n---\n# Intro\n\nHello world.")
    assert indexer.reconcile(source, "notes/a.md") == "indexed"
    doc = _docs(source)["notes/a.md"]
    assert doc.title == "Alpha"
    assert doc.meta["embed_model"] == "fake-embed"
    (chunk,) = _chunks(source, "notes/a.md")
    assert chunk.meta == {"heading_path": ["Intro"], "start_line": 4, "end_line": 6}
    assert len(chunk.embedding) == EMBED_DIM

    calls = embedder.calls
    assert indexer.reconcile(source, "notes/a.md") == "unchanged"
    assert embedder.calls == calls  # no re-embedding


def test_new_chunker_version_reindexes_unchanged_files(source, indexer, embedder):
    _write(source, "a.md", "# A\n\nshort.\n\n## B\n\nalso short.")
    indexer.reconcile(source, "a.md")
    assert _docs(source)["a.md"].meta["chunker"] == CHUNKER_VERSION
    with SessionLocal() as s, s.begin():  # pretend it was indexed by the previous chunker
        doc = s.scalars(select(Document).where(Document.source == source.name)).one()
        doc.meta = {**doc.meta, "chunker": CHUNKER_VERSION - 1}
    calls = embedder.calls
    counts, _ = indexer.sync(source)
    assert counts == {"indexed": 1}
    assert embedder.calls == calls + 1
    assert indexer.sync(source)[0] == {"unchanged": 1}


def test_edit_replaces_chunks_without_orphans(source, indexer):
    body = " ".join(["word"] * 150)
    _write(source, "a.md", "\n\n".join(f"# S{i}\n\n{body}" for i in range(4)))
    indexer.reconcile(source, "a.md")
    doc_id = _docs(source)["a.md"].id
    assert len(_chunks(source, "a.md")) == 4

    _write(source, "a.md", "# Only\n\nShort now.")
    assert indexer.reconcile(source, "a.md") == "indexed"
    assert _docs(source)["a.md"].id == doc_id  # same document row, updated in place
    chunks = _chunks(source, "a.md")
    assert [c.content for c in chunks] == ["# Only\n\nShort now."]
    assert _chunk_total(source) == 1


def test_embed_failure_keeps_previous_version(source, indexer, embedder):
    _write(source, "a.md", "version one")
    indexer.reconcile(source, "a.md")
    _write(source, "a.md", "version two")
    embedder.fail = True
    with pytest.raises(RuntimeError):
        indexer.reconcile(source, "a.md")
    assert [c.content for c in _chunks(source, "a.md")] == ["version one"]


def test_delete_removes_document_and_chunks(source, indexer):
    _write(source, "a.md", "text")
    indexer.reconcile(source, "a.md")
    (source.root / "a.md").unlink()
    assert indexer.reconcile(source, "a.md") == "removed"
    assert _docs(source) == {}
    assert _chunk_total(source) == 0
    assert indexer.reconcile(source, "a.md") == "absent"


def test_rename_repoints_without_reembedding(source, indexer, embedder):
    _write(source, "old.md", "some text")
    indexer.reconcile(source, "old.md")
    doc_id = _docs(source)["old.md"].id
    (source.root / "old.md").rename(source.root / "new.md")
    calls = embedder.calls
    assert indexer.move(source, "old.md", "new.md") == "moved"
    assert embedder.calls == calls
    assert set(_docs(source)) == {"new.md"}
    assert _docs(source)["new.md"].id == doc_id


def test_sync_indexes_skips_excluded_and_removes_missing(source, indexer):
    _write(source, "a.md", "a")
    _write(source, "sub/b.md", "b")
    _write(source, "sub/image.png", "not markdown")
    _write(source, ".obsidian/workspace.md", "excluded")
    _write(source, "proj/node_modules/pkg/README.md", "excluded")
    counts, failed = indexer.sync(source)
    assert failed == []
    assert counts["indexed"] == 2
    assert set(_docs(source)) == {"a.md", "sub/b.md"}

    (source.root / "sub" / "b.md").unlink()
    counts, _ = indexer.sync(source)
    assert counts == {"unchanged": 1, "removed": 1}
    assert set(_docs(source)) == {"a.md"}


def test_sync_subdir_only_touches_that_folder(source, indexer):
    _write(source, "a.md", "a")
    _write(source, "sub/b.md", "b")
    _write(source, "sub_other/c.md", "c")
    indexer.sync(source)
    shutil.rmtree(source.root / "sub")
    counts, _ = indexer.sync(source, "sub")
    assert counts == {"removed": 1}
    assert set(_docs(source)) == {"a.md", "sub_other/c.md"}  # prefix 'sub' must not match 'sub_other'


def test_source_path_filtering(tmp_path):
    src = Source("x", tmp_path)
    assert src.rel_file(tmp_path / "a.md") == "a.md"
    assert src.rel_file(tmp_path / "Dir" / "B.MD") == "Dir/B.MD"
    assert src.rel_file(tmp_path / "a.txt") is None
    assert src.rel_file(tmp_path / ".git" / "x.md") is None
    assert src.rel_file(tmp_path.parent / "outside.md") is None
    assert src.rel_dir(tmp_path) == ""
    assert src.rel_dir(tmp_path / "node_modules") is None

    archived = Source("x", tmp_path, archive="OUTDATED")
    assert archived.rel_file(tmp_path / "OUTDATED" / "old.md") is None
    assert archived.rel_file(tmp_path / "outdated" / "sub" / "old.md") is None  # case-insensitive
    assert archived.rel_file(tmp_path / "notes" / "OUTDATED" / "x.md") == "notes/OUTDATED/x.md"  # top level only
    assert archived.rel_file(tmp_path / "OUTDATED-ideas.md") == "OUTDATED-ideas.md"


def test_sync_skips_archive_folder_and_drops_its_old_documents(source, indexer):
    _write(source, "keep.md", "k")
    _write(source, "OUTDATED/old.md", "o")
    indexer.sync(source)
    assert set(_docs(source)) == {"keep.md", "OUTDATED/old.md"}  # indexed before an archive was configured

    archived = Source(source.name, source.root, archive="OUTDATED")
    counts, _ = indexer.sync(archived)
    assert counts == {"unchanged": 1, "removed": 1}
    assert set(_docs(source)) == {"keep.md"}
