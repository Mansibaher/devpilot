"""Retrieval evaluation harness for DevPilot semantic search.

Measures how well search surfaces the right source files for a set of
hand-labeled queries. It is deliberately NOT part of the automated test suite:
it uses the real embedding model, which is a large download and needs torch, so
it is run manually and its output is the only place a retrieval number should
ever come from. No metric is hard-coded anywhere; the numbers below are computed
from the ground truth in ``ground_truth.jsonl`` against a freshly indexed
corpus, or the script refuses to print them.

How it works:
  1. Index a target repository (the "corpus") with the REAL provider, so chunks
     and embeddings exist in the database.
  2. For each labeled query, run the real search path and collect the ranked
     file paths (deduplicated, preserving order).
  3. Compare against the query's ``relevant_paths``: a query is a hit@k if any
     relevant path appears in the top k results.
  4. Report recall@1, recall@5, recall@10, and mean reciprocal rank.

Ground-truth format (one JSON object per line):
    {"query": "...", "relevant_paths": ["src/...", ...]}

Usage (documented, not run in CI):
    # 1. Point DEVPILOT at a database and the REAL provider.
    export DEVPILOT_EMBEDDING_PROVIDER=sentence_transformer
    export DEVPILOT_DATABASE_URL=postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot
    # 2. Index the corpus repository via the normal API/worker flow first, then:
    python -m eval.run_eval --repository-id <uuid>

The script never fabricates results: if it cannot embed (model missing) or the
repository has no embeddings, it exits non-zero with a clear message rather than
emitting a number.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from devpilot.core.config import get_settings
from devpilot.db.session import Database
from devpilot.providers.embeddings.registry import build_embedding_provider
from devpilot.repositories.embedding_repo import EmbeddingRepository

_GROUND_TRUTH = Path(__file__).parent / "ground_truth.jsonl"
_K_VALUES = (1, 5, 10)


@dataclass
class LabeledQuery:
    """A query and the set of file paths that genuinely answer it."""

    query: str
    relevant_paths: set[str]


def load_ground_truth(path: Path = _GROUND_TRUTH) -> list[LabeledQuery]:
    """Load hand-labeled queries from a JSONL file.

    Args:
        path: The ground-truth file.

    Returns:
        The labeled queries.

    """
    queries: list[LabeledQuery] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        queries.append(LabeledQuery(query=obj["query"], relevant_paths=set(obj["relevant_paths"])))
    return queries


def _ranked_paths(hit_paths: list[str]) -> list[str]:
    """Deduplicate result paths, preserving first-seen order (best rank)."""
    seen: list[str] = []
    for p in hit_paths:
        if p not in seen:
            seen.append(p)
    return seen


@dataclass
class EvalResult:
    """Computed metrics for a run. All values are measured, never assumed."""

    n_queries: int
    recall_at: dict[int, float]
    mrr: float


async def evaluate(repository_id: uuid.UUID) -> EvalResult:
    """Run every labeled query against real search and compute metrics.

    Args:
        repository_id: A repository already indexed with the real provider.

    Returns:
        The measured metrics.

    Raises:
        SystemExit: If the repository has no embeddings, so no fake number is
            ever produced.

    """
    settings = get_settings()
    database = Database(settings)
    embedder = build_embedding_provider(settings)
    queries = load_ground_truth()

    max_k = max(_K_VALUES)
    hits_at: dict[int, int] = dict.fromkeys(_K_VALUES, 0)
    reciprocal_ranks = 0.0

    try:
        async with database.session() as session:
            repo = EmbeddingRepository(session)
            if await repo.count_for_repository(repository_id) == 0:
                raise SystemExit(
                    "Repository has no embeddings; index it with the real provider "
                    "before evaluating. Refusing to emit a metric."
                )
            for labeled in queries:
                qvec = embedder.embed_query(labeled.query)
                hits = await repo.search(
                    repository_id=repository_id, query_vector=qvec, top_k=max_k
                )
                ranked = _ranked_paths([h.path for h in hits])
                first_rank = next(
                    (i + 1 for i, p in enumerate(ranked) if p in labeled.relevant_paths),
                    None,
                )
                if first_rank is not None:
                    reciprocal_ranks += 1.0 / first_rank
                    for k in _K_VALUES:
                        if first_rank <= k:
                            hits_at[k] += 1
    finally:
        await database.dispose()

    n = len(queries)
    return EvalResult(
        n_queries=n,
        recall_at={k: hits_at[k] / n for k in _K_VALUES},
        mrr=reciprocal_ranks / n,
    )


def main() -> None:
    """Parse arguments, run the evaluation, and print measured metrics."""
    parser = argparse.ArgumentParser(description="DevPilot retrieval evaluation")
    parser.add_argument(
        "--repository-id",
        required=True,
        type=uuid.UUID,
        help="UUID of a repository already indexed with the real embedding provider",
    )
    args = parser.parse_args()

    result = asyncio.run(evaluate(args.repository_id))

    print(f"Queries evaluated: {result.n_queries}")
    for k in _K_VALUES:
        print(f"recall@{k}: {result.recall_at[k]:.3f}")
    print(f"MRR: {result.mrr:.3f}")

    if result.n_queries == 0:
        sys.exit("No queries in ground truth; nothing measured.")


if __name__ == "__main__":
    main()
