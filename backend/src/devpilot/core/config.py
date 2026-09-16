"""Application configuration.

All configuration enters the process through environment variables and is
validated exactly once, here. No module outside this file reads ``os.environ``.

Settings are cached so that ``get_settings`` can be used as a FastAPI
dependency without re-parsing the environment on every request, while still
being overridable in tests via ``get_settings.cache_clear()``.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
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

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

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
