from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.knowledge import KnowledgeSearchResponse
from app.search import SearchUnavailable, get_embedder, search_chunks
from app.watcher.embedder import OllamaEmbedder

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DB = Annotated[Session, Depends(get_db)]
Embedder = Annotated[OllamaEmbedder, Depends(get_embedder)]


@router.get("/search", response_model=KnowledgeSearchResponse)
def search(
    db: DB,
    embedder: Embedder,
    q: Annotated[str, Query(min_length=1, max_length=1000, description="What to look for, in plain language")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    source: Annotated[str | None, Query(description="Only search this source, e.g. 'vault'")] = None,
) -> KnowledgeSearchResponse:
    """Semantic search over indexed files (Stage 3 watcher), best match first."""
    q = q.strip()
    if not q:
        raise HTTPException(422, "q must not be blank")
    try:
        results = search_chunks(db, embedder, q, limit, source)
    except SearchUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Knowledge search is unavailable: {e}")
    return KnowledgeSearchResponse(query=q, source=source, results=results)
