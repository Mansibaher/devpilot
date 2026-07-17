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
