from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, select, true
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.habits import Habit, HabitCheckin
from app.models.pantry import PantryItem
from app.models.schedule import ScheduleEvent
from app.schemas.context import ContextEvent, ContextPantryItem, HabitProgress, MeContext, PantrySummary
from app.search import SearchUnavailable, get_embedder, search_chunks
from app.watcher.embedder import OllamaEmbedder

router = APIRouter(prefix="/me", tags=["context"])

DB = Annotated[Session, Depends(get_db)]
Embedder = Annotated[OllamaEmbedder, Depends(get_embedder)]

# Caps keep the snapshot small and fast; use the resource endpoints for full lists.
MAX_EVENTS = 20
MAX_EXPIRING = 15
MAX_HABITS = 30


def _schedule(db: Session, now: datetime, days: int) -> list[ScheduleEvent]:
    # An event is relevant until it ends; all-day events without an end last one day.
    ends = func.coalesce(
        ScheduleEvent.ends_at,
        case((ScheduleEvent.all_day, ScheduleEvent.starts_at + timedelta(days=1)), else_=ScheduleEvent.starts_at),
    )
    stmt = (
        select(ScheduleEvent)
        .where(ends >= now, ScheduleEvent.starts_at <= now + timedelta(days=days))
        .order_by(ScheduleEvent.starts_at, ScheduleEvent.id)
        .limit(MAX_EVENTS)
    )
    return list(db.scalars(stmt))


def _pantry(db: Session, today: date, days: int) -> PantrySummary:
    total, out_of_stock = db.execute(
        select(func.count(PantryItem.id), func.count(PantryItem.id).filter(PantryItem.qty == 0))
    ).one()
    expiring = db.scalars(
        select(PantryItem)
        .where(PantryItem.qty > 0, PantryItem.expiry_estimate <= today + timedelta(days=days))
        .order_by(PantryItem.expiry_estimate, func.lower(PantryItem.name))
        .limit(MAX_EXPIRING)
    )
    return PantrySummary(
        total_items=total,
        out_of_stock=out_of_stock,
        expiring=[ContextPantryItem.model_validate(i) for i in expiring],
    )


def _habits(db: Session, today: date) -> list[HabitProgress]:
    monday = today - timedelta(days=today.weekday())
    done_this_week = func.count(HabitCheckin.id)
    done_today = func.coalesce(func.bool_or(HabitCheckin.checkin_date == today), False)
    rows = db.execute(
        select(Habit, done_this_week, done_today)
        .outerjoin(
            HabitCheckin,
            and_(
                HabitCheckin.habit_id == Habit.id,
                HabitCheckin.done.is_(true()),
                HabitCheckin.checkin_date.between(monday, today),
            ),
        )
        .where(Habit.active.is_(true()))
        .group_by(Habit.id)
        .order_by(func.lower(Habit.name))
        .limit(MAX_HABITS)
    ).all()
    return [
        HabitProgress(
            id=h.id,
            name=h.name,
            unit=h.unit,
            target_per_week=h.target_per_week,
            done_this_week=week,
            done_today=today_done,
        )
        for h, week, today_done in rows
    ]


@router.get("/context", response_model=MeContext)
def me_context(
    db: DB,
    embedder: Embedder,
    q: Annotated[
        str | None,
        Query(max_length=1000, description="Topic to pull related notes for. Without it the knowledge section is empty."),
    ] = None,
    days: Annotated[int, Query(ge=1, le=31, description="Look-ahead for schedule and expiring pantry items")] = 7,
    knowledge_limit: Annotated[int, Query(ge=1, le=20)] = 5,
    source: Annotated[str | None, Query(description="Only search notes in this source, e.g. 'vault'")] = None,
) -> MeContext:
    """A small situational snapshot: upcoming schedule, pantry status, this week's habits, and notes about `q`."""
    if q is not None:
        q = q.strip()
        if not q:
            raise HTTPException(422, "q must not be blank when given")
    now = datetime.now(UTC)
    today = date.today()
    warnings: list[str] = []

    knowledge = []
    if q:
        try:
            knowledge = search_chunks(db, embedder, q, knowledge_limit, source)
        except SearchUnavailable as e:
            # The structured sections are still useful; say what is missing instead of failing.
            warnings.append(f"knowledge: search unavailable ({e})")

    return MeContext(
        generated_at=now,
        today=today,
        q=q,
        days=days,
        schedule=[ContextEvent.model_validate(e) for e in _schedule(db, now, days)],
        pantry=_pantry(db, today, days),
        habits=_habits(db, today),
        knowledge=knowledge,
        warnings=warnings,
    )
