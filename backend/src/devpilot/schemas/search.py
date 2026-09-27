"""Request and response schemas for semantic search.

The request carries a query and an optional result count; the response carries
ranked chunks with the citation fields a consumer needs to locate the source:
path, line span, content, and a similarity score. ``top_k`` bounds are enforced
here as a first line of validation, and clamped again in the service against the
configured maximum.
"""

import uuid

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """A semantic search over a repository's indexed chunks.

    Attributes:
        query: The natural-language or code query. Bounded to keep request size
            and embedding cost in check.
        top_k: How many results to return. Optional; the service applies the
            configured default when omitted and clamps to the configured
            maximum.

    """

    query: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=200)


class SearchResult(BaseModel):
    """One ranked chunk returned by search.

    Attributes:
        chunk_id: The chunk's id.
        path: Repository-relative file path.
        language: Detected language, or null.
        start_line: First line of the chunk, 1-based inclusive.
        end_line: Last line of the chunk, 1-based inclusive.
        content: The chunk's source text.
        score: Cosine similarity in [0, 1]; higher is more relevant.

    """

    chunk_id: uuid.UUID
    path: str
    language: str | None
    start_line: int
    end_line: int
    content: str
    score: float


class SearchResponse(BaseModel):
    """The result set for a search request.

    Attributes:
        query: Echo of the query that was run.
        results: Ranked chunks, most relevant first.

    """

    query: str
    results: list[SearchResult]
