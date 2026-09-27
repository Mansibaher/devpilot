# Retrieval evaluation

This harness measures DevPilot's semantic search quality against a set of
hand-labeled queries. It is **not** part of the automated test suite: it
requires the real embedding model (`jinaai/jina-embeddings-v2-base-code`), which
is a large download and needs `torch`. The automated tests use the deterministic
fake provider and never run this.

## Files

- `ground_truth.jsonl` — one labeled query per line:
  `{"query": "...", "relevant_paths": ["src/..."]}`. The relevant paths are
  hand judgments about which files answer each query.
- `run_eval.py` — indexes nothing itself; it evaluates an already-indexed
  repository and computes `recall@1`, `recall@5`, `recall@10`, and MRR from the
  ground truth. It refuses to emit a number if the repository has no embeddings.

## Running it (manual)

```bash
export DEVPILOT_EMBEDDING_PROVIDER=sentence_transformer
export DEVPILOT_DATABASE_URL=postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot
export DEVPILOT_JWT_SECRET=... # any 32+ char value

# Register + index the DevPilot repo itself (or any target) through the API and
# worker so chunks/embeddings exist, then:
python -m eval.run_eval --repository-id <repository-uuid>
```

## Metric honesty

No metric is stored in code or docs. The only numbers that exist are whatever
`run_eval.py` prints from a real run against real embeddings. As of this
milestone the harness has **NOT been run** — the real model has not been
downloaded or executed in the development environment — so there is no recall
figure to report yet.
