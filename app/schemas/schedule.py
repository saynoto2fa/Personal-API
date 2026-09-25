import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import strip_required


class _ScheduleFields(BaseModel):
    ends_at: AwareDatetime | None = Field(None, examples=["2026-10-01T10:00:00-06:00"])
    location: str | None = Field(None, max_length=200)
    category: str | None = Field(None, max_length=100, examples=["work"])
    recurrence_rule: str | None = Field(
        None, max_length=500, description="RFC 5545 RRULE, stored as-is", examples=["FREQ=WEEKLY;BYDAY=MO,WE"]
    )
    source: str | None = Field(None, max_length=100, examples=["manual"])
    external_id: str | None = Field(None, max_length=200, description="Id in the source system; unique per source")
    notes: str | None = None

    @field_validator("recurrence_rule")
    @classmethod
    def _check_rrule(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if "FREQ=" not in v.upper():
            raise ValueError("recurrence_rule must be an RRULE containing FREQ=, e.g. FREQ=WEEKLY;BYDAY=MO")
        return v


class ScheduleEventCreate(_ScheduleFields):
    title: str = Field(..., min_length=1, max_length=200, examples=["Dentist"])
    starts_at: AwareDatetime = Field(..., examples=["2026-10-01T09:00:00-06:00"])
    all_day: bool = False

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        return strip_required(v, "title")

    @model_validator(mode="after")
    def _ends_after_starts(self) -> "ScheduleEventCreate":
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("ends_at must not be before starts_at")
        return self


class ScheduleEventUpdate(_ScheduleFields):
    """Partial update: only fields present in the request body are changed."""

    title: str | None = Field(None, min_length=1, max_length=200)
    starts_at: AwareDatetime | None = None
    all_day: bool | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str | None) -> str | None:
        return strip_required(v, "title")


class ScheduleEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    location: str | None
    category: str | None
    recurrence_rule: str | None
    source: str | None
    external_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
