"""Password hashing and access-token handling.

This module is the only place that knows how a password becomes a hash or how
a token is signed and verified. Services call these functions; they never see
argon2 or jwt directly. Concentrating the primitives here means the hashing
parameters, the signing algorithm, and the claim set each have a single
definition that tests can pin and a future change can touch in one place.

Two security properties are enforced here rather than left to callers:

- Password *byte* length is bounded before hashing. The API validates
  character length, but Argon2 operates on bytes, and a 128-character string
  of 4-byte code points is 512 bytes. Bounding bytes here stops an expensive
  hash from being forced regardless of how the caller reached this function.
- Token decoding pins the accepted algorithm to a single value. Passing the
  algorithm as a list of one, rather than trusting the token's own header, is
  what defeats ``alg: none`` and RS256-to-HS256 confusion attacks.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from devpilot.core.config import Settings

# Argon2id is the default type for this hasher. The remaining parameters are
# the argon2-cffi defaults, which track the library maintainers' guidance;
# pinning them explicitly documents what produced existing hashes and makes a
# future parameter bump a visible change rather than a silent dependency drift.
_password_hasher = PasswordHasher()

# Hard ceiling on the bytes handed to the hasher. The password policy caps
# input at 128 characters; at up to 4 UTF-8 bytes each that is 512 bytes. The
# ceiling is set here, independently of the API, so this function is safe no
# matter who calls it.
_MAX_PASSWORD_BYTES = 1024


class PasswordTooLongError(ValueError):
    """Raised when a password exceeds the byte ceiling for hashing."""


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id.

    Args:
        password: The plaintext password. Character-length policy is enforced
            by the API schema; this function independently bounds byte length.

    Returns:
        The Argon2 encoded hash, which embeds the algorithm, parameters, and
        salt, and is therefore self-describing for later verification.

    Raises:
        PasswordTooLongError: If the UTF-8 encoding exceeds the byte ceiling.

    """
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise PasswordTooLongError("password exceeds the maximum permitted length")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2 hash.

    Returns ``False`` rather than raising on mismatch or on a malformed stored
    hash, so callers branch on a boolean and never leak, through an exception
    type, which of "no such user" or "wrong password" occurred.

    Args:
        password: The plaintext password supplied at login.
        password_hash: The stored Argon2 encoded hash.

    Returns:
        True if the password matches, False otherwise.

    """
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(subject: uuid.UUID, settings: Settings) -> str:
    """Create a signed JWT access token for a user.

    The token carries the three claims the ``/me`` path needs and nothing more:
    ``sub`` (the user id as a string), ``iat`` (issued-at), and ``exp``
    (expiry). No email, no role -- claims that would go stale the moment the
    user record changed and would have to be trusted without re-reading the
    database.

    Args:
        subject: The authenticated user's id.
        settings: Supplies the signing secret, algorithm, and access TTL.

    Returns:
        The encoded, signed JWT as a compact string.

    """
    now = datetime.now(UTC)
    claims = {
        "sub": str(subject),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class TokenError(Exception):
    """Raised when an access token is missing, malformed, or expired.

    A single exception type covers every failure mode on purpose. A caller
    catching this cannot distinguish an expired token from a forged one and so
    cannot leak that distinction to a client.
    """


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    """Validate an access token and return its subject.

    Args:
        token: The bare JWT, with any ``Bearer`` prefix already stripped.
        settings: Supplies the verification secret and the single permitted
            algorithm.

    Returns:
        The user id parsed from the ``sub`` claim.

    Raises:
        TokenError: If the signature is invalid, the token has expired, the
            algorithm does not match, required claims are absent, or ``sub`` is
            not a well-formed UUID.

    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            # A list of exactly one. The token's own ``alg`` header is never
            # trusted, so a token claiming ``none`` or a different algorithm is
            # rejected before its signature is even considered.
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "iat", "exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError(str(exc)) from exc

    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise TokenError("token subject is missing or not a string")
    try:
        return uuid.UUID(subject)
    except ValueError as exc:
        raise TokenError("token subject is not a valid identifier") from exc
