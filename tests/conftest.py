"""Tests run against a real Postgres database.

Set TEST_DATABASE_URL to a *throwaway* database — tables are truncated between tests.
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/personal_api_test pytest
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)

# Must happen before app modules create the engine.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.migrate import migrate  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    migrate(get_settings().libpq_url)


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE pantry_items"))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
