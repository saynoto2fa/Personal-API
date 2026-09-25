import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schedule import ScheduleEvent
from app.schemas.schedule import ScheduleEventCreate, ScheduleEventRead, ScheduleEventUpdate

router = APIRouter(prefix="/schedule", tags=["schedule"])

DB = Annotated[Session, Depends(get_db)]
NOT_NULL_FIELDS = ("title", "starts_at", "all_day")


def _get_or_404(db: Session, event_id: uuid.UUID) -> ScheduleEvent:
    event = db.get(ScheduleEvent, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule event not found")
    return event


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An event with this source and external_id already exists")


@router.get("", response_model=list[ScheduleEventRead])
def list_events(
    db: DB,
    from_: Annotated[
        AwareDatetime | None, Query(alias="from", description="Only events still running at or after this time")
    ] = None,
    to: Annotated[AwareDatetime | None, Query(description="Only events starting at or before this time")] = None,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ScheduleEvent]:
    """Events sorted by start time. Recurring events are returned once (RRULEs are not expanded)."""
    stmt = select(ScheduleEvent)
    if from_ is not None:
        stmt = stmt.where(func.coalesce(ScheduleEvent.ends_at, ScheduleEvent.starts_at) >= from_)
    if to is not None:
        stmt = stmt.where(ScheduleEvent.starts_at <= to)
    if category:
        stmt = stmt.where(func.lower(ScheduleEvent.category) == category.lower())
    stmt = stmt.order_by(ScheduleEvent.starts_at, ScheduleEvent.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("", response_model=ScheduleEventRead, status_code=status.HTTP_201_CREATED)
def create_event(payload: ScheduleEventCreate, db: DB) -> ScheduleEvent:
    event = ScheduleEvent(**payload.model_dump())
    db.add(event)
    _commit(db)
    db.refresh(event)
    return event


@router.get("/{event_id}", response_model=ScheduleEventRead)
def get_event(event_id: uuid.UUID, db: DB) -> ScheduleEvent:
    return _get_or_404(db, event_id)


@router.patch("/{event_id}", response_model=ScheduleEventRead)
def update_event(event_id: uuid.UUID, payload: ScheduleEventUpdate, db: DB) -> ScheduleEvent:
    changes = payload.model_dump(exclude_unset=True)
    if nulls := [f for f in NOT_NULL_FIELDS if f in changes and changes[f] is None]:
        raise HTTPException(422, f"Fields cannot be null: {', '.join(nulls)}")
    event = _get_or_404(db, event_id)
    starts_at = changes.get("starts_at", event.starts_at)
    ends_at = changes.get("ends_at", event.ends_at)
    if ends_at is not None and ends_at < starts_at:
        raise HTTPException(422, "ends_at must not be before starts_at")
    for field, value in changes.items():
        setattr(event, field, value)
    _commit(db)
    db.refresh(event)
    return event


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: uuid.UUID, db: DB) -> Response:
    event = _get_or_404(db, event_id)
    db.delete(event)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
