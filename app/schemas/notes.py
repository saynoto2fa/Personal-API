import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import normalize_tags, strip_required


class _NoteFields(BaseModel):
    title: str | None = Field(None, max_length=200)
    source: str | None = Field(None, max_length=100, examples=["chat"])


class NoteCreate(_NoteFields):
    body: str = Field(..., min_length=1, examples=["Call the plumber about the kitchen sink"])
    tags: list[str] = Field(default_factory=list, examples=[["home"]])

    @field_validator("body")
    @classmethod
    def _strip_body(cls, v: str) -> str:
        return strip_required(v, "body")

    @field_validator("tags")
    @classmethod
    def _norm_tags(cls, v: list[str]) -> list[str]:
        return normalize_tags(v)


class NoteUpdate(_NoteFields):
    """Partial update: only fields present in the request body are changed."""

    body: str | None = Field(None, min_length=1)
    tags: list[str] | None = None

    @field_validator("body")
    @classmethod
    def _strip_body(cls, v: str | None) -> str | None:
        return strip_required(v, "body")

    @field_validator("tags")
    @classmethod
    def _norm_tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_tags(v)


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    body: str
    tags: list[str]
    source: str | None
    created_at: datetime
    updated_at: datetime
