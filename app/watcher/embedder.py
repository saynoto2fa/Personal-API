"""Embeddings from a local Ollama server (nomic-embed-text by default)."""

import logging
import time
from collections.abc import Callable

import httpx

from app.models.knowledge import EMBED_DIM

log = logging.getLogger(__name__)

# nomic-embed-text is trained with task prefixes. Documents are indexed with DOC_PREFIX;
# search queries (Stage 4) must be embedded with QUERY_PREFIX or results get noticeably worse.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbeddingError(RuntimeError):
    """Embedding failed after retries (Ollama down, model missing, bad response)."""


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        batch_size: int = 16,
        retries: int = 4,
        backoff_seconds: float = 1.0,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        keep_alive: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive  # how long Ollama keeps the model loaded after a request, e.g. "1h"
        self.batch_size = batch_size
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def check(self) -> str | None:
        """None if Ollama is reachable and the model is pulled, otherwise a description of the problem."""
        try:
            r = self._client.get("/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            return f"Ollama is not reachable at {self.base_url} ({e.__class__.__name__}). Is it running?"
        names = {m.get("name", "") for m in r.json().get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            return f"Ollama model '{self.model}' is not pulled. Run: ollama pull {self.model}."
        return None

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(texts[i : i + self.batch_size]))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        delay = self.backoff_seconds
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                payload: dict = {"model": self.model, "input": batch}
                if self.keep_alive:
                    payload["keep_alive"] = self.keep_alive
                r = self._client.post("/api/embed", json=payload)
            except httpx.TransportError as e:  # connection refused, timeout, ...
                last_error = e
            else:
                if r.status_code == 404:
                    raise EmbeddingError(f"Ollama model '{self.model}' not found. Run: ollama pull {self.model}")
                if r.status_code < 500:
                    r.raise_for_status()  # other 4xx: a bug in the request, retrying won't help
                    return self._validate(r.json().get("embeddings"), len(batch))
                last_error = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            if attempt < self.retries:
                log.warning("Ollama embed failed (%s), retry %d/%d in %.0fs", last_error, attempt, self.retries - 1, delay)
                self._sleep(delay)
                delay *= 2
        raise EmbeddingError(f"Ollama at {self.base_url} failed after {self.retries} attempts: {last_error}")

    def _validate(self, vectors: object, expected: int) -> list[list[float]]:
        if not isinstance(vectors, list) or len(vectors) != expected:
            raise EmbeddingError(f"Ollama returned {len(vectors) if isinstance(vectors, list) else 'no'} vectors, expected {expected}")
        for v in vectors:
            if len(v) != EMBED_DIM:
                raise EmbeddingError(
                    f"Model '{self.model}' returns {len(v)}-dim vectors but the chunks table stores {EMBED_DIM}"
                )
        return vectors
