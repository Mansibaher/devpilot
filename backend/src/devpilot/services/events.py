"""Repository event types and a helper to record them.

Event types are constants, not free strings, so a typo becomes an import error
rather than a row that silently never matches a query. Recording an event is a
plain insert through the event repository; it is intentionally fire-and-forget
from the caller's point of view, part of the same transaction as the state
change it describes.
"""

# Emitted by the API service when a repository row is first created.
EVENT_REPOSITORY_CREATED = "repository_created"
# Emitted by the API service when an index job is enqueued.
EVENT_INDEX_QUEUED = "index_queued"
# Emitted by the worker as it claims and begins a job.
EVENT_INDEX_STARTED = "index_started"
# Emitted by the worker when a job finishes successfully.
EVENT_INDEX_COMPLETED = "index_completed"
# Emitted by the worker when a job fails (transiently or permanently).
EVENT_INDEX_FAILED = "index_failed"
# Emitted by the worker when the cloned HEAD already matches the last indexed
# SHA and the file scan is skipped.
EVENT_INDEX_SKIPPED_UNCHANGED = "index_skipped_unchanged"
