"""Application configuration.

All configuration enters the process through environment variables and is
validated exactly once, here. No module outside this file reads ``os.environ``.

Settings are cached so that ``get_settings`` can be used as a FastAPI
dependency without re-parsing the environment on every request, while still
being overridable in tests via ``get_settings.cache_clear()``.
"""

from functools import lru_cache
from typing import ClassVar, Literal

from pydantic import Field, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "ci", "production"]


class Settings(BaseSettings):
    """Runtime configuration for the API and worker processes.

    Attributes:
        environment: Deployment environment. Controls log rendering and
            whether debug affordances are permitted.
        debug: Enables SQL echo and verbose error output. Must never be true
            in production.
        log_level: Standard library log level name.
        database_url: Async Postgres DSN. Must use the ``postgresql+asyncpg``
            scheme because the application uses SQLAlchemy's asyncio engine.
        db_pool_size: Persistent connections held per process.
        db_max_overflow: Additional connections opened under burst load.
        db_pool_timeout_seconds: How long a caller waits for a connection
            before failing fast rather than queueing indefinitely.
        jwt_secret: HS256 signing key for access tokens. Required; no default.
        jwt_algorithm: Signing algorithm, constrained to HS256.
        jwt_access_ttl_minutes: Access-token lifetime in minutes.
        index_max_repo_bytes: Reject a clone whose working tree exceeds this.
        index_max_files: Reject a repo with more discoverable files than this.
        index_max_file_bytes: Skip any single file larger than this.
        index_clone_timeout_seconds: Kill a clone that runs longer than this.
        worker_poll_interval_seconds: Idle sleep between empty claim attempts.
        worker_heartbeat_interval_seconds: How often a running job renews its lease.
        worker_lease_timeout_seconds: Age past which a silent lease is reaped.
        job_max_attempts: Attempts before a job is marked permanently failed.
        embedding_provider: 'sentence_transformer' (real, lazy) or 'fake'.
        embedding_model: sentence-transformers model id; must emit embedding_dim.
        embedding_dim: Vector dimension. A schema invariant (see the validator),
            not a free knob: it must match the vector column in migration 0004.
        embedding_batch_size: Chunks embedded per batch.
        chunk_window_lines: Lines per chunk window (> 0).
        chunk_overlap_lines: Overlap between adjacent windows (>= 0, < window).
        search_default_top_k: Default result count for semantic search.
        search_max_top_k: Hard cap on requested result count.
        cors_origins: Browser origins permitted to call the API.

    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DEVPILOT_",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = "local"
    debug: bool = False
    log_level: str = "INFO"

    database_url: PostgresDsn = Field(
        description="Async Postgres DSN, e.g. postgresql+asyncpg://user:pass@host:5432/db",
    )
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0)

    jwt_secret: str = Field(
        min_length=32,
        description=(
            "Secret used to sign access tokens (HS256). Required, with no "
            "default: an app that signs tokens with a predictable key is worse "
            "than one that refuses to start. The 32-character floor is enforced "
            "here so a weak secret fails at boot rather than in production."
        ),
    )
    jwt_algorithm: Literal["HS256"] = Field(
        default="HS256",
        description=(
            "Signing algorithm. A Literal, not a free string, so it can never "
            "be set to 'none'. Token decoding pins this same value explicitly, "
            "which is the defence against alg-substitution attacks."
        ),
    )
    jwt_access_ttl_minutes: int = Field(default=15, ge=1, le=1440)

    # --- Indexing limits (M3). All configurable via the environment. Each is a
    # distinct defence: a repo can be under the size cap but have too many
    # files, or within the file cap but contain one enormous blob. ---
    index_max_repo_bytes: int = Field(default=200 * 1024 * 1024, ge=1)
    index_max_files: int = Field(default=5000, ge=1)
    index_max_file_bytes: int = Field(default=1024 * 1024, ge=1)
    index_clone_timeout_seconds: int = Field(default=300, ge=1, le=3600)

    # --- Worker loop (M3) ---
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    worker_heartbeat_interval_seconds: float = Field(default=10.0, gt=0)
    worker_lease_timeout_seconds: float = Field(default=60.0, gt=0)
    job_max_attempts: int = Field(default=3, ge=1, le=10)

    # --- Embeddings (M4) ---
    # The provider is swappable behind the EmbeddingProvider port, but it MUST
    # produce vectors of EMBEDDING_DIM, and EMBEDDING_DIM is not a free knob:
    # migration 0004 declares the column as vector(768), so 768 is an invariant
    # of this schema version. Changing it requires a new migration that alters
    # (or recreates) the embedding column and rebuilds the HNSW index. The real
    # provider validates its own output dimension against this value at load
    # time and fails loudly on a mismatch, so a model that does not match the
    # schema can never write rows the database would reject anyway.
    embedding_provider: Literal["sentence_transformer", "fake"] = Field(
        default="sentence_transformer",
        description=(
            "Which embedding backend to use. 'fake' is deterministic and needs "
            "no model or network, and is what tests and CI run with. "
            "'sentence_transformer' loads the real model lazily on first use."
        ),
    )
    embedding_model: str = Field(
        default="jinaai/jina-embeddings-v2-base-code",
        description=(
            "The sentence-transformers model id. Must emit EMBEDDING_DIM "
            "dimensions to be compatible with the vector(768) schema column."
        ),
    )
    # Fixed to the schema's vector dimension. Exposed as a setting only so the
    # fake provider and the real provider agree on a single source of truth --
    # not because it is safe to change without a migration. The validator below
    # rejects any value that would silently diverge from the 0004 schema.
    embedding_dim: int = Field(default=768, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)

    # --- Chunking (M4) ---
    chunk_window_lines: int = Field(default=40, gt=0)
    chunk_overlap_lines: int = Field(default=10, ge=0)

    # --- Semantic search (M4) ---
    search_default_top_k: int = Field(default=10, ge=1, le=200)
    search_max_top_k: int = Field(default=50, ge=1, le=200)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # The vector(768) column in migration 0004 is the source of truth for the
    # embedding dimension. This constant guards against a config that would
    # write vectors the schema cannot store; it must be updated in lockstep
    # with any future migration that changes the column dimension.
    SCHEMA_EMBEDDING_DIM: ClassVar[int] = 768

    @model_validator(mode="after")
    def _validate_embedding_and_chunking(self) -> "Settings":
        """Enforce the dimension invariant and the chunk-window relationship.

        - ``embedding_dim`` must equal the dimension baked into the current
          schema. Migration 0004 declares ``vector(768)``; a differing value
          would produce vectors Postgres rejects, so this fails at startup with
          a clear message rather than at the first insert.
        - ``chunk_overlap_lines`` must be strictly less than
          ``chunk_window_lines``; an overlap equal to or larger than the window
          would never advance and would loop forever.
        """
        if self.embedding_dim != self.SCHEMA_EMBEDDING_DIM:
            raise ValueError(
                f"embedding_dim={self.embedding_dim} does not match the schema "
                f"dimension {self.SCHEMA_EMBEDDING_DIM}. The embedding column is "
                f"vector({self.SCHEMA_EMBEDDING_DIM}); changing the dimension "
                "requires a database migration, not just a config change."
            )
        if self.chunk_overlap_lines >= self.chunk_window_lines:
            raise ValueError(
                f"chunk_overlap_lines ({self.chunk_overlap_lines}) must be less "
                f"than chunk_window_lines ({self.chunk_window_lines})."
            )
        return self

    @property
    def is_production(self) -> bool:
        """Return True when running in the production environment."""
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Returns:
        The validated ``Settings`` instance for this process.

    """
    return Settings()
