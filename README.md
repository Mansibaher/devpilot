# DevPilot AI

Ask questions about a GitHub repository and get answers grounded in its actual source, with citations back to the exact files and line ranges the answer came from.

**Status:** M1 — scaffold, configuration, migrations, health probes, CI. No product functionality yet.

## Scope

v1 indexes **public** GitHub repositories only. Private repositories require storing user access tokens, which requires encryption at rest and key management — a separate project, and deliberately out of scope rather than half-built.

Not in v1: PR review, bug detection, doc generation, coding agent, multi-user workspaces. Every table that holds user data will carry `owner_id` from the migration that creates it, so workspaces are additive rather than a rewrite.

## Architecture

Clean layering, one direction only:

```
api/  ->  services/  ->  repositories/  ->  Postgres
             |
             +->  providers/   (LLM, embeddings, VCS — swappable behind ports)
```

- **Routes** parse, authenticate, and delegate. No business logic.
- **Services** hold the logic. No HTTP, no SQL. This is why `IndexingService` runs unchanged inside the worker process.
- **Repositories** are the only layer that emits SQL.
- **Providers** are ports with adapters, so OpenAI can be swapped for HuggingFace, or for a fake in tests, by changing configuration.

### Decisions worth knowing

| Decision | Why |
| --- | --- |
| Postgres as the job queue (`FOR UPDATE SKIP LOCKED`) | Indexing takes minutes and cannot live in a request. Durable and restartable without adding Redis. Celery slots in behind the same port later. |
| Separate worker process | An OOM while embedding a large repo must not take down the API. |
| HNSW over IVFFlat | IVFFlat needs training data and `lists` retuning as the corpus grows. HNSW builds incrementally — right for continuously arriving repos. |
| `chunk_embeddings` split from `chunks` | Re-embed with a new model without touching chunk text; keep the ANN index on a narrow table. |
| Hybrid retrieval (vector + full-text, RRF) | Embeddings miss exact identifiers. Users search for `RATE_LIMIT_BURST`, not "code about rate limits". |
| Short access JWT + rotating refresh token | Without server-side refresh state, logout does nothing. |
| Discard the clone after indexing | Persisted working trees grow without bound and buy nothing. |

## Local setup

### Prerequisites

| | Version | Check |
| --- | --- | --- |
| Docker Engine + Compose v2 | 24+ | `docker compose version` |
| Python (only for running tests outside Docker) | 3.12 | `python3 --version` |
| GNU Make (optional; every target is a one-line shell command) | any | `make --version` |

Ports **8000** (API) and **5432** (Postgres) must be free.

### Run it

```bash
git clone <your-fork-url> devpilot && cd devpilot
cp .env.example .env          # defaults work as-is for local development
make up                       # == docker compose up --build -d --wait
```

`make up` builds the image, starts Postgres, waits for it to pass its
healthcheck, runs `alembic upgrade head` as a one-shot job, and only then
starts the API. It returns when the API is healthy.

```bash
curl localhost:8000/health    # {"status":"ok","environment":"local"}
curl localhost:8000/readyz    # {"status":"ready","checks":{"database":true}}
open http://localhost:8000/docs
```

Tear down, keeping data: `make down`. Discard the database volume too:
`docker compose down -v`.

### Run the tests

Two supported ways. Both run the identical suite.

**In Docker** (nothing to install; this is the reproducible one):

```bash
make up                                # stack must be running
docker compose exec api pytest         # == make test-docker
```

From cold, without a running stack — starts Postgres and migrations, runs the
suite in a throwaway container, removes it:

```bash
docker compose run --rm api pytest     # == make test-docker-cold
```

Useful variations:

```bash
docker compose exec api pytest -m "not integration"   # skip tests needing Postgres
docker compose exec api pytest -v                     # per-test names
docker compose exec api pytest --cov=devpilot --cov-report=term-missing
docker compose exec api pytest tests/unit/test_health.py::test_liveness_reports_ok
```

