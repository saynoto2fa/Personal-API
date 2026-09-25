import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.notes import Note
from app.schemas.common import normalize_tags
from app.schemas.notes import NoteCreate, NoteRead, NoteUpdate

router = APIRouter(prefix="/notes", tags=["notes"])

DB = Annotated[Session, Depends(get_db)]
NOT_NULL_FIELDS = ("body", "tags")


def _get_or_404(db: Session, note_id: uuid.UUID) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note


@router.get("", response_model=list[NoteRead])
def list_notes(
    db: DB,
    q: Annotated[str | None, Query(description="Case-insensitive substring match on title or body")] = None,
    tag: Annotated[list[str], Query(description="Note must have ALL of these tags (repeatable)")] = [],
    source: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Note]:
    """Newest first."""
    stmt = select(Note)
    if q:
        stmt = stmt.where(or_(Note.title.ilike(f"%{q}%"), Note.body.ilike(f"%{q}%")))
    if tags := normalize_tags(tag):
        stmt = stmt.where(Note.tags.contains(tags))
    if source:
        stmt = stmt.where(Note.source == source)
    stmt = stmt.order_by(Note.created_at.desc(), Note.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
def create_note(payload: NoteCreate, db: DB) -> Note:
    note = Note(**payload.model_dump())
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/{note_id}", response_model=NoteRead)
def get_note(note_id: uuid.UUID, db: DB) -> Note:
    return _get_or_404(db, note_id)


@router.patch("/{note_id}", response_model=NoteRead)
def update_note(note_id: uuid.UUID, payload: NoteUpdate, db: DB) -> Note:
    changes = payload.model_dump(exclude_unset=True)
    if nulls := [f for f in NOT_NULL_FIELDS if f in changes and changes[f] is None]:
        raise HTTPException(422, f"Fields cannot be null: {', '.join(nulls)}")
    note = _get_or_404(db, note_id)
    for field, value in changes.items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: uuid.UUID, db: DB) -> Response:
    note = _get_or_404(db, note_id)
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
