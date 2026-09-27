"""A deterministic, dependency-free embedding provider for tests and CI.

Produces a stable unit-normalised vector from a hash of the input text: the
same text always yields the same vector, different texts yield different
vectors, and no model is loaded and no network is touched. That determinism is
what makes retrieval testable -- a query whose text matches a stored chunk
embeds to the same vector and therefore ranks that chunk first -- while keeping
the whole test suite offline, which the milestone requires.

This is not a good embedding model and is not meant to be. It exists so the
storage, indexing, and search machinery can be exercised without the real
model's weight and download.
"""

from __future__ import annotations

import hashlib
import math


class FakeEmbeddingProvider:
    """Deterministic hash-based embeddings of a fixed dimension."""

    def __init__(self, dim: int, model_name: str = "fake-embedding") -> None:
        """Construct the provider.

        Args:
            dim: The vector dimension to produce. Matches the configured
                embedding dimension so fake and real providers are
                interchangeable in the pipeline.
            model_name: A label stored alongside embeddings.

        """
        self._dim = dim
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        """Return the provider's model label."""
        return self._model_name

    @property
    def dim(self) -> int:
        """Return the vector dimension."""
        return self._dim

    def _vector(self, text: str) -> list[float]:
        """Return a deterministic unit vector for a string.

        Bytes from a repeated SHA-256 digest of the text seed the components,
        which are then L2-normalised so cosine distance behaves and identical
        text maps to an identical unit vector.
        """
        raw = bytearray()
        counter = 0
        while len(raw) < self._dim * 2:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            raw.extend(digest)
            counter += 1
        # Two bytes per component, centred around zero.
        components = [(raw[2 * i] << 8 | raw[2 * i + 1]) / 65535.0 - 0.5 for i in range(self._dim)]
        norm = math.sqrt(sum(c * c for c in components)) or 1.0
        return [c / norm for c in components]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts deterministically.

        Args:
            texts: Texts to embed.

        Returns:
            One unit vector per text.

        """
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query deterministically.

        Args:
            text: The query text.

        Returns:
            A unit vector. Identical to what ``embed_documents`` would produce
            for the same string, so a query equal to a chunk retrieves it.

        """
        return self._vector(text)
