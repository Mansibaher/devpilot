"""Domain exceptions raised by the service layer.

These extend the ``DomainError`` hierarchy in ``core.errors``, so the handler
already installed on the app maps them to status codes and the canonical error
envelope with no new wiring. The service layer still imports only this module,
not FastAPI: ``DomainError`` itself is transport-agnostic and carries no HTTP
machinery, only a status code that the API layer chooses to honour.
"""

from devpilot.core.errors import AuthenticationError, ConflictError


class EmailAlreadyExistsError(ConflictError):
    """Registration attempted with an address already in use.

    Maps to 409. The message is generic and names no address, since it may be
    logged and whether a given email is registered is precisely what the
    enumeration defences avoid revealing.
    """

    def __init__(self) -> None:
        """Initialise with a fixed, address-free message."""
        super().__init__("An account with these details could not be created.")


class InvalidCredentialsError(AuthenticationError):
    """Authentication failed.

    Maps to 401. Raised identically for an unknown email, a wrong password, and
    an inactive account, with one fixed message, so neither the response nor a
    log line can distinguish the cases.
    """

    def __init__(self) -> None:
        """Initialise with the single generic authentication-failure message."""
        super().__init__("Invalid email or password.")


class RepositoryNotIndexedError(ConflictError):
    """Search attempted on a repository that has no embeddings yet.

    Maps to 409. The repository exists and belongs to the user, but it has not
    been indexed (or indexing has not produced any searchable chunks), so there
    is nothing to search. A distinct 409 tells the client to index first, rather
    than an empty 200 that looks like "no matches".
    """

    def __init__(self) -> None:
        """Initialise with a message directing the caller to index first."""
        super().__init__(
            "This repository has not been indexed yet. Queue an index job and "
            "wait for it to succeed before searching."
        )
