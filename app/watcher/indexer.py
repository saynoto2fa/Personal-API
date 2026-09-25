"""Keep the documents/chunks tables in sync with files on disk, one file at a time."""

import hashlib
import logging
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Protocol

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models.knowledge import Chunk, Document
from app.watcher.chunker import Chunk as TextChunk
from app.watcher.chunker import chunk_markdown
from app.watcher.embedder import DOC_PREFIX

log = logging.getLogger(__name__)

_LOOKUP = object()  # sentinel: "look up the stored version yourself"

EXTENSIONS = {".md"}
EXCLUDED_DIRS = {".obsidian", ".git", ".trash", ".venv", "venv", "node_modules", "__pycache__"}


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class Source:
    """A named folder to index. `name` is stored in documents.source ('vault', 'project:<name>', ...)."""

    name: str
    root: Path

    def rel_dir(self, path: str | os.PathLike) -> str | None:
        """Folder path relative to the root ('' for the root), or None if outside it or excluded."""
        try:
            rel = PurePath(path).relative_to(self.root)
        except ValueError:
            return None
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            return None
        return "" if rel == PurePath(".") else rel.as_posix()

    def rel_file(self, path: str | os.PathLike) -> str | None:
        """File path relative to the root if it is a file type we index, else None."""
        p = PurePath(path)
        if p.suffix.lower() not in EXTENSIONS:
            return None
        parent = self.rel_dir(p.parent)
        if parent is None:
            return None
        return f"{parent}/{p.name}" if parent else p.name


def embed_input(title: str, chunk: TextChunk) -> str:
    """What actually gets embedded: note title and heading path give short chunks their context."""
    context = " > ".join([title, *chunk.heading_path])
    return f"{DOC_PREFIX}{context}\n\n{chunk.content}"


class Indexer:
    def __init__(self, embedder: Embedder, session_factory: sessionmaker[Session] = SessionLocal) -> None:
        self.embedder = embedder
        self._session = session_factory

    def _stored_versions(self, source: Source, subdir: str | None = None, rel: str | None = None) -> dict[str, tuple]:
        """{path: (content_hash, embed_model)} for one file, one folder, or the whole source."""
        stmt = select(Document.path, Document.content_hash, Document.meta["embed_model"].astext).where(
            Document.source == source.name
        )
        if rel is not None:
            stmt = stmt.where(Document.path == rel)
        elif subdir:
            stmt = stmt.where(Document.path.startswith(f"{subdir}/", autoescape=True))
        with self._session() as s:
            return {path: (digest, model) for path, digest, model in s.execute(stmt)}

    def reconcile(self, source: Source, rel: str, stored: tuple | None | object = _LOOKUP) -> str:
        """Make the database match the file at `rel`: index it, skip it if unchanged, or remove it.

        `stored` is the (content_hash, embed_model) already known for this file (None if not indexed);
        by default it is looked up. Returns one of 'indexed', 'unchanged', 'removed', 'absent'.
        """
        path = source.root / rel
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            data = path.read_bytes()
        except FileNotFoundError:
            return "removed" if self.remove(source, rel) else "absent"

        digest = hashlib.sha256(data).hexdigest()
        if stored is _LOOKUP:
            stored = self._stored_versions(source, rel=rel).get(rel)
        if stored == (digest, self.embedder.model):
            return "unchanged"

        parsed = chunk_markdown(data.decode("utf-8-sig", errors="replace"))
        title = parsed.title or PurePath(rel).stem
        # Embed before touching the database, so a failure leaves the previous version intact.
        vectors = self.embedder.embed([embed_input(title, c) for c in parsed.chunks]) if parsed.chunks else []

        with self._session() as s, s.begin():
            doc_meta = {"embed_model": self.embedder.model, "chunk_count": len(parsed.chunks)}
            stmt = pg_insert(Document).values(
                source=source.name, path=rel, title=title, content_hash=digest, mtime=mtime, metadata=doc_meta
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "path"],
                set_={
                    "title": stmt.excluded.title,
                    "content_hash": stmt.excluded.content_hash,
                    "mtime": stmt.excluded.mtime,
                    "metadata": stmt.excluded.metadata,
                    "indexed_at": func.now(),
                },
            )
            doc_id = s.execute(stmt.returning(Document.id)).scalar_one()
            s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
            if parsed.chunks:
                s.execute(
                    insert(Chunk),
                    [
                        {
                            "document_id": doc_id,
                            "chunk_index": c.index,
                            "content": c.content,
                            "token_count": c.token_estimate,
                            "embedding": vec,
                            "meta": {
                                "heading_path": c.heading_path,
                                "start_line": c.start_line,
                                "end_line": c.end_line,
                            },
                        }
                        for c, vec in zip(parsed.chunks, vectors, strict=True)
                    ],
                )
        return "indexed"

    def remove(self, source: Source, rel: str) -> bool:
        """Delete a file's document; its chunks go with it (ON DELETE CASCADE)."""
        with self._session() as s, s.begin():
            result = s.execute(delete(Document).where(Document.source == source.name, Document.path == rel))
        return result.rowcount > 0

    def move(self, source: Source, old_rel: str | None, new_rel: str | None) -> str:
        """Handle a rename. A plain rename just repoints the document instead of re-embedding it."""
        if old_rel and new_rel:
            with self._session() as s, s.begin():
                taken = s.scalar(select(Document.id).where(Document.source == source.name, Document.path == new_rel))
                moved = not taken and s.execute(
                    update(Document)
                    .where(Document.source == source.name, Document.path == old_rel)
                    .values(path=new_rel)
                ).rowcount
            if moved:
                return "moved" if self.reconcile(source, new_rel) == "unchanged" else "moved+indexed"
        results = [self.reconcile(source, r) for r in (old_rel, new_rel) if r]
        return "+".join(results) or "ignored"

    def sync(self, source: Source, subdir: str = "") -> tuple[Counter, list[str]]:
        """Reconcile every file under `subdir` (the whole source by default) with the database.

        Unchanged files are skipped by content hash, so this is cheap after the first run.
        Returns (counts per outcome, files that failed and should be retried).
        """
        base = source.root / subdir if subdir else source.root
        on_disk: list[str] = []
        if base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
                for name in sorted(filenames):
                    if rel := source.rel_file(Path(dirpath) / name):
                        on_disk.append(rel)

        in_db = self._stored_versions(source, subdir=subdir)  # one query instead of one per file

        counts: Counter = Counter()
        failed: list[str] = []
        for rel in on_disk:
            try:
                counts[self.reconcile(source, rel, stored=in_db.get(rel))] += 1
            except Exception as e:  # keep going; the caller retries failures later
                log.error("Failed to index %s/%s: %s", source.name, rel, e)
                failed.append(rel)
        for rel in sorted(in_db.keys() - set(on_disk)):
            try:
                counts["removed" if self.remove(source, rel) else "absent"] += 1
            except Exception as e:
                log.error("Failed to remove %s/%s: %s", source.name, rel, e)
                failed.append(rel)
        return counts, failed
