"""Semantic search business logic.

Runs inside request handling. Enforces ownership (a repo that is not the
caller's is simply not found), refuses to search a repository that has no
embeddings yet (409 rather than a misleading empty result), embeds the query
through the same provider the worker used, and returns ranked chunks. It knows
nothing about HTTP; the route maps its results and errors.
"""

import uuid

from devpilot.core.config import Settings
from devpilot.core.errors import NotFoundError
from devpilot.providers.embeddings.base import EmbeddingProvider
from devpilot.repositories.embedding_repo import EmbeddingRepository, SearchHit
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.services.exceptions import RepositoryNotIndexedError


class SearchService:
    """Owner-scoped semantic search over a repository's chunks."""

    def __init__(
        self,
        repositories: RepositoryRepository,
        embeddings: EmbeddingRepository,
        embedder: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        """Construct the service.

        Args:
            repositories: Repository data access, for the ownership check.
            embeddings: Embedding data access, for the vector search.
            embedder: The embedding provider, used to embed the query. The same
                provider family the worker embedded documents with, so query and
                document vectors live in the same space.
            settings: Supplies the default and maximum top_k.

        """
        self._repositories = repositories
        self._embeddings = embeddings
        self._embedder = embedder
        self._settings = settings

    def _resolve_top_k(self, requested: int | None) -> int:
        """Return the effective result count, applying default and cap.

        Args:
            requested: The client's requested top_k, or None.

        Returns:
            The default when unset, otherwise the request clamped to the
            configured maximum. Clamping (rather than rejecting) keeps an
            over-large request friendly and still bounds the work.

        """
        if requested is None:
            return self._settings.search_default_top_k
        return min(requested, self._settings.search_max_top_k)

    async def search(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID, query: str, top_k: int | None
    ) -> list[SearchHit]:
        """Search a repository's chunks for a query.

        Args:
            owner_id: The requesting user.
            repository_id: The repository to search.
            query: The query text.
            top_k: Requested result count, or None for the default.

        Returns:
            Ranked hits, most relevant first.

        Raises:
            NotFoundError: If the repository does not exist or is not the
                caller's (raised by the ownership check; indistinguishable).
            RepositoryNotIndexedError: If the repository has no embeddings yet.

        """
        # Ownership first: a cross-tenant id is a 404 before any work happens.
        # The owner-scoped repository query returns nothing for a repo that is
        # not the caller's, which is what makes "not found" and "not yours"
        # indistinguishable -- the same rule RepositoryService applies.
        repository = await self._repositories.get_for_owner(repository_id, owner_id)
        if repository is None:
            raise NotFoundError("Repository not found.")

        if await self._embeddings.count_for_repository(repository_id) == 0:
            raise RepositoryNotIndexedError

        query_vector = self._embedder.embed_query(query)
        return await self._embeddings.search(
            repository_id=repository_id,
            query_vector=query_vector,
            top_k=self._resolve_top_k(top_k),
        )
