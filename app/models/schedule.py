import uuid
from datetime import datetime

from sqlalchemy import Boolean, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ScheduleEvent(Base):
    __tablename__ = "schedule"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    title: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    location: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    recurrence_rule: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
