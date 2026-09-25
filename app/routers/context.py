from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, not_, select, true
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.habits import Habit, HabitCheckin
from app.models.pantry import PantryItem
from app.models.schedule import ScheduleEvent
from app.schemas.context import (
    ContextEvent,
    ContextPantryItem,
    ContextSummary,
    HabitProgress,
    MeContext,
    PantrySummary,
)
from app.schemas.common import normalize_tags
from app.search import SearchUnavailable, get_embedder, search_chunks
from app.watcher.embedder import OllamaEmbedder

router = APIRouter(prefix="/me", tags=["context"])

DB = Annotated[Session, Depends(get_db)]
Embedder = Annotated[OllamaEmbedder, Depends(get_embedder)]

SECTIONS = ("pantry", "schedule", "habits", "knowledge")

# Caps keep detail sections small and fast; use the resource endpoints for full lists.
MAX_EVENTS = 20
MAX_EXPIRING = 15
MAX_HABITS = 30


def _event_end():
    """When an event stops being relevant: its end, or the whole day for all-day events without one."""
    return func.coalesce(
        ScheduleEvent.ends_at,
        case((ScheduleEvent.all_day, ScheduleEvent.starts_at + timedelta(days=1)), else_=ScheduleEvent.starts_at),
    )


def _upcoming(now: datetime, days: int):
    return and_(_event_end() >= now, ScheduleEvent.starts_at <= now + timedelta(days=days))


def _expiring(today: date, days: int):
    return and_(PantryItem.qty > 0, PantryItem.expiry_estimate <= today + timedelta(days=days))


def _week_checkins(today: date):
    monday = today - timedelta(days=today.weekday())
    return and_(
        HabitCheckin.habit_id == Habit.id,
        HabitCheckin.done.is_(true()),
        HabitCheckin.checkin_date.between(monday, today),
    )


def _summary(db: Session, now: datetime, today: date, days: int) -> ContextSummary:
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    today_overlap = and_(_event_end() >= local_midnight, ScheduleEvent.starts_at < local_midnight + timedelta(days=1))
    count_today, count_upcoming = db.execute(
        select(func.count().filter(today_overlap), func.count().filter(_upcoming(now, days))).select_from(ScheduleEvent)
    ).one()
    next_event = db.scalars(
        select(ScheduleEvent).where(_upcoming(now, days)).order_by(ScheduleEvent.starts_at, ScheduleEvent.id).limit(1)
    ).first()

    pantry_total, out_of_stock, expiring = db.execute(
        select(
            func.count(),
            func.count().filter(PantryItem.qty == 0),
            func.count().filter(_expiring(today, days)),
        ).select_from(PantryItem)
    ).one()

    habits_active, done_this_week, done_today = db.execute(
        select(
            func.count(func.distinct(Habit.id)),
            func.count(HabitCheckin.id),
            func.count(func.distinct(case((HabitCheckin.checkin_date == today, Habit.id)))),
        )
        .select_from(Habit)
        .outerjoin(HabitCheckin, _week_checkins(today))
        .where(Habit.active.is_(true()))
    ).one()

    return ContextSummary(
        schedule_count_today=count_today,
        schedule_count_upcoming=count_upcoming,
        next_event=ContextEvent.model_validate(next_event) if next_event else None,
        pantry_total=pantry_total,
        pantry_out_of_stock=out_of_stock,
        pantry_expiring_count=expiring,
        habits_active=habits_active,
        habits_done_today=done_today,
        habits_done_this_week=done_this_week,
    )


def _schedule(db: Session, now: datetime, days: int, category: str | None) -> list[ContextEvent]:
    stmt = select(ScheduleEvent).where(_upcoming(now, days))
    if category:  # same matching as GET /schedule?category=
        stmt = stmt.where(func.lower(ScheduleEvent.category) == category.lower())
    events = db.scalars(stmt.order_by(ScheduleEvent.starts_at, ScheduleEvent.id).limit(MAX_EVENTS))
    return [ContextEvent.model_validate(e) for e in events]


def _pantry_scope(tags: list[str], exclude_tags: list[str], location: str | None) -> list:
    """Same filters as GET /pantry?tag=&exclude_tag=&location=."""
    conditions = []
    if tags := normalize_tags(tags):
        conditions.append(PantryItem.dietary_tags.contains(tags))
    if excluded := normalize_tags(exclude_tags):
        conditions.append(not_(PantryItem.dietary_tags.overlap(excluded)))
    if location:
        conditions.append(func.lower(PantryItem.location) == location.lower())
    return conditions


