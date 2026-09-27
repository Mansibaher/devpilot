# Public demo deployment plan

Status: preparation only. Nothing has been deployed or purchased.

## Proposed first release

Offer a small, read-only demonstration over preloaded public repositories. Visitors should be able to search without being allowed to start arbitrary indexing jobs. This requires a server-enforced demo mode; hiding buttons alone is insufficient. The current app has owner-scoped authenticated repositories and does not yet provide this public read-only mode.

## Required components

- A host that can run the FastAPI service and Python embedding model continuously.
- A PostgreSQL database with the pgvector extension and persistent storage.
- A worker for initial indexing and later administrator-triggered updates.
- Persistent model caching to avoid downloading weights on every restart.
- An HTTPS endpoint for the browser and API, preferably on the same origin.

The API and worker can each load the embedding model. Measure peak memory and cold-start latency with both running before selecting a server size. No hardware sizing or hosting price has been verified yet.

## Work before public deployment

1. Implement a read-only demo route scoped to explicitly allowed repositories. Keep visitor registration, import, and indexing disabled for that demo.
2. Add server-side request limits and query concurrency limits, plus clear handling of overloaded searches. The current query embedding call is synchronous inside the request path and needs review for concurrent visitors.
3. Create a separate production configuration using the runtime image, no source bind mounts or reload, production settings, and independently generated secrets. The current development Compose file exposes database port 5432 and uses local database credentials; do not deploy it unchanged.
4. Use a private database connection, persistent volumes, backups, and a tested restore procedure. Run migrations once before starting the application services.
5. Configure HTTPS, startup/restart behavior, logs, and health/readiness monitoring. Add a real search smoke check because database readiness alone does not validate the embedding model.
6. Choose a host and spending limit after measuring resources; review current provider documentation and pricing at that point.
7. Deploy, seed the allowed repositories, and verify the public URL from another device with the laptop switched off.

## Acceptance checks

- A visitor can search the selected repositories and follow valid citations.
- Visitors cannot access other accounts' data or launch indexing jobs.
- Request limits are enforced by the server.
- The app recovers after restart without losing its database or model cache.
- Backups can be restored and secrets are not included in source control or browser assets.
- The public demo works independently of the developer's laptop.

Next decision: choose a hosting budget and account after the read-only demo changes are ready. A source repository and recorded video can be published before the live service exists.
