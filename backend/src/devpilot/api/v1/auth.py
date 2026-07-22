"""Authentication routes.

Thin by design. Each handler validates input via its schema, calls one service
method, and shapes the result. No hashing, no token logic, no SQL, no
conditional business rules live here -- those are in the service and the
security module. If a handler grows an ``if``, it is a sign logic has leaked
into the wrong layer.
"""

from fastapi import APIRouter, status

from devpilot.api.deps import AuthServiceDep, CurrentUserDep
from devpilot.schemas.auth import TokenResponse, UserLogin, UserRead, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
)
async def register(payload: UserRegister, auth_service: AuthServiceDep) -> UserRead:
    """Create an account and return its public representation.

    The response is a ``UserRead``, which has no password field, so the hash
    cannot appear in the body. A duplicate address raises a domain error that
    the installed handler renders as 409.

    Args:
        payload: Validated registration request.
        auth_service: The authentication service.

    Returns:
        The created user, without any credential material.

    """
    user = await auth_service.register(payload.email, payload.password)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for an access token",
)
async def login(payload: UserLogin, auth_service: AuthServiceDep) -> TokenResponse:
    """Authenticate and return a bearer access token.

    Unknown email and wrong password produce the identical 401, raised from the
    service, so this handler needs no branching to preserve that property.

    Args:
        payload: Validated login request.
        auth_service: The authentication service.

    Returns:
        A signed access token in the OAuth2 bearer shape.

    """
    token = await auth_service.authenticate(payload.email, payload.password)
    return TokenResponse(access_token=token)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Return the authenticated user",
)
async def me(current_user: CurrentUserDep) -> UserRead:
    """Return the account for the presented bearer token.

    All token validation happens in the ``CurrentUserDep`` dependency, which
    returns 401 for any missing, invalid, or expired token before this handler
    runs. Reaching this body means the token was valid.

    Args:
        current_user: The authenticated user, resolved from the token.

    Returns:
        The authenticated user's public representation.

    """
    return UserRead.model_validate(current_user)
