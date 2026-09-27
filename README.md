# DevPilot AI

Find relevant source code by asking questions in everyday language. DevPilot indexes public GitHub repositories and returns ranked code excerpts with filenames, line numbers, and links to the indexed commit.

**Status:** M4 — adds code chunking, embeddings (jina-embeddings-v2-base-code), pgvector storage, and owner-scoped semantic search. No LLM answers yet: search returns ranked source chunks with citations.


## See it in action

[Watch the recorded demo](devpilot-demo.webm) — a real search in the local browser workspace.

![DevPilot searching BrainTumorClassifier for its dataset split](docs/assets/devpilot-workspace.png)

**Example:** “Where is the dataset split into training and validation data?” retrieves the relevant code in `src/utils.py` from BrainTumorClassifier. DevPilot searches that project's source; it does not run the classifier.

**Availability:** locally runnable portfolio project. No public live demo is deployed yet. `localhost:8000` works only on the computer running the services.

### What you can do today

- Create an account and sign in through the browser workspace.
- Import public GitHub repositories and index them in the background.
- Search code by meaning using the Jina code embedding model and pgvector.
- Inspect, expand, and copy matching excerpts; follow commit-specific GitHub citations.
- Reindex a repository after its source changes.

### How it works

```mermaid
flowchart LR
    G[Public GitHub repository] --> W[Background indexing worker]
    W --> C[Code chunks and embeddings]
    C --> D[(PostgreSQL + pgvector)]
    U[Browser question] --> A[FastAPI and query embedding]
    A --> D
    D --> R[Ranked source excerpts and citations]
```

**Stack:** Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, pgvector, Sentence Transformers, vanilla JavaScript/HTML/CSS, and Docker Compose.

**Current limits:** public repositories only; manual reindexing; source retrieval rather than generated answers; no published retrieval accuracy benchmark. Similarity is a ranking signal, not a probability. Hybrid search and conversational answers are future work.

See [the portfolio walkthrough](docs/portfolio.md) and [the public demo deployment plan](docs/hosting-plan.md).

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
- **Providers** isolate external integrations. The embedding provider has a real Sentence Transformers adapter and a deterministic fake for tests; LLM answer generation is planned.

### Implemented decisions and planned extensions

| Decision | Why |
| --- | --- |
| Postgres as the job queue (`FOR UPDATE SKIP LOCKED`) | Indexing takes minutes and cannot live in a request. Durable and restartable without adding Redis. Celery slots in behind the same port later. |
| Separate worker process | An OOM while embedding a large repo must not take down the API. |
| HNSW over IVFFlat | IVFFlat needs training data and `lists` retuning as the corpus grows. HNSW builds incrementally — right for continuously arriving repos. |
| `chunk_embeddings` split from `chunks` | Re-embed with a new model without touching chunk text; keep the ANN index on a narrow table. |
| Planned: hybrid retrieval (vector + full-text, RRF) | Embeddings miss exact identifiers. Users search for `RATE_LIMIT_BURST`, not "code about rate limits". |
| Short access JWT; refresh tokens planned | Current access tokens expire after 15 minutes. Browser sign-out clears the local token but does not revoke an already issued token. |
| Discard the clone after indexing | Persisted working trees grow without bound and buy nothing. |

## Local setup

### Prerequisites

| | Version | Check |
| --- | --- | --- |
| Docker Engine + Compose v2 | 24+ | `docker compose version` |
| Python (only for running tests outside Docker) | 3.12 | `python3 --version` |
| GNU Make (optional; every target is a one-line shell command) | any | `make --version` |

Ports **8000** (API) and **5432** (Postgres) must be free.

### Run it on Windows (PowerShell)

From your cloned DevPilot folder:

```powershell
Copy-Item .env.example .env  # First setup only; preserve an existing .env
docker compose up --build -d --wait
Start-Process http://localhost:8000/
```

Create an account in the browser, import a public GitHub repository, index it, and ask a question. The first index/search may take longer while the embedding model downloads and loads. Keep Docker running during use.

