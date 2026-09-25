from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict:
    db.execute(text("SELECT 1"))
    migrations = list(db.scalars(text("SELECT version FROM schema_migrations ORDER BY version")))
    return {"status": "ok", "version": __version__, "migrations": migrations}
