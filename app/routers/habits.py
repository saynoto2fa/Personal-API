import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.habits import Habit
from app.schemas.habits import HabitCreate, HabitRead, HabitUpdate

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
