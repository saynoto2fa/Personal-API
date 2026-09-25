import re
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import normalize_tags, strip_required

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _ContactFields(BaseModel):
    relationship: str | None = Field(None, max_length=100, examples=["friend"])
    email: str | None = Field(None, max_length=320, examples=["jane@example.com"])
    phone: str | None = Field(None, max_length=50)
    birthday: date | None = None
    notes: str | None = None
    vault_path: str | None = Field(None, max_length=500, examples=["people/jane-doe.md"])

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("email is not a valid address")
        return v

    @field_validator("birthday")
    @classmethod
    def _birthday_not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("birthday cannot be in the future")
        return v


class ContactCreate(_ContactFields):
    name: str = Field(..., min_length=1, max_length=200, examples=["Jane Doe"])
    dietary_tags: list[str] = Field(default_factory=list, examples=[["gluten_free"]])
    tags: list[str] = Field(default_factory=list, examples=[["book_club"]])

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return strip_required(v, "name")

    @field_validator("dietary_tags", "tags")
    @classmethod
    def _norm_tags(cls, v: list[str]) -> list[str]:
        return normalize_tags(v)


class ContactUpdate(_ContactFields):
    """Partial update: only fields present in the request body are changed."""

    name: str | None = Field(None, min_length=1, max_length=200)
    dietary_tags: list[str] | None = None
    tags: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        return strip_required(v, "name")

    @field_validator("dietary_tags", "tags")
    @classmethod
    def _norm_tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_tags(v)


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    relationship: str | None
    email: str | None
    phone: str | None
    birthday: date | None
    dietary_tags: list[str]
    tags: list[str]
    notes: str | None
    vault_path: str | None
    created_at: datetime
    updated_at: datetime
