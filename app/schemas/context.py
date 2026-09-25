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


class ContextSummary(BaseModel):
    """Always returned: counts and flags only, so the default call stays small and fast."""

    schedule_count_today: int = Field(description="Events that overlap today (server's local day)")
    schedule_count_upcoming: int = Field(description="Events not yet over that start within `days`")
    next_event: ContextEvent | None = Field(description="The next event that hasn't ended, if any within `days`")
    pantry_total: int
    pantry_out_of_stock: int
    pantry_expiring_count: int = Field(description="In-stock items expiring within `days`, including already expired")
    habits_active: int
    habits_done_today: int = Field(description="Active habits with a check-in marked done today")
    habits_done_this_week: int = Field(description="Check-ins marked done since Monday, across active habits")


class MeContext(BaseModel):
    generated_at: datetime
    today: date
    q: str | None = Field(description="Topic used for the knowledge section, if any")
    days: int = Field(description="Look-ahead window used for schedule and expiring pantry items")
    include: list[str] = Field(description="Sections returned in full detail")
    filters: dict = Field(
        default_factory=dict,
        description="Topic filters applied to detail sections (schedule_category, pantry_tag, ...); the summary ignores them",
    )
    summary: ContextSummary
    schedule: list[ContextEvent] | None = Field(None, description="Only with include=schedule")
    pantry: PantrySummary | None = Field(None, description="Only with include=pantry")
    habits: list[HabitProgress] | None = Field(None, description="Only with include=habits")
    knowledge: list[KnowledgeHit] | None = Field(None, description="Top notes for `q`; only when `q` is given")
    warnings: list[str] = Field(default_factory=list, description="Sections that could not be filled, and why")