Test dependencies live in the **`dev`** image stage, not `runtime`. Compose
builds `dev`; production and CI build `runtime`, which contains no pytest and
no test code. After changing dependencies or the Dockerfile, rebuild:

```bash
make rebuild        # docker compose build --no-cache api
make up
```

**On the host** (faster loop, needs local Python 3.12):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

make test              # unit tests; no database required
make test-integration  # + tests needing live Postgres (migrates first)
make cov               # coverage report
make check             # lint + format + types + tests, the same gate as CI
```

The integration suite reads `DEVPILOT_DATABASE_URL` and **skips** when nothing
is listening, so `make test` works on a laptop with nothing running. To point
it at the Compose database:

```bash
export DEVPILOT_DATABASE_URL=postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot
make test-integration
```

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness. Touches no dependency. Always 200 while the process runs. |
| GET | `/healthz` | Undocumented alias of `/health`, for orchestrators that default to this path. |
| GET | `/readyz` | Readiness. 200 when Postgres is reachable, 503 otherwise. |
| GET | `/docs` | OpenAPI UI. Disabled when `DEVPILOT_ENVIRONMENT=production`. |

### Configuration

Every setting is an environment variable prefixed `DEVPILOT_`, parsed and
validated once in `core/config.py`; no other module reads `os.environ`. See
`.env.example` for the full list. There are no secrets in the repository and
no defaults for anything that would be a secret in production.

### Troubleshooting

| Symptom | Cause |
| --- | --- |
| `ValidationError: database_url Field required` | `DEVPILOT_DATABASE_URL` is unset. Copy `.env.example` to `.env`, or export it. |
| `/readyz` returns 503 | Postgres is not reachable. `docker compose ps db`, then `docker compose logs db`. |
| `port is already allocated` | Something else holds 8000 or 5432. Change the host-side port in `docker-compose.yml`. |
| `make test-integration` reports skips | No database at `DEVPILOT_DATABASE_URL`. Run `make up` first and export the DSN above. |
| `exec: "pytest": executable file not found in $PATH` | The `api` container was built from the `runtime` target, which deliberately has no test dependencies. Run `make rebuild && make up` to rebuild it from the `dev` target. |
| `ModuleNotFoundError: No module named 'tests'` | Running bare `pytest` from outside the project root. `pytest` only picks up `pythonpath` from `pyproject.toml` when run with the repo root as the working directory. |

## Roadmap

| | Milestone |
| --- | --- |
| M1 | Scaffold, config, pgvector migration, health, CI ✅ |
| M2 | Users, auth, refresh rotation |
| M3 | Repository import, `index_jobs`, worker loop |
| M4 | Clone, walk, chunk, persist |
| M5 | Embeddings, HNSW, vector search |
| M6 | Hybrid retrieval + RRF |
| M7 | Tree-sitter AST chunking |
| M8 | Chat, LLM provider port, SSE streaming, citations |
| M9 | React frontend |
| M10 | Test hardening, docs |

## Testing notes

`/health` and `/readyz` answer different questions, and conflating them causes
outages:

- **Liveness** asks *is this process alive?* It touches no dependency. If it
  failed on a database blip, an orchestrator would turn that blip into a
  restart storm.
- **Readiness** asks *should this process get traffic?* It does check
  dependencies. Failing it removes the instance from the load balancer without
  killing it — the correct response to a transient outage.

Both behaviours are covered by tests, because they are easy to regress and
expensive to get wrong.

Unit tests stub the database; they are about HTTP behaviour. Integration tests
use a real Postgres, because a mock cannot tell you that a migration failed to
apply or that pgvector is missing. CI runs both against a `pgvector/pgvector:pg16`
service, applies migrations, and verifies they are reversible.

The image is built in three stages. `builder` carries compilers and build
metadata. `runtime` carries neither and is what deploys — no pytest, no test
code, 29 packages. `dev` layers the test dependencies and the suite on top of
`runtime` (46 packages) and is what Compose builds locally. CI builds `runtime`
separately, which is what keeps that separation honest: if a test dependency
ever leaks into production, that job is where it surfaces.
