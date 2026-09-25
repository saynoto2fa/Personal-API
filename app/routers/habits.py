import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.habits import Habit, HabitCheckin
from app.schemas.habits import HabitCheckinCreate, HabitCheckinRead, HabitCreate, HabitRead, HabitUpdate

router = APIRouter(prefix="/habits", tags=["habits"])

DB = Annotated[Session, Depends(get_db)]
NOT_NULL_FIELDS = ("name", "active")


def _get_or_404(db: Session, habit_id: uuid.UUID) -> Habit:
    habit = db.get(Habit, habit_id)
    if habit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Habit not found")
    return habit


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A habit with this name already exists")


@router.get("", response_model=list[HabitRead])
def list_habits(
    db: DB,
    active: Annotated[bool | None, Query(description="true: only active habits, false: only inactive")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Habit]:
    stmt = select(Habit)
    if active is not None:
        stmt = stmt.where(Habit.active == active)
    stmt = stmt.order_by(func.lower(Habit.name), Habit.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("", response_model=HabitRead, status_code=status.HTTP_201_CREATED)
def create_habit(payload: HabitCreate, db: DB) -> Habit:
    habit = Habit(**payload.model_dump())
    db.add(habit)
    _commit(db)
    db.refresh(habit)
    return habit


@router.get("/{habit_id}", response_model=HabitRead)
def get_habit(habit_id: uuid.UUID, db: DB) -> Habit:
    return _get_or_404(db, habit_id)


@router.patch("/{habit_id}", response_model=HabitRead)
def update_habit(habit_id: uuid.UUID, payload: HabitUpdate, db: DB) -> Habit:
    changes = payload.model_dump(exclude_unset=True)
    if nulls := [f for f in NOT_NULL_FIELDS if f in changes and changes[f] is None]:
        raise HTTPException(422, f"Fields cannot be null: {', '.join(nulls)}")
    habit = _get_or_404(db, habit_id)
    for field, value in changes.items():
        setattr(habit, field, value)
    _commit(db)
    db.refresh(habit)
    return habit


@router.delete("/{habit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_habit(habit_id: uuid.UUID, db: DB) -> Response:
    """Deleting a habit also deletes its check-ins (ON DELETE CASCADE)."""
    habit = _get_or_404(db, habit_id)
    db.delete(habit)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Check-ins: one per habit per day
# ---------------------------------------------------------------------------


@router.get("/{habit_id}/checkins", response_model=list[HabitCheckinRead])
def list_checkins(
    habit_id: uuid.UUID,
    db: DB,
    from_: Annotated[date | None, Query(alias="from", description="Only check-ins on or after this day")] = None,
    to: Annotated[date | None, Query(description="Only check-ins on or before this day")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[HabitCheckin]:
    """A habit's check-ins, most recent day first."""
    if from_ and to and from_ > to:
        raise HTTPException(422, "from must not be after to")
    _get_or_404(db, habit_id)
    stmt = select(HabitCheckin).where(HabitCheckin.habit_id == habit_id)
    if from_:
        stmt = stmt.where(HabitCheckin.checkin_date >= from_)
    if to:
        stmt = stmt.where(HabitCheckin.checkin_date <= to)
    stmt = stmt.order_by(HabitCheckin.checkin_date.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("/{habit_id}/checkins", response_model=HabitCheckinRead, status_code=status.HTTP_201_CREATED)
def create_checkin(habit_id: uuid.UUID, payload: HabitCheckinCreate, db: DB) -> HabitCheckin:
    """Record a habit for a day (today by default). A habit has at most one check-in per day."""
    _get_or_404(db, habit_id)
    # Always send the day explicitly: the column's CURRENT_DATE default is the database's (UTC) date,
    # which is already tomorrow on a US evening.
    day = payload.checkin_date or date.today()
    if day > date.today():
        raise HTTPException(422, "checkin_date cannot be in the future")
    checkin = HabitCheckin(habit_id=habit_id, checkin_date=day, done=payload.done, value=payload.value, note=payload.note)
    db.add(checkin)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"This habit already has a check-in for {day}")
    db.refresh(checkin)
    return checkin


@router.delete("/{habit_id}/checkins/{checkin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_checkin(habit_id: uuid.UUID, checkin_id: uuid.UUID, db: DB) -> Response:
    """Remove a mistaken check-in."""
    checkin = db.get(HabitCheckin, checkin_id)
    if checkin is None or checkin.habit_id != habit_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Check-in not found")
    db.delete(checkin)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
