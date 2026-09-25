import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.contacts import Contact
from app.schemas.common import normalize_tags
from app.schemas.contacts import ContactCreate, ContactRead, ContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])

DB = Annotated[Session, Depends(get_db)]
NOT_NULL_FIELDS = ("name", "dietary_tags", "tags")


def _get_or_404(db: Session, contact_id: uuid.UUID) -> Contact:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    return contact


@router.get("", response_model=list[ContactRead])
def list_contacts(
    db: DB,
    q: Annotated[str | None, Query(description="Case-insensitive substring match on name")] = None,
    tag: Annotated[list[str], Query(description="Contact must have ALL of these tags (repeatable)")] = [],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Contact]:
    stmt = select(Contact)
    if q:
        stmt = stmt.where(Contact.name.ilike(f"%{q}%"))
    if tags := normalize_tags(tag):
        stmt = stmt.where(Contact.tags.contains(tags))
    stmt = stmt.order_by(func.lower(Contact.name), Contact.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("", response_model=ContactRead, status_code=status.HTTP_201_CREATED)
def create_contact(payload: ContactCreate, db: DB) -> Contact:
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.get("/{contact_id}", response_model=ContactRead)
def get_contact(contact_id: uuid.UUID, db: DB) -> Contact:
    return _get_or_404(db, contact_id)


@router.patch("/{contact_id}", response_model=ContactRead)
def update_contact(contact_id: uuid.UUID, payload: ContactUpdate, db: DB) -> Contact:
    changes = payload.model_dump(exclude_unset=True)
    if nulls := [f for f in NOT_NULL_FIELDS if f in changes and changes[f] is None]:
        raise HTTPException(422, f"Fields cannot be null: {', '.join(nulls)}")
    contact = _get_or_404(db, contact_id)
    for field, value in changes.items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: uuid.UUID, db: DB) -> Response:
    contact = _get_or_404(db, contact_id)
    db.delete(contact)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
