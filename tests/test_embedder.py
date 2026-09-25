import json

import httpx
import pytest

from app.models.knowledge import EMBED_DIM
from app.watcher.embedder import EmbeddingError, OllamaEmbedder


def _embedder(handler, **kw):
    sleeps: list[float] = []
    e = OllamaEmbedder("http://ollama.test", "nomic-embed-text", transport=httpx.MockTransport(handler), sleep=sleeps.append, **kw)
    return e, sleeps


def _ok(request: httpx.Request) -> httpx.Response:
    n = len(json.loads(request.content)["input"])
    return httpx.Response(200, json={"embeddings": [[0.1] * EMBED_DIM] * n})


def test_batches_requests():
    sizes = []

    def handler(request):
        sizes.append(len(json.loads(request.content)["input"]))
        return _ok(request)

    e, _ = _embedder(handler, batch_size=4)
    vectors = e.embed([f"t{i}" for i in range(10)])
    assert len(vectors) == 10
    assert sizes == [4, 4, 2]


def test_keep_alive_is_sent_only_when_set():
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return _ok(request)

    _embedder(handler)[0].embed(["a"])
    _embedder(handler, keep_alive="1h")[0].embed(["a"])
    assert "keep_alive" not in payloads[0]
    assert payloads[1]["keep_alive"] == "1h"


def test_retries_connection_errors_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("refused")
        return _ok(request)

    e, sleeps = _embedder(handler, retries=4, backoff_seconds=1)
    assert len(e.embed(["a"])) == 1
    assert sleeps == [1, 2]  # exponential backoff


def test_gives_up_after_retries():
    def handler(request):
        return httpx.Response(503)

    e, sleeps = _embedder(handler, retries=3)
    with pytest.raises(EmbeddingError, match="after 3 attempts"):
        e.embed(["a"])
    assert len(sleeps) == 2


def test_missing_model_fails_fast():
    e, sleeps = _embedder(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(EmbeddingError, match="ollama pull"):
        e.embed(["a"])
    assert sleeps == []


def test_wrong_dimension_is_rejected():
    e, _ = _embedder(lambda r: httpx.Response(200, json={"embeddings": [[0.1] * 384]}))
    with pytest.raises(EmbeddingError, match="384-dim"):
        e.embed(["a"])


def test_check_reports_problems():
    tags = {"models": [{"name": "nomic-embed-text:latest"}]}
    e, _ = _embedder(lambda r: httpx.Response(200, json=tags))
    assert e.check() is None

    e, _ = _embedder(lambda r: httpx.Response(200, json={"models": []}))
    assert "ollama pull nomic-embed-text" in e.check()

    def down(request):
        raise httpx.ConnectError("refused")

    e, _ = _embedder(down)
    assert "not reachable" in e.check()
