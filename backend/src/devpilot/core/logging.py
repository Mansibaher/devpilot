"""Structured logging setup.

Logs are rendered as JSON everywhere except local development, where a
human-readable console renderer is used instead. A ``request_id`` is bound to
a context variable at the edge of each request and is therefore attached to
every log line emitted downstream without being threaded through call
signatures.
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def _add_request_id(
    _logger: Any,
    _method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Attach the current request id to a log event when one is bound.

    Args:
        _logger: Unused; required by the structlog processor protocol.
        _method_name: Unused; required by the structlog processor protocol.
        event_dict: The mutable log event under construction.

    Returns:
        The event dict, with ``request_id`` added when available.

    """
    request_id = request_id_var.get()
    if request_id is not None:
        event_dict["request_id"] = request_id
    return event_dict


def configure_logging(*, log_level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and the stdlib logging bridge.

    Safe to call more than once; the last call wins.

    Args:
        log_level: Standard library log level name, e.g. ``"INFO"``.
        json_output: Render JSON when True, otherwise a coloured console format.

    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: Logger name, conventionally ``__name__``.

    Returns:
        A configured structlog logger.

    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
