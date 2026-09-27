"""The embedding provider port.

A ``Protocol`` rather than an ABC so adapters need only match the shape, and so
the search service and indexing pipeline can be typed against the interface
without importing any concrete model. Two methods, because document and query
embedding are conceptually distinct (some models prepend different instructions
to each); for the models used here they behave the same, but keeping them
separate means a future asymmetric model needs no interface change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into fixed-dimension vectors.

    Implementations must return vectors of exactly ``dim`` floats and must be
    safe to call repeatedly within a process.
    """

    @property
    def model_name(self) -> str:
        """Return the identifier of the underlying model."""
        ...

    @property
    def dim(self) -> int:
        """Return the dimension of the vectors this provider produces."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents (chunk texts).

        Args:
            texts: The texts to embed.

        Returns:
            One vector per input text, each of length ``dim``.

        """
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query.

        Args:
            text: The query text.

        Returns:
            A single vector of length ``dim``.

        """
        ...
