"""Unit tests for the embedding providers and registry.

Only the fake provider is exercised here -- no model download, no network. The
real provider is validated for construction and lazy-loading behaviour without
ever loading a model.
"""

import pytest

from devpilot.core.config import Settings
from devpilot.providers.embeddings.base import EmbeddingProvider
from devpilot.providers.embeddings.fake import FakeEmbeddingProvider
from devpilot.providers.embeddings.registry import build_embedding_provider
from devpilot.providers.embeddings.sentence_transformer import SentenceTransformerProvider


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "ci",
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/devpilot_test",
        "jwt_secret": "unit-test-secret-value-at-least-32-characters",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestFakeProvider:
    def test_dimension_is_respected(self) -> None:
        p = FakeEmbeddingProvider(dim=768)
        v = p.embed_query("anything")
        assert len(v) == 768

    def test_same_text_same_vector(self) -> None:
        p = FakeEmbeddingProvider(dim=64)
        assert p.embed_query("hello") == p.embed_query("hello")

    def test_different_text_different_vector(self) -> None:
        p = FakeEmbeddingProvider(dim=64)
        assert p.embed_query("hello") != p.embed_query("goodbye")

    def test_query_matches_document_for_same_text(self) -> None:
        # The property that makes retrieval testable: a query equal to a chunk
        # embeds identically, so it will rank that chunk first.
        p = FakeEmbeddingProvider(dim=64)
        [doc] = p.embed_documents(["def foo(): pass"])
        assert p.embed_query("def foo(): pass") == doc

    def test_vectors_are_unit_normalised(self) -> None:
        p = FakeEmbeddingProvider(dim=128)
        v = p.embed_query("normalise me")
        magnitude = sum(x * x for x in v) ** 0.5
        assert magnitude == pytest.approx(1.0, abs=1e-6)

    def test_empty_batch(self) -> None:
        assert FakeEmbeddingProvider(dim=8).embed_documents([]) == []

    def test_satisfies_protocol(self) -> None:
        assert isinstance(FakeEmbeddingProvider(dim=8), EmbeddingProvider)


class TestRegistry:
    def test_fake_selected_by_settings(self) -> None:
        provider = build_embedding_provider(_settings(embedding_provider="fake"))
        assert isinstance(provider, FakeEmbeddingProvider)
        assert provider.dim == 768

    def test_real_selected_by_settings_but_not_loaded(self) -> None:
        # The real provider must be constructible without loading the model, so
        # that building it (e.g. via DI at startup) never triggers a download.
        provider = build_embedding_provider(_settings(embedding_provider="sentence_transformer"))
        assert isinstance(provider, SentenceTransformerProvider)
        assert provider.dim == 768  # reported without a model load
        assert provider.model_name == "jinaai/jina-embeddings-v2-base-code"


class TestRealProviderLazy:
    def test_dim_property_does_not_load_model(self) -> None:
        # Accessing dim must not touch sentence-transformers at all.
        p = SentenceTransformerProvider("some/model", expected_dim=768)
        assert p.dim == 768
        assert p._model is None  # still unloaded
