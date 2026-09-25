import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import normalize_tag, normalize_tags  # noqa: F401  (re-exported for callers)

# Suggested vocabulary (not enforced — any snake_case tag is accepted):
#   gluten_free, contains_gluten, may_contain_gluten,
#   contains_pork, pork_free, dairy_free, vegetarian, vegan, nut_free
RECOMMENDED_DIETARY_TAGS = (
    "gluten_free",
    "contains_gluten",
    "may_contain_gluten",
    "contains_pork",
    "pork_free",
    "dairy_free",
    "vegetarian",
    "vegan",
    "nut_free",
)


class _PantryFields(BaseModel):
    unit: str | None = Field(None, max_length=50, examples=["g"])
    location: str | None = Field(None, max_length=100, examples=["pantry"])
    expiry_estimate: date | None = None
    notes: str | None = None


class PantryItemCreate(_PantryFields):
    name: str = Field(..., min_length=1, max_length=200, examples=["Rice pasta"])
    qty: float = Field(1, ge=0)
    dietary_tags: list[str] = Field(default_factory=list, examples=[["gluten_free", "pork_free"]])

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("dietary_tags")
    @classmethod
    def _norm_tags(cls, v: list[str]) -> list[str]:
        return normalize_tags(v)


class PantryItemUpdate(_PantryFields):
    """Partial update: only fields present in the request body are changed."""

    name: str | None = Field(None, min_length=1, max_length=200)
    qty: float | None = Field(None, ge=0)
    dietary_tags: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("dietary_tags")
    @classmethod
    def _norm_tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_tags(v)


class PantryAdjust(BaseModel):
    """Change quantity relative to the current value, e.g. used 2 eggs -> delta=-2."""

    delta: float = Field(..., examples=[-2])


class PantryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    qty: float
    unit: str | None
    location: str | None
    expiry_estimate: date | None
    dietary_tags: list[str]
    notes: str | None
    created_at: datetime
    updated_at: datetime
