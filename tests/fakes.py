"""Test doubles shared by several test modules."""

import re

from app.models.knowledge import EMBED_DIM
from app.watcher.embedder import EmbeddingError

VOCAB = ["tomato", "garden", "soil", "python", "database", "query", "recipe", "pasta", "budget", "invoice"]


class KeywordEmbedder:
    """Bag-of-keywords vectors: texts sharing words from VOCAB have high cosine similarity.

    Records every text it embeds, so tests can check the prefixes used.
    """

    model = "fake-keyword"

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.down = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.down:
            raise EmbeddingError("Ollama at http://fake failed after 2 attempts: connection refused")
        self.texts.extend(texts)
        vectors = []
        for t in texts:
            words = re.findall(r"[a-z]+", t.lower())
            v = [0.0] * EMBED_DIM
            v[0] = 0.05  # small shared component so no vector is all zeros
            for i, term in enumerate(VOCAB, start=1):
                v[i] = float(words.count(term))
            vectors.append(v)
        return vectors
