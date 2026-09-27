"""The production embedding provider, backed by sentence-transformers.

The model is loaded lazily -- on the first embed call, not at import or
construction -- so importing this module never pulls torch into memory or
triggers a multi-hundred-megabyte download, and a process that only ever uses
the fake provider pays nothing. The real model is downloaded once into the
Hugging Face cache (a mounted volume in Docker) and reused thereafter.

On first load the provider validates that the model's output dimension matches
the dimension the schema expects. The embedding column is ``vector(768)``;
a model that produced a different width would generate rows Postgres rejects,
so this fails immediately and clearly at load time rather than at the first
insert, and names the mismatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class EmbeddingDimensionMismatchError(RuntimeError):
    """Raised when a model's output dimension does not match the schema."""


class SentenceTransformerProvider:
    """Real embeddings via a sentence-transformers model, loaded on first use."""

    def __init__(self, model_name: str, expected_dim: int, batch_size: int = 32) -> None:
        """Construct the provider without loading the model.

        Args:
            model_name: The sentence-transformers model id to load.
            expected_dim: The dimension the schema requires. The loaded model's
                dimension is checked against this the first time it is used.
            batch_size: Batch size passed to the encoder for document embedding.

        """
        self._model_name = model_name
        self._expected_dim = expected_dim
        self._batch_size = batch_size
        self._model: SentenceTransformer | None = None

    @property
    def model_name(self) -> str:
        """Return the model id."""
        return self._model_name

    @property
    def dim(self) -> int:
        """Return the expected (schema) dimension.

        Reports the schema dimension without forcing a model load, so callers
        that only need the width (e.g. to size a buffer) do not pay for the
        model. The real model's dimension is validated against this on load.
        """
        return self._expected_dim

    def _get_model(self) -> SentenceTransformer:
        """Load the model on first use and validate its dimension.

        Returns:
            The loaded model.

        Raises:
            EmbeddingDimensionMismatchError: If the model's output dimension
                does not equal the schema dimension.

        """
        if self._model is None:
            # Imported here, not at module top, so torch is pulled in only when
            # the real provider is actually used -- never in the fake path.
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(self._model_name, trust_remote_code=True)
            actual = model.get_sentence_embedding_dimension()
            if actual != self._expected_dim:
                raise EmbeddingDimensionMismatchError(
                    f"model '{self._model_name}' produces {actual}-dim vectors, "
                    f"but the schema requires {self._expected_dim}. The embedding "
                    f"column is vector({self._expected_dim}); use a model of that "
                    "width, or migrate the schema to the new dimension."
                )
            self._model = model
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents, normalising for cosine similarity.

        Args:
            texts: Texts to embed.

        Returns:
            One unit vector per text.

        """
        if not texts:
            return []
        model = self._get_model()
        vectors = model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query, normalised for cosine similarity.

        Args:
            text: The query text.

        Returns:
            A unit vector.

        """
        return self.embed_documents([text])[0]
