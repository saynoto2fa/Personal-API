import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import strip_required


class _HabitFields(BaseModel):
    description: str | None = None
    target_per_week: int | None = Field(None, ge=1, le=7, examples=[5])
    unit: str | None = Field(None, max_length=50, description="For measured habits", examples=["min"])


class HabitCreate(_HabitFields):
    name: str = Field(..., min_length=1, max_length=200, examples=["Read"])
    active: bool = True

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return strip_required(v, "name")


class HabitUpdate(_HabitFields):
    """Partial update: only fields present in the request body are changed."""

    name: str | None = Field(None, min_length=1, max_length=200)
    active: bool | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        return strip_required(v, "name")


class HabitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    target_per_week: int | None
    unit: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class HabitCheckinCreate(BaseModel):
    checkin_date: date | None = Field(None, description="Day of the check-in; defaults to today (server's local date)")
    done: bool = Field(True, description="false records that the habit was explicitly skipped that day")
    value: float | None = Field(None, description="For measured habits, in the habit's unit", examples=[20])
    note: str | None = Field(None, max_length=1000)


class HabitCheckinRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    habit_id: uuid.UUID
    checkin_date: date
    done: bool
    value: float | None
    note: str | None
    created_at: datetime
