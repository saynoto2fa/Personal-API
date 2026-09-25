"""Database tests run against a real Postgres database; pure unit tests run anywhere.

Set TEST_DATABASE_URL to a *throwaway* database — tables are truncated between tests.
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/personal_api_test pytest
Without it, tests that need the database are skipped.
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# Must happen before app modules create the engine. Without a test database, point the app
# at an unreachable address so no test can ever touch the real DATABASE_URL from .env.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL or "postgresql://nobody@127.0.0.1:1/no_test_database"
os.environ["API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.migrate import migrate  # noqa: E402


@pytest.fixture(scope="session")
def migrated_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    migrate(get_settings().libpq_url)


@pytest.fixture
def api_client():
    """TestClient for tests that never reach the database (auth, request validation)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(migrated_db):
    with TestClient(app) as c:
        yield c
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE pantry_items, schedule, habits, habit_checkins, contacts, notes, documents, archive_entries CASCADE")
        )
