"""Semantic search over indexed chunks, shared by /knowledge/search and /me/context."""

from functools import lru_cache

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import Chunk, Document
from app.schemas.knowledge import KnowledgeHit
from app.watcher.embedder import QUERY_PREFIX, EmbeddingError, OllamaEmbedder

# Enough HNSW candidates for the largest allowed limit (50) with room to spare; the default is 40.
HNSW_EF_SEARCH = 100


class SearchUnavailable(RuntimeError):
    """The query could not be embedded (Ollama down, model missing, ...)."""


@lru_cache
def get_embedder() -> OllamaEmbedder:
    settings = get_settings()
    # A request can't wait through the watcher's long retry schedule: fail within seconds instead.
    # keep_alive avoids a ~5s model reload on the first search after Ollama's default 5 idle minutes.
    return OllamaEmbedder(
        settings.ollama_url, settings.embed_model, retries=2, backoff_seconds=0.5, timeout=60, keep_alive="1h"
    )


def embed_query(embedder: OllamaEmbedder, q: str) -> list[float]:
    # nomic-embed-text is asymmetric on purpose: chunks were indexed with DOC_PREFIX
    # ("search_document: "), queries must use QUERY_PREFIX ("search_query: "). Using the same
    # prefix for both is NOT a fix; it makes rankings noticeably worse.
    try:
        return embedder.embed([QUERY_PREFIX + q])[0]
    except EmbeddingError as e:
        raise SearchUnavailable(str(e)) from e


def search_chunks(db: Session, embedder: OllamaEmbedder, q: str, limit: int, source: str | None = None) -> list[KnowledgeHit]:
    """Top `limit` chunks by cosine similarity to `q`, optionally within one documents.source."""
    vector = embed_query(embedder, q)
    distance = Chunk.embedding.cosine_distance(vector)
    stmt = (
        select(
            Document.source,
            Document.path,
            Document.title,
            Chunk.chunk_index,
            Chunk.content,
            Chunk.meta,
            distance.label("distance"),
        )
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.embedding.is_not(None))
    )
    if source:
        stmt = stmt.where(Document.source == source)
    stmt = stmt.order_by(distance, Chunk.id).limit(limit)

    # SET LOCAL lasts until the end of this request's transaction only.
    db.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
    if source:
        # pgvector >= 0.8: keep scanning the index when the source filter discards candidates,
        # instead of returning fewer than `limit` rows.
        db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))

    return [
        KnowledgeHit(
            score=round(1 - row.distance, 4),
            source=row.source,
            path=row.path,
            title=row.title,
            chunk_index=row.chunk_index,
            heading_path=row.meta.get("heading_path", []),
            start_line=row.meta.get("start_line"),
            end_line=row.meta.get("end_line"),
            content=row.content,
        )
        for row in db.execute(stmt)
    ]
