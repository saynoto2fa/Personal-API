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
    "ops/deploy.md": "# Deploy\n\nRun kubectl rollout restart after the database migration.",
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


def test_semantic_mode_returns_503_when_ollama_is_down(api_client, fake_embedder):
    fake_embedder.down = True
    r = api_client.get("/knowledge/search", params={"q": "tomato", "mode": "semantic"})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"]
    assert api_client.get("/knowledge/search", params={"q": "x", "mode": "fuzzy"}).status_code == 422


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
    assert body["results"][-1]["path"] in {"money.md", "code/db.md", "ops/deploy.md"}  # unrelated notes rank last
    assert body["mode"] == "hybrid" and body["warnings"] == []

    # Queries use the query prefix; documents were indexed with the document prefix.
    assert fake_embedder.texts == ["search_query: tomato garden soil"]


def test_search_limit_and_source_filter(client, indexed):
    r = client.get("/knowledge/search", params={"q": "python database query", "limit": 1, "source": indexed.name})
    assert [h["path"] for h in r.json()["results"]] == ["code/db.md"]

    r = client.get("/knowledge/search", params={"q": "tomato", "source": "no-such-source"})
    assert r.status_code == 200
    assert r.json()["results"] == []


def _paths(client, source, q, mode, limit=10):
    r = client.get("/knowledge/search", params={"q": q, "mode": mode, "source": source.name, "limit": limit})
    assert r.status_code == 200, r.text
    return [h["path"] for h in r.json()["results"]], r.json()


def test_exact_term_found_by_keyword_and_hybrid(client, indexed):
    # "kubectl" means nothing to the (fake) embedding model, like an error code or identifier would.
    paths, body = _paths(client, indexed, "kubectl", "keyword")
    assert paths == ["ops/deploy.md"]
    hit = body["results"][0]
    assert hit["keyword_score"] > 0 and hit["semantic_score"] is None and hit["score"] == hit["keyword_score"]

    paths, body = _paths(client, indexed, "kubectl", "hybrid")
    assert paths[0] == "ops/deploy.md"
    assert body["results"][0]["keyword_score"] > 0 and body["results"][0]["semantic_score"] is not None
    assert all(h["keyword_score"] is None for h in body["results"][1:])  # the rest only came from semantic


def test_conceptual_query_found_by_semantic_and_hybrid(client, indexed):
    # No word of "growing vegetables" appears in garden.md, so full-text search can't find it.
    assert _paths(client, indexed, "growing vegetables", "keyword")[0] == []
    assert _paths(client, indexed, "growing vegetables", "semantic")[0][0] == "garden.md"
    paths, body = _paths(client, indexed, "growing vegetables", "hybrid")
    assert paths[0] == "garden.md"
    assert body["results"][0]["keyword_score"] is None


def test_hybrid_ranks_chunks_both_methods_agree_on_first(client, indexed):
    # Semantic alone ranks "python database" notes by vocabulary overlap; keyword adds exact words.
    paths, body = _paths(client, indexed, "database migration", "hybrid")
    assert paths[0] == "ops/deploy.md"  # the only chunk with both exact words, and semantically close
    scores = [h["score"] for h in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_heading_words_weigh_more_in_keyword_ranking(client, indexed):
    r = client.get("/knowledge/search", params={"q": "soil", "mode": "keyword", "source": indexed.name})
    hits = r.json()["results"]
    assert [h["heading_path"][-1] for h in hits] == ["Soil", "Garden"]  # "## Soil" chunk outranks a passing mention


def test_hybrid_falls_back_to_keyword_when_ollama_is_down(client, indexed, fake_embedder):
    fake_embedder.down = True
    paths, body = _paths(client, indexed, "kubectl", "hybrid")
    assert paths == ["ops/deploy.md"]
    assert body["warnings"] and "keyword matches only" in body["warnings"][0]
    assert _paths(client, indexed, "kubectl", "keyword")[1]["warnings"] == []  # keyword mode never needs Ollama
    assert client.get("/knowledge/search", params={"q": "kubectl", "mode": "semantic"}).status_code == 503


def test_fusion_scores_and_tie_breaks():
    from types import SimpleNamespace as Row

    from app.search import RRF_K, fuse

    near = Row(id="near", semantic=0.53, keyword=None)  # nearest embedding, no exact words
    exact = Row(id="exact", semantic=0.43, keyword=0.4)  # only found by keyword
    both = Row(id="both", semantic=0.50, keyword=0.1)
    order = fuse([[near, both], [exact, both]])
    assert [r.id for r, _ in order] == ["both", "exact", "near"]  # in both lists beats #1 of one list
    scores = dict((r.id, s) for r, s in order)
    assert scores["both"] == 2 / (RRF_K + 2)
    assert scores["exact"] == scores["near"] == 1 / (RRF_K + 1)  # the tie goes to the exact-word match
