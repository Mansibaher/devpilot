"""Repository indexing business logic (worker side).

This runs inside the worker, never inside a request. It clones a repository into
an isolated temporary directory, walks the tree applying the filtering rules,
persists metadata for the files that survive, and records what happened -- then
destroys the clone. It imports no web framework; it is the payoff for keeping
services HTTP-free since M1.

The flow is: clone shallow and blobless -> read HEAD -> if HEAD already matches
the repository's last indexed SHA, skip the scan entirely and mark success ->
otherwise walk, filter, checksum, upsert, and prune -> record the terminal
event. The temporary directory is always removed, success or failure.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from devpilot.core.config import Settings
from devpilot.indexing.chunker import chunk_file
from devpilot.indexing.file_filter import classify_path, looks_binary
from devpilot.models.index_job import IndexJob
from devpilot.models.repository import (
    REPO_STATUS_FAILED,
    REPO_STATUS_INDEXED,
    REPO_STATUS_INDEXING,
)
from devpilot.providers.embeddings.base import EmbeddingProvider
from devpilot.providers.vcs.git_client import GitClient
from devpilot.repositories.chunk_repo import ChunkRepository
from devpilot.repositories.embedding_repo import EmbeddingRepository
from devpilot.repositories.event_repo import EventRepository
from devpilot.repositories.file_repo import FileRepository
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.services import events

_READ_CHUNK = 65536


class RepositoryTooLargeError(Exception):
    """Raised when a clone exceeds a configured size or file-count limit."""


@dataclass
class IndexOutcome:
    """The result of indexing one repository.

    Attributes:
        commit_sha: The HEAD SHA that was indexed or checked.
        default_branch: The branch that was cloned.
        skipped_unchanged: True if HEAD matched the last indexed SHA.
        files_indexed: How many files were persisted (zero when skipped).
        kept_paths: The paths persisted this run.
        chunks_indexed: How many chunks were embedded (zero when embedding is
            disabled or the repo was unchanged).

    """

    commit_sha: str
    default_branch: str | None
    skipped_unchanged: bool = False
    files_indexed: int = 0
    kept_paths: list[str] = field(default_factory=list)
    chunks_indexed: int = 0


class IndexingService:
    """Clones a repository and persists its file metadata."""

    def __init__(
        self,
        repositories: RepositoryRepository,
        files: FileRepository,
        jobs: JobRepository,
        event_repo: EventRepository,
        git: GitClient,
        settings: Settings,
        chunks: ChunkRepository | None = None,
        embeddings: EmbeddingRepository | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        """Construct the service.

        Args:
            repositories: Repository data access.
            files: File persistence.
            jobs: Job/queue data access, for progress updates.
            event_repo: Audit-event data access.
            git: The hardened git client.
            settings: Supplies the indexing limits and chunk parameters.
            chunks: Chunk persistence. When omitted (with ``embeddings`` and
                ``embedder``), the embedding stage is skipped -- used by tests
                that exercise only M3 file discovery.
            embeddings: Embedding persistence and search.
            embedder: The embedding provider (real or fake).

        """
        self._repositories = repositories
        self._files = files
        self._jobs = jobs
        self._events = event_repo
        self._git = git
        self._settings = settings
        self._chunks = chunks
        self._embeddings = embeddings
        self._embedder = embedder

    @property
    def _embedding_enabled(self) -> bool:
        """Return True when all embedding collaborators are present."""
        return (
            self._chunks is not None and self._embeddings is not None and self._embedder is not None
        )

    async def run(self, job: IndexJob) -> IndexOutcome:
        """Index the repository referenced by a claimed job.

        Args:
            job: A job already claimed and marked running by the worker.

        Returns:
            An :class:`IndexOutcome` describing what happened.

        Raises:
            NotFoundError: If the repository row has vanished.
            GitError: On a clone or git failure.
            RepositoryTooLargeError: If a configured limit is exceeded.

        """
        repository = await self._repositories.get_by_id_unscoped(job.repository_id)
        if repository is None:
            # The repository was deleted after the job was queued.
            raise FileNotFoundError("repository row not found")

        repository.status = REPO_STATUS_INDEXING
        await self._jobs.heartbeat(job.id)

        tmp = Path(tempfile.mkdtemp(prefix="devpilot-clone-"))
        try:
            clone_dir = tmp / "repo"
            self._git.shallow_clone(repository.clone_url, clone_dir)
            head_sha = self._git.head_sha(clone_dir)
            branch = self._git.current_branch(clone_dir)
            repository.default_branch = branch

            # Unchanged short-circuit: identical HEAD means the stored files are
            # already correct, so the scan and rewrite are pure waste.
            if repository.last_indexed_sha == head_sha:
                repository.status = REPO_STATUS_INDEXED
                await self._events.record(
                    repository.id,
                    events.EVENT_INDEX_SKIPPED_UNCHANGED,
                    {"job_id": str(job.id), "commit_sha": head_sha},
                )
                return IndexOutcome(
                    commit_sha=head_sha, default_branch=branch, skipped_unchanged=True
                )

            outcome = await self._scan_and_persist(job, repository.id, clone_dir, head_sha, branch)
            repository.last_indexed_sha = head_sha
            repository.status = REPO_STATUS_INDEXED
            return outcome
        except Exception:
            repository.status = REPO_STATUS_FAILED
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def _scan_and_persist(
        self,
        job: IndexJob,
        repository_id: uuid.UUID,
        clone_dir: Path,
        head_sha: str,
        branch: str | None,
    ) -> IndexOutcome:
        """Walk a clone, filter files, and persist their metadata.

        Args:
            job: The running job, updated with progress.
            repository_id: The repository being indexed.
            clone_dir: The cloned working tree.
            head_sha: The HEAD SHA being indexed.
            branch: The cloned branch name.

        Returns:
            The index outcome.

        Raises:
            RepositoryTooLargeError: If size or file-count limits are exceeded.

        """
        kept: list[dict[str, object]] = []
        kept_paths: list[str] = []
        total_bytes = 0
        candidate_count = 0

        for absolute in sorted(clone_dir.rglob("*")):
            if not absolute.is_file() or absolute.is_symlink():
                continue
            relative = absolute.relative_to(clone_dir).as_posix()

            decision = classify_path(relative)
            if not decision.keep:
                continue

            size = absolute.stat().st_size
            if size > self._settings.index_max_file_bytes:
                continue  # oversized single file: skip, not fatal

            candidate_count += 1
            if candidate_count > self._settings.index_max_files:
                raise RepositoryTooLargeError(
                    f"repository exceeds {self._settings.index_max_files} indexable files"
                )
            total_bytes += size
            if total_bytes > self._settings.index_max_repo_bytes:
                raise RepositoryTooLargeError("repository exceeds the maximum indexable size")

            sample = absolute.read_bytes()[:8192]
            if looks_binary(sample):
                continue

            checksum = self._hash_file(absolute)
            kept.append(
                {
                    "path": relative,
                    "language": decision.language,
                    "size_bytes": size,
                    "checksum_sha256": checksum,
                }
            )
            kept_paths.append(relative)

            if len(kept) % 100 == 0:
                await self._jobs.update_progress(
                    job.id, files_done=len(kept), current_file=relative
                )
                await self._jobs.heartbeat(job.id)

        await self._jobs.update_progress(
            job.id, files_total=len(kept), files_done=len(kept), current_file=None
        )
        await self._files.upsert_many(repository_id, kept)
        await self._files.delete_paths_not_in(repository_id, kept_paths)

        chunks_written = 0
        if self._embedding_enabled:
            chunks_written = await self._chunk_and_embed(job, repository_id, clone_dir, kept_paths)

        await self._events.record(
            repository_id,
            events.EVENT_INDEX_COMPLETED,
            {
                "job_id": str(job.id),
                "commit_sha": head_sha,
                "files": len(kept),
                "chunks": chunks_written,
            },
        )
        return IndexOutcome(
            commit_sha=head_sha,
            default_branch=branch,
            files_indexed=len(kept),
            kept_paths=kept_paths,
            chunks_indexed=chunks_written,
        )

    async def _chunk_and_embed(
        self,
        job: IndexJob,
        repository_id: uuid.UUID,
        clone_dir: Path,
        kept_paths: list[str],
    ) -> int:
        """Chunk each kept file, embed the chunks, and persist both.

        Runs after file rows exist, so each path maps to a persisted file id.
        For every kept file it reads the text from the clone, splits it into
        line windows, replaces that file's chunks, embeds them in one batch, and
        stores the vectors. Chunk replacement is per file, so re-indexing a
        changed repository rewrites only the files that changed and prunes
        chunks for files that disappeared.

        Args:
            job: The running job, for progress and heartbeats.
            repository_id: The repository being indexed.
            clone_dir: The cloned working tree (still present at this point).
            kept_paths: Paths that survived filtering, in scan order.

        Returns:
            The total number of chunks embedded.

        """
        assert self._chunks is not None
        assert self._embeddings is not None
        assert self._embedder is not None

        # Map each kept path to its persisted file id in one query.
        files = await self._files.list_for_repository(repository_id)
        file_id_by_path = {f.path: f.id for f in files}
        keep_file_ids = [file_id_by_path[p] for p in kept_paths if p in file_id_by_path]

        total_chunks = 0
        for done, path in enumerate(kept_paths, start=1):
            file_id = file_id_by_path.get(path)
            if file_id is None:
                continue
            absolute = clone_dir / path
            try:
                text_content = absolute.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            file_chunks = chunk_file(
                path,
                text_content,
                window_lines=self._settings.chunk_window_lines,
                overlap_lines=self._settings.chunk_overlap_lines,
            )
            if not file_chunks:
                # Empty file: ensure no stale chunks linger for it.
                await self._chunks.replace_for_file(
                    repository_id=repository_id, file_id=file_id, chunks=[]
                )
                continue

            chunk_rows = await self._chunks.replace_for_file(
                repository_id=repository_id,
                file_id=file_id,
                chunks=[
                    {
                        "chunk_index": c.chunk_index,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "content": c.content,
                        "content_sha256": c.content_sha256,
                    }
                    for c in file_chunks
                ],
            )
            vectors = self._embedder.embed_documents([c.content for c in file_chunks])
            await self._embeddings.add_many(
                repository_id=repository_id,
                model=self._embedder.model_name,
                dim=self._embedder.dim,
                items=list(zip((r.id for r in chunk_rows), vectors, strict=True)),
            )
            total_chunks += len(chunk_rows)

            if done % 50 == 0:
                await self._jobs.update_progress(job.id, current_file=path)
                await self._jobs.heartbeat(job.id)

        # Prune chunks for files that no longer exist in the repository.
        await self._chunks.delete_for_repository_except_files(repository_id, keep_file_ids)
        return total_chunks

    @staticmethod
    def _hash_file(path: Path) -> str:
        """Return the hex SHA-256 of a file's contents, read in chunks.

        Args:
            path: The file to hash.

        Returns:
            The hex digest.

        """
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()