def _pantry(db: Session, today: date, days: int, scope: list) -> PantrySummary:
    total, out_of_stock = db.execute(
        select(func.count(PantryItem.id), func.count(PantryItem.id).filter(PantryItem.qty == 0)).where(*scope)
    ).one()
    expiring = db.scalars(
        select(PantryItem)
        .where(_expiring(today, days), *scope)
        .order_by(PantryItem.expiry_estimate, func.lower(PantryItem.name))
        .limit(MAX_EXPIRING)
    )
    return PantrySummary(
        total_items=total,
        out_of_stock=out_of_stock,
        expiring=[ContextPantryItem.model_validate(i) for i in expiring],
    )


def _habits(db: Session, today: date) -> list[HabitProgress]:
    done_this_week = func.count(HabitCheckin.id)
    done_today = func.coalesce(func.bool_or(HabitCheckin.checkin_date == today), False)
    rows = db.execute(
        select(Habit, done_this_week, done_today)
        .outerjoin(HabitCheckin, _week_checkins(today))
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


def _parse_include(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if unknown := sorted(set(parts) - set(SECTIONS)):
        raise HTTPException(422, f"Unknown include value(s): {', '.join(unknown)}. Use: {', '.join(SECTIONS)}")
    return [s for s in SECTIONS if s in parts]


@router.get("/context", response_model=MeContext)
def me_context(
    db: DB,
    embedder: Embedder,
    q: Annotated[
        str | None,
        Query(max_length=1000, description="Topic to pull related notes for. Adds the knowledge section."),
    ] = None,
    include: Annotated[
        str | None,
        Query(
            description="Comma-separated sections to return in full: pantry, schedule, habits, knowledge. "
            "Without it only the summary counts (and knowledge, if q is given) are returned.",
            examples=["pantry,schedule,habits"],
        ),
    ] = None,
    days: Annotated[int, Query(ge=1, le=31, description="Look-ahead for schedule and expiring pantry items")] = 7,
    knowledge_limit: Annotated[int, Query(ge=1, le=20)] = 5,
    source: Annotated[str | None, Query(description="Only search notes in this source, e.g. 'vault'")] = None,
    schedule_category: Annotated[
        str | None, Query(description="Scope the schedule section to this category (as GET /schedule?category=). Implies include=schedule.")
    ] = None,
    pantry_tag: Annotated[
        list[str], Query(description="Scope the pantry section to items with ALL these tags (as GET /pantry?tag=). Implies include=pantry.")
    ] = [],
    pantry_exclude_tag: Annotated[
        list[str], Query(description="Scope the pantry section to items with NONE of these tags. Implies include=pantry.")
    ] = [],
    pantry_location: Annotated[
        str | None, Query(description="Scope the pantry section to one location, e.g. fridge. Implies include=pantry.")
    ] = None,
) -> MeContext:
    """A situational snapshot. By default: summary counts only, plus notes about `q` if given.

    Ask for full detail per section with `include` (e.g. `include=pantry,schedule,habits` returns
    the pre-summary response shape). Scope a section to a topic with its filters
    (`schedule_category`, `pantry_tag`, `pantry_exclude_tag`, `pantry_location`), which reuse the
    list endpoints' filters and include that section. The summary always covers everything.
    """
    if q is not None:
        q = q.strip()
        if not q:
            raise HTTPException(422, "q must not be blank when given")
    sections = _parse_include(include)
    if "knowledge" in sections and not q:
        raise HTTPException(422, "include=knowledge needs q (the topic to search notes for)")
    filters: dict = {}
    if schedule_category:
        filters["schedule_category"] = schedule_category
    if tags := normalize_tags(pantry_tag):
        filters["pantry_tag"] = tags
    if excluded := normalize_tags(pantry_exclude_tag):
        filters["pantry_exclude_tag"] = excluded
    if pantry_location:
        filters["pantry_location"] = pantry_location
    # A topic or a section filter brings its section along, as q always has for knowledge.
    implied = {"knowledge": bool(q), "schedule": "schedule_category" in filters,
               "pantry": any(k.startswith("pantry_") for k in filters)}  # fmt: skip
    sections = [s for s in SECTIONS if s in sections or implied.get(s)]

    now = datetime.now(UTC)
    today = date.today()
    warnings: list[str] = []

    knowledge = None
    if q:
        try:
            found = search_chunks(db, embedder, q, knowledge_limit, source)
            knowledge = found.hits
            warnings += [f"knowledge: {w}" for w in found.warnings]
        except SearchUnavailable as e:  # only semantic mode raises; kept for safety
            knowledge = []
            warnings.append(f"knowledge: search unavailable ({e})")

    return MeContext(
        generated_at=now,
        today=today,
        q=q,
        days=days,
        include=sections,
        filters=filters,
        summary=_summary(db, now, today, days),
        schedule=_schedule(db, now, days, schedule_category) if "schedule" in sections else None,
        pantry=_pantry(db, today, days, _pantry_scope(pantry_tag, pantry_exclude_tag, pantry_location))
        if "pantry" in sections
        else None,
        habits=_habits(db, today) if "habits" in sections else None,
        knowledge=knowledge,
        warnings=warnings,
    )
