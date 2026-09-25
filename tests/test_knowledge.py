import uuid

import pytest
from sqlalchemy import delete

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models.knowledge import Document
from app.search import get_embedder
from app.watcher.indexer import Indexer, Source
from tests.fakes import KeywordEmbedder

NOTES = {
    "garden.md": "# Garden\n\nPlant tomato seedlings in warm soil.\n\n## Soil\n\nThe garden soil needs compost. tomato tomato",
    "code/db.md": "# Database notes\n\nTune the python database query planner.",
    "food/pasta.md": "# Recipes\n\nA pasta recipe with tomato sauce.",
    "money.md": "# Budget\n\nSend the invoice before the budget review.",
}


@pytest.fixture
def fake_embedder():
    embedder = KeywordEmbedder()
    app.dependency_overrides[get_embedder] = lambda: embedder
    yield embedder
    app.dependency_overrides.pop(get_embedder, None)


@pytest.fixture
def indexed(migrated_db, tmp_path, fake_embedder):
    """NOTES indexed under a unique source, removed afterwards."""
    source = Source(f"test:{uuid.uuid4().hex[:8]}", tmp_path.resolve())
    for rel, body in NOTES.items():
        path = source.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    counts, failed = Indexer(fake_embedder).sync(source)
    assert counts["indexed"] == len(NOTES) and not failed
    fake_embedder.texts.clear()
    yield source
    with SessionLocal() as s, s.begin():
        s.execute(delete(Document).where(Document.source == source.name))


# --- no database needed -------------------------------------------------------


def test_auth_required_on_search_and_context(api_client, monkeypatch, fake_embedder):
    monkeypatch.setattr(get_settings(), "api_key", "s3cret")
    for path in ("/knowledge/search?q=tomato", "/me/context"):
        assert api_client.get(path).status_code == 401
        assert api_client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401
    assert fake_embedder.texts == []  # rejected before anything was embedded


def test_search_rejects_missing_blank_or_oversized_input(api_client, fake_embedder):
    assert api_client.get("/knowledge/search").status_code == 422
    assert api_client.get("/knowledge/search", params={"q": ""}).status_code == 422
    r = api_client.get("/knowledge/search", params={"q": "   "})
    assert r.status_code == 422
    assert "blank" in r.json()["detail"]
    assert api_client.get("/knowledge/search", params={"q": "x" * 1001}).status_code == 422
    assert api_client.get("/knowledge/search", params={"q": "x", "limit": 0}).status_code == 422
    assert api_client.get("/knowledge/search", params={"q": "x", "limit": 51}).status_code == 422
    assert fake_embedder.texts == []


def test_search_returns_503_when_ollama_is_down(api_client, fake_embedder):
    fake_embedder.down = True
    r = api_client.get("/knowledge/search", params={"q": "tomato"})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"]


# --- database -----------------------------------------------------------------


def test_search_finds_relevant_chunks_with_traceability(client, indexed, fake_embedder):
    r = client.get("/knowledge/search", params={"q": "  tomato garden soil ", "source": indexed.name})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "tomato garden soil"
    assert body["source"] == indexed.name

    top = body["results"][0]
    assert top["path"] == "garden.md"
    assert top["source"] == indexed.name
    assert top["title"] == "Garden"
    assert top["start_line"] >= 1 and top["end_line"] >= top["start_line"]
    assert top["heading_path"][0] == "Garden"
    assert "tomato" in top["content"]
    scores = [hit["score"] for hit in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert body["results"][-1]["path"] in {"money.md", "code/db.md"}  # unrelated notes rank last

    # Queries use the query prefix; documents were indexed with the document prefix.
    assert fake_embedder.texts == ["search_query: tomato garden soil"]


def test_search_limit_and_source_filter(client, indexed):
    r = client.get("/knowledge/search", params={"q": "python database query", "limit": 1, "source": indexed.name})
    assert [h["path"] for h in r.json()["results"]] == ["code/db.md"]

    r = client.get("/knowledge/search", params={"q": "tomato", "source": "no-such-source"})
    assert r.status_code == 200
    assert r.json()["results"] == []
