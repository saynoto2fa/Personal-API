from typing import Literal

from pydantic import BaseModel, Field

SearchMode = Literal["hybrid", "semantic", "keyword"]


class KnowledgeHit(BaseModel):
    score: float = Field(
        ...,
        description="Ranking score for the mode used: hybrid = reciprocal-rank-fusion score, "
        "semantic = cosine similarity, keyword = full-text rank. Compare within one response only.",
        examples=[0.0325],
    )
    semantic_score: float | None = Field(
        None, description="Cosine similarity to the query (1 = identical direction; ~0.7+ is strong). Null in keyword mode."
    )
    keyword_score: float | None = Field(
        None, description="Postgres full-text rank; null when the chunk doesn't contain the query terms"
    )
    source: str = Field(..., examples=["vault"])
    path: str = Field(..., description="File path relative to the source folder", examples=["Windows-Process-Auditor.md"])
    title: str | None
    chunk_index: int
    heading_path: list[str] = Field(default_factory=list, examples=[["Windows Background Process Auditor", "Steps"]])
    start_line: int | None = Field(None, description="1-based line in the file where this chunk starts")
    end_line: int | None = None
    content: str


class KnowledgeSearchResponse(BaseModel):
    query: str
    mode: SearchMode
    source: str | None
    results: list[KnowledgeHit]
    warnings: list[str] = Field(default_factory=list, description="E.g. hybrid fell back to keyword-only because Ollama is down")