### Run it on macOS/Linux

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
| POST | `/api/v1/auth/register` | Create an account. `{email, password}` → `201` user (no hash). `409` on duplicate, `422` on invalid email / weak password. |
| POST | `/api/v1/auth/login` | Exchange credentials for a token. `{email, password}` → `200 {access_token, token_type}`. `401` on any failure, identical for unknown email and wrong password. |
| GET | `/api/v1/auth/me` | Return the authenticated user. Requires `Authorization: Bearer <jwt>`. `401` for missing, malformed, or expired tokens. |
| POST | `/api/v1/repositories` | Register a GitHub repo. `{url}` → `201`. `422` invalid/non-GitHub URL, `409` already registered by you. |
| GET | `/api/v1/repositories` | List your repositories, newest first. Owner-scoped. |
| GET | `/api/v1/repositories/{id}` | Fetch one of your repositories, including index status. `404` if absent **or** owned by someone else. |
| POST | `/api/v1/repositories/{id}/index` | Queue indexing. `202` with the job. Idempotent: a queued/running job is returned rather than duplicated. |
| GET | `/api/v1/repositories/{id}/jobs/{job_id}` | Poll a job's status and progress. |
| POST | `/api/v1/repositories/{id}/search` | Semantic search over indexed code. `{query, top_k?}` → `200` ranked chunks with path, line span, content, score. `409` if not indexed yet, `404` if not yours. |
| GET | `/docs` | OpenAPI UI. Disabled when `DEVPILOT_ENVIRONMENT=production`. |

### Configuration

Every setting is an environment variable prefixed `DEVPILOT_`, parsed and
validated once in `core/config.py`; no other module reads `os.environ`. See
`.env.example` for the full list. There are no secrets in the repository and
no defaults for anything that would be a secret in production.

`DEVPILOT_JWT_SECRET` is **required and has no default** — the app refuses to
start without it, and a value under 32 characters is rejected at boot rather
than in production. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Compose reads it from your `.env`; it is never hardcoded in `docker-compose.yml`.

### Authentication

Passwords are hashed with **Argon2id** (`argon2-cffi`) and never stored or
logged in plaintext. The password policy is passphrase-friendly: 12–128
characters, spaces and Unicode allowed, no composition rules. Access tokens are
**HS256 JWTs** carrying `sub`, `iat`, and `exp`, valid for 15 minutes; token
decoding pins the algorithm explicitly, so `alg: none` and algorithm-confusion
attacks are rejected. Login returns one generic `401` for both unknown email
and wrong password, and does equal work in both cases so response timing cannot
be used to enumerate accounts. Refresh tokens, OAuth, roles, email
verification, and password reset are deliberately out of scope at this
milestone.

### Repository indexing

Registering a repository validates the URL and stores its metadata; it does not
clone. Cloning happens asynchronously in a **separate worker process**
(`python -m devpilot.worker`, same image as the API) that pulls work from a
durable queue.

The queue is a Postgres table, not Redis or Celery. A worker claims the oldest
queued job with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of workers
can run concurrently and never claim the same job. Each running job renews a
lease via a heartbeat; if a worker dies, its lease expires and the job is
reclaimed, so the queue survives a crash. Scale workers with
`docker compose up --scale worker=N`.

Jobs move through `queued → running → succeeded | failed`, with `attempts`
bounded by `max_attempts`. Re-indexing is idempotent: if the cloned HEAD already
matches the last indexed commit, the scan is skipped entirely.

**Cloning is hardened.** Only `https://github.com/owner/repo` URLs are accepted,
validated with `urllib.parse` (not a regex) before anything touches the network;
other schemes, other hosts, embedded credentials, ports, and path traversal are
rejected. git runs with `shell=False` and an argument list, a scrubbed
environment, `protocol.allowed=https`, `GIT_TERMINAL_PROMPT=0`, and a timeout.
Clones are shallow and blobless into an isolated temp dir that is always removed.

**Discovery filters** exclude dependency and build directories, secrets and
credential files, large generated lockfiles, binaries (null-byte sniff), and
unsupported types — while keeping useful special files (Dockerfile, Makefile,
README/LICENSE, `.gitignore`, `.env.example`). Kept files are stored with path,
language, size, and a SHA-256 checksum. Limits (200 MB repo / 5000 files / 1 MB
per file / 300 s clone) are all configurable.

M4 supports public repositories, embeddings, and semantic search. Private repository support remains out of scope.

Example flow:

