from fastapi import Depends, FastAPI

from app import __version__
from app.auth import require_api_key
from app.routers import contacts, context, habits, health, knowledge, notes, pantry, schedule

app = FastAPI(
    title="Personal Unified API",
    version=__version__,
    description="Single source of truth for pantry, schedule, habits, contacts, notes and indexed knowledge.",
)

app.include_router(health.router)
for module in (pantry, schedule, habits, contacts, notes, knowledge, context):
    app.include_router(module.router, dependencies=[Depends(require_api_key)])
