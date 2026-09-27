"""Selects an embedding provider from settings.

One function, so the choice between the real model and the fake is made in
exactly one place from configuration. Tests and CI set the provider to 'fake';
local and production default to the real sentence-transformers model. The
returned object satisfies the ``EmbeddingProvider`` protocol either way, so
callers never branch on which one they got.
"""

from __future__ import annotations

from devpilot.core.config import Settings
from devpilot.providers.embeddings.base import EmbeddingProvider
from devpilot.providers.embeddings.fake import FakeEmbeddingProvider
from devpilot.providers.embeddings.sentence_transformer import SentenceTransformerProvider


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Return the embedding provider named by settings.

    Args:
        settings: Supplies the provider name, model id, dimension, and batch
            size. ``embedding_dim`` has already been validated against the
            schema dimension by the settings validator.

    Returns:
        A provider satisfying :class:`EmbeddingProvider`. The real provider is
        constructed but not loaded; its model loads lazily on first use.

    """
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider(dim=settings.embedding_dim)
    return SentenceTransformerProvider(
        model_name=settings.embedding_model,
        expected_dim=settings.embedding_dim,
        batch_size=settings.embedding_batch_size,
    )