```bash
curl -X POST localhost:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"a proper long passphrase"}'

TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"a proper long passphrase"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

### Semantic search

Once a repository is indexed, its files are split into overlapping line-window
chunks (40 lines, 10 overlapping, both configurable), each embedded with
**jinaai/jina-embeddings-v2-base-code** and stored in pgvector. Chunking and
embedding run inside the existing index job — indexing a repo makes it
searchable in one step. The unchanged-HEAD short-circuit still applies, and a
changed file re-chunks and re-embeds only that file.

Search embeds the query with the same model and returns the nearest chunks by
cosine similarity:

```bash
curl -X POST localhost:8000/api/v1/repositories/$REPO/search \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query": "where are JWT tokens validated", "top_k": 5}'
```

Each result carries `path`, `start_line`, `end_line`, `content`, and a `score`
(cosine similarity, higher is closer; not a confidence probability). `top_k` defaults to 10 and is capped
at 50. A repository with no embeddings yet returns `409` — index it first. This
milestone returns chunks, not generated answers; answer synthesis is a later
milestone.

**The embedding dimension is a schema invariant.** Migration 0004 declares the
vector column as `vector(768)`, so `DEVPILOT_EMBEDDING_DIM` must be 768 for this
schema version; the app refuses to start otherwise, and the real provider also
checks the loaded model's width against it. Changing the model to a different
dimension requires a new migration that alters the column and rebuilds the HNSW
index — it is not a config-only change.

**First index is slow.** The real model (~322 MB) downloads once on first use
into `HF_HOME`, a named Docker volume shared by the API and worker; subsequent
runs reuse it.

**Retrieval evaluation.** `backend/eval/` holds a small harness with
hand-labeled ground truth that computes recall@k and MRR against a real index.
It is run manually with the real model, never in CI, and it refuses to emit a
number without real embeddings — so no metric is fabricated. As of this
milestone the eval has **not been run**.

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
| M2 | Users, registration, login, JWT access tokens ✅ |
| M3 | Repository import, durable queue, worker, safe cloning, file discovery ✅ |
| M4 | Chunking, embeddings, pgvector storage, semantic search ✅ |
| M5 | Hybrid retrieval + RRF |
| M6 | Tree-sitter AST chunking |
| M7 | Chat, LLM provider port, SSE streaming, citations |
| M8 | React frontend |
| M9 | Test hardening, docs |

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

Automated tests never download the embedding model or touch the network: they
run with a deterministic fake embedding provider (`DEVPILOT_EMBEDDING_PROVIDER=fake`),
which makes retrieval assertions exact (a query equal to a chunk retrieves it)
while staying offline. The real model is exercised by manual browser/API smoke checks and the optional eval harness.

Unit tests stub the database; they are about HTTP behaviour. Integration tests
use a real Postgres, because a mock cannot tell you that a migration failed to
apply or that pgvector is missing. CI runs both against a `pgvector/pgvector:pg16`
service, applies migrations, and verifies they are reversible.

The image is built in three stages. `builder` carries compilers and build
metadata. `runtime` carries neither and is what deploys — no pytest, no test
code. `dev` layers the test dependencies and the suite on top of
`runtime` and is what Compose builds locally. CI builds `runtime`
separately, which is what keeps that separation honest: if a test dependency
ever leaks into production, that job is where it surfaces.

## Browser workspace

Open http://localhost:8000/ after starting the Docker Compose stack. The browser
workspace is served by the API itself; no separate frontend server is required.

- Sign in with a DevPilot account or create one in the sign-in dialog.
- Select an existing repository, or import a public GitHub URL and click Index repository.
- Ask a natural-language question to retrieve the three most similar source excerpts.
- Expand or copy code and follow citations to the indexed commit on GitHub.
- The BrainTumorClassifier demo includes suggested questions for its training and data-loading code.

Authentication is still enforced by the API. Access tokens are kept in the current
browser tab's session storage and cleared on sign-out. Expired sessions require
signing in again. Repository contents are rendered as text, including highlighted
code; retrieved code is never executed by the browser. Similarity scores are ranking
signals, not confidence probabilities, and results are excerpts rather than generated answers.

The workspace needs the local API and database to be running. Its optional web fonts
fall back to system fonts when offline. Keep account credentials and local demo
launchers out of source control.
