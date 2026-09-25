import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, not_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.pantry import PantryItem
from app.schemas.pantry import (
    PantryAdjust,
    PantryItemCreate,
    PantryItemRead,
    PantryItemUpdate,
    normalize_tags,
)

router = APIRouter(prefix="/pantry", tags=["pantry"])

DB = Annotated[Session, Depends(get_db)]
NOT_NULL_FIELDS = ("name", "qty", "dietary_tags")


def _get_or_404(db: Session, item_id: uuid.UUID) -> PantryItem:
    item = db.get(PantryItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pantry item not found")
    return item


@router.get("", response_model=list[PantryItemRead])
def list_items(
    db: DB,
    q: Annotated[str | None, Query(description="Case-insensitive substring match on name")] = None,
    location: str | None = None,
    tag: Annotated[list[str], Query(description="Item must have ALL of these tags (repeatable)")] = [],
    exclude_tag: Annotated[list[str], Query(description="Item must have NONE of these tags (repeatable)")] = [],
    expiring_within_days: Annotated[
        int | None, Query(ge=0, description="Only items with an expiry estimate within N days (includes already expired)")
    ] = None,
    in_stock: Annotated[bool | None, Query(description="true: qty > 0, false: qty = 0")] = None,
    sort: Literal["name", "expiry", "updated"] = "name",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PantryItem]:
    stmt = select(PantryItem)
    if q:
        stmt = stmt.where(PantryItem.name.ilike(f"%{q}%"))
    if location:
        stmt = stmt.where(func.lower(PantryItem.location) == location.lower())
    if tags := normalize_tags(tag):
        stmt = stmt.where(PantryItem.dietary_tags.contains(tags))
    if excluded := normalize_tags(exclude_tag):
        stmt = stmt.where(not_(PantryItem.dietary_tags.overlap(excluded)))
    if expiring_within_days is not None:
        cutoff = date.today() + timedelta(days=expiring_within_days)
        stmt = stmt.where(PantryItem.expiry_estimate <= cutoff)
    if in_stock is True:
        stmt = stmt.where(PantryItem.qty > 0)
    elif in_stock is False:
        stmt = stmt.where(PantryItem.qty == 0)

    order = {
        "name": (func.lower(PantryItem.name),),
        "expiry": (PantryItem.expiry_estimate.asc().nulls_last(), func.lower(PantryItem.name)),
        "updated": (PantryItem.updated_at.desc(),),
    }[sort]
    stmt = stmt.order_by(*order, PantryItem.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("", response_model=PantryItemRead, status_code=status.HTTP_201_CREATED)
def create_item(payload: PantryItemCreate, db: DB) -> PantryItem:
    item = PantryItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/{item_id}", response_model=PantryItemRead)
def get_item(item_id: uuid.UUID, db: DB) -> PantryItem:
    return _get_or_404(db, item_id)


@router.patch("/{item_id}", response_model=PantryItemRead)
def update_item(item_id: uuid.UUID, payload: PantryItemUpdate, db: DB) -> PantryItem:
    changes = payload.model_dump(exclude_unset=True)
    if nulls := [f for f in NOT_NULL_FIELDS if f in changes and changes[f] is None]:
        raise HTTPException(422, f"Fields cannot be null: {', '.join(nulls)}")
    item = _get_or_404(db, item_id)
    for field, value in changes.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@router.post("/{item_id}/adjust", response_model=PantryItemRead)
def adjust_qty(item_id: uuid.UUID, payload: PantryAdjust, db: DB) -> PantryItem:
    """Add/subtract quantity. The result is clamped at 0 (item is kept, not deleted)."""
    item = _get_or_404(db, item_id)
    item.qty = max(Decimal("0"), item.qty + Decimal(str(payload.delta)))
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: uuid.UUID, db: DB) -> Response:
    item = _get_or_404(db, item_id)
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
