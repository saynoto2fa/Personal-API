"""Test doubles shared by several test modules."""

import re

from app.models.knowledge import EMBED_DIM
from app.watcher.embedder import EmbeddingError

VOCAB = ["tomato", "garden", "soil", "python", "database", "query", "recipe", "pasta", "budget", "invoice"]
# Words the fake "understands" as meaning a VOCAB word, so a query can match by meaning without
# sharing a single word with the document (what full-text search can't do).
SYNONYMS = {"vegetables": "tomato", "vegetable": "tomato", "growing": "garden", "coding": "python", "money": "budget"}


class KeywordEmbedder:
    """Bag-of-keywords vectors: texts sharing words (or SYNONYMS) from VOCAB have high cosine similarity.

    Words outside VOCAB, like identifiers or error codes, are invisible to it, as rare exact terms
    tend to be to a real embedding model. Records every text it embeds, so tests can check prefixes.
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
            words = [SYNONYMS.get(w, w) for w in re.findall(r"[a-z]+", t.lower())]
            v = [0.0] * EMBED_DIM
            v[0] = 0.05  # small shared component so no vector is all zeros
            for i, term in enumerate(VOCAB, start=1):
                v[i] = float(words.count(term))
            vectors.append(v)
        return vectors
