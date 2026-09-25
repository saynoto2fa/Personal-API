import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.knowledge import KnowledgeHit


class ContextEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    location: str | None
    category: str | None


class ContextPantryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    qty: float
    unit: str | None
    location: str | None
    expiry_estimate: date | None


class PantrySummary(BaseModel):
    total_items: int
    out_of_stock: int
    expiring: list[ContextPantryItem] = Field(
        description="In stock and expiring within the window (already-expired items included), soonest first"
    )


class HabitProgress(BaseModel):
    id: uuid.UUID
    name: str
    unit: str | None
    target_per_week: int | None
    done_this_week: int = Field(description="Check-ins marked done since Monday")
    done_today: bool


class MeContext(BaseModel):
    generated_at: datetime
    today: date
    q: str | None = Field(description="Topic used for the knowledge section, if any")
    days: int = Field(description="Look-ahead window used for schedule and expiring pantry items")
    schedule: list[ContextEvent]
    pantry: PantrySummary
    habits: list[HabitProgress]
    knowledge: list[KnowledgeHit] = Field(description="Top matching notes for `q`; empty when no `q` is given")
    warnings: list[str] = Field(default_factory=list, description="Sections that could not be filled, and why")
