import uuid
from datetime import datetime

from sqlalchemy import Boolean, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ArchiveEntry(Base):
    __tablename__ = "archive_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    is_dir: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
