"""Domain errors and their translation to HTTP responses.

Services raise domain errors that carry no knowledge of HTTP. The API layer
installs handlers that map those errors onto status codes. This keeps the
service layer transport-agnostic: the same ``IndexingService`` that runs
behind FastAPI also runs inside the worker process, where ``HTTPException``
would be meaningless.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class DomainError(Exception):
    """Base class for all expected, application-level failures.

    Attributes:
        code: Stable machine-readable identifier for the failure. Clients
            branch on this, never on the human-readable message.
        message: Human-readable description, safe to surface to the caller.
        details: Optional structured context, e.g. offending field names.

    """

    code: str = "internal_error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """Initialise the error.

        Args:
            message: Human-readable description of the failure.
            details: Optional structured context about the failure.

        """
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    """A requested entity does not exist, or is not visible to the caller."""

    code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(DomainError):
    """The request conflicts with existing state, e.g. a uniqueness violation."""

    code = "conflict"
    status_code = status.HTTP_409_CONFLICT


class ValidationError(DomainError):
    """The request is well-formed but semantically invalid."""

    code = "validation_error"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class AuthenticationError(DomainError):
    """The caller could not be authenticated."""

    code = "unauthenticated"
    status_code = status.HTTP_401_UNAUTHORIZED


class AuthorizationError(DomainError):
    """The caller is authenticated but not permitted to perform the action."""

    code = "forbidden"
    status_code = status.HTTP_403_FORBIDDEN


class ServiceUnavailableError(DomainError):
    """A required downstream dependency is unavailable."""

    code = "service_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


def _error_body(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical error response body.

    Args:
        code: Machine-readable error code.
        message: Human-readable message.
        details: Structured context.

    Returns:
        A JSON-serialisable error envelope.

    """
    return {"error": {"code": code, "message": message, "details": details}}


async def _handle_domain_error(_request: Request, exc: Exception) -> JSONResponse:
    """Translate a ``DomainError`` into its HTTP representation.

    Args:
        _request: The incoming request; unused.
        exc: The raised exception, expected to be a ``DomainError``.

    Returns:
        A JSON response carrying the error envelope.

    """
    assert isinstance(exc, DomainError)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details),
    )


async def _handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
    """Return an opaque 500 for unexpected exceptions.

    Internal failure detail is deliberately withheld from the response body;
    the traceback is emitted to the logs instead.

    Args:
        _request: The incoming request; unused.
        _exc: The raised exception; unused.

    Returns:
        A generic 500 JSON response.

    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            "internal_error",
            HTTPStatus.INTERNAL_SERVER_ERROR.phrase,
            {},
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register domain error handlers on the application.

    Args:
        app: The FastAPI application to install handlers on.

    """
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
