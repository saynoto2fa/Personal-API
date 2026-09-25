"""Database tests run against a throwaway Postgres; pure unit tests run anywhere.

By default the local test database from `python -m tests.testdb setup` is used (and started if
needed). Set TEST_DATABASE_URL to use another *throwaway* database instead. Tables are truncated
between tests, so this must never be a real database: URLs pointing at Supabase or equal to
DATABASE_URL in .env are refused. With no test database at all, DB tests are skipped.
"""

import os
from pathlib import Path

import pytest

from app.config import Settings
from tests import testdb

REPO = Path(__file__).resolve().parents[1]

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_started_testdb = False
if not TEST_DATABASE_URL and testdb.installed():
    _started_testdb = not testdb.running()
    testdb.start()
    TEST_DATABASE_URL = testdb.URL

if TEST_DATABASE_URL:
    live = Settings(_env_file=REPO / ".env").database_url
    if TEST_DATABASE_URL == live or "supabase" in TEST_DATABASE_URL.lower():
        raise pytest.UsageError(
            "TEST_DATABASE_URL points at a real database; the tests truncate tables. "
            "Use the local test database (python -m tests.testdb setup) or another throwaway one."
        )

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


def pytest_report_header(config):
    return f"test database: {TEST_DATABASE_URL or 'none (DB tests will be skipped)'}"


def pytest_sessionfinish(session, exitstatus):
    if _started_testdb:
        engine.dispose()
        testdb.stop()  # leave the machine as we found it


@pytest.fixture(scope="session")
def migrated_db():
    if not TEST_DATABASE_URL:
        pytest.skip("no test database (run: python -m tests.testdb setup)")
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
