from pydantic import BaseModel, Field


class KnowledgeHit(BaseModel):
    score: float = Field(..., description="Cosine similarity to the query (1 = identical direction)", examples=[0.79])
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
    source: str | None
    results: list[KnowledgeHit]
