"""Search over indexed chunks, shared by /knowledge/search and /me/context.

Three modes:
- semantic: cosine similarity between the query embedding and chunk embeddings (pgvector, HNSW).
  Finds passages about a topic even when worded differently.
- keyword:  Postgres full-text search (chunks.content_tsv, GIN index; heading words weigh more).
  Finds exact terms the embedding blurs: names, error codes, identifiers. Needs no Ollama.
- hybrid (default): both, merged with reciprocal rank fusion (RRF): each list contributes
  1 / (RRF_K + rank) per chunk and the sums are sorted. RRF uses only ranks, because cosine
  similarity (~0.5-0.8) and ts_rank (small, unbounded) aren't on comparable scales; a weighted
  score mix would need tuning and drift as content changes. Chunks both methods agree on rise to
  the top. If Ollama is down, hybrid falls back to keyword-only with a warning.
"""

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import cast, func, literal, null, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import Chunk, Document
from app.schemas.knowledge import KnowledgeHit, SearchMode
from app.watcher.embedder import QUERY_PREFIX, EmbeddingError, OllamaEmbedder

# Enough HNSW candidates for the largest allowed limit (50) with room to spare; the default is 40.
HNSW_EF_SEARCH = 100
RRF_K = 60  # the standard constant from the RRF paper; dampens the gap between top ranks
CANDIDATES = 50  # results taken from each method before fusing


class SearchUnavailable(RuntimeError):
    """The query could not be embedded (Ollama down, model missing, ...)."""


@dataclass
class SearchResult:
    hits: list[KnowledgeHit]
    warnings: list[str]


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


def search_chunks(
    db: Session, embedder: OllamaEmbedder, q: str, limit: int, source: str | None = None, mode: SearchMode = "hybrid"
) -> SearchResult:
    """Top `limit` chunks for `q`, optionally within one documents.source.

    Raises SearchUnavailable only in semantic mode; hybrid degrades to keyword-only.
    """
    warnings: list[str] = []
    vector = None
    if mode in ("semantic", "hybrid"):
        try:
            vector = embed_query(embedder, q)
        except SearchUnavailable as e:
            if mode == "semantic":
                raise
            warnings.append(f"semantic search unavailable ({e}); showing keyword matches only")

    tsquery = func.websearch_to_tsquery(cast(literal("english"), REGCONFIG), q)
    matches = Chunk.content_tsv.op("@@")(tsquery)
    distance = Chunk.embedding.cosine_distance(vector) if vector is not None else None
    columns = [
        Chunk.id,
        Document.source,
        Document.path,
        Document.title,
        Chunk.chunk_index,
        Chunk.content,
        Chunk.meta,
        (1 - distance).label("semantic") if distance is not None else null().label("semantic"),
        func.nullif(func.ts_rank_cd(Chunk.content_tsv, tsquery), 0).label("keyword"),
    ]

    def base():
        stmt = select(*columns).join(Document, Chunk.document_id == Document.id)
        return stmt.where(Document.source == source) if source else stmt

    pool = limit if mode != "hybrid" else max(limit, CANDIDATES)
    lists: list[list] = []
    if distance is not None:
        # SET LOCAL lasts until the end of this request's transaction only.
        db.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
        if source:
            # pgvector >= 0.8: keep scanning the index when the source filter discards candidates.
            db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        lists.append(
            db.execute(base().where(Chunk.embedding.is_not(None)).order_by(distance, Chunk.id).limit(pool)).all()
        )
    if mode in ("keyword", "hybrid"):
        rank = func.ts_rank_cd(Chunk.content_tsv, tsquery)
        lists.append(db.execute(base().where(matches).order_by(rank.desc(), Chunk.id).limit(pool)).all())

    if mode == "hybrid":
        ordered = fuse(lists)
    else:
        rows = lists[0] if lists else []
        ordered = [(r, r.semantic if mode == "semantic" else r.keyword) for r in rows]
    hits = [
        KnowledgeHit(
            score=round(score, 6 if mode == "hybrid" else 4),
            semantic_score=round(r.semantic, 4) if r.semantic is not None else None,
            keyword_score=round(r.keyword, 4) if r.keyword is not None else None,
            source=r.source,
            path=r.path,
            title=r.title,
            chunk_index=r.chunk_index,
            heading_path=r.meta.get("heading_path", []),
            start_line=r.meta.get("start_line"),
            end_line=r.meta.get("end_line"),
            content=r.content,
        )
        for r, score in ordered[:limit]
    ]
    return SearchResult(hits=hits, warnings=warnings)


def fuse(ranked_lists: list[list]) -> list[tuple]:
    """Reciprocal rank fusion: [(row, score)] best first. Rows need .id, .semantic, .keyword.

    Ties (common: the #1 of each list scores the same) go to rows containing the query's exact
    words, then to higher semantic similarity. An exact match is stronger evidence than being the
    nearest embedding, which for a query like an error code can be an unrelated note.
    """
    rows: dict = {}
    fused: dict = {}
    for ranked in ranked_lists:
        for position, row in enumerate(ranked, start=1):
            rows[row.id] = row
            fused[row.id] = fused.get(row.id, 0.0) + 1.0 / (RRF_K + position)
    order = sorted(rows.values(), key=lambda r: (-fused[r.id], r.keyword is None, -(r.semantic or 0), str(r.id)))
    return [(r, fused[r.id]) for r in order]
