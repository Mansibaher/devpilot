"""Authentication business logic.

Holds everything that is neither HTTP nor SQL: email normalisation, the
duplicate-registration rule, password hashing and verification, and the
constant-time-ish authentication path that resists account enumeration. Routes
call this; it calls the repository and the security primitives. It never
imports FastAPI and never writes a raw query.
"""

import uuid

from sqlalchemy.exc import IntegrityError

from devpilot.core.config import Settings
from devpilot.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from devpilot.models.user import User
from devpilot.repositories.user_repo import UserRepository
from devpilot.services.exceptions import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
)

# A precomputed Argon2 hash of a throwaway value. Verifying against this on the
# unknown-email path makes that path do the same work as the wrong-password
# path, so the two cannot be told apart by response time. Without it, "no such
# user" returns fast and "wrong password" returns slow, and that timing gap is
# a working account-enumeration oracle.
_DUMMY_HASH = hash_password("a-fixed-value-used-only-for-timing-equalisation")


class AuthService:
    """Registration and authentication operations."""

    def __init__(self, users: UserRepository, settings: Settings) -> None:
        """Construct the service.

        Args:
            users: The user repository.
            settings: Supplies token signing configuration.

        """
        self._users = users
        self._settings = settings

    @staticmethod
    def _normalise_email(email: str) -> str:
        """Return a canonical form of an email for storage and lookup.

        Lowercasing and trimming here is defence in depth: the ``citext``
        column already compares case-insensitively, but normalising in the
        application means the stored value is canonical and every lookup uses
        the same form.
        """
        return email.strip().lower()

    async def register(self, email: str, password: str) -> User:
        """Create a new account.

        Args:
            email: The requested email; normalised before use.
            password: The plaintext password. Length is already bounded by the
                request schema; it is hashed, never stored or logged.

        Returns:
            The created ``User``.

        Raises:
            EmailAlreadyExistsError: If the address is already registered.

        """
        normalised = self._normalise_email(email)

        # Pre-check for a friendly error on the common case. It is not the
        # integrity guarantee -- two concurrent registrations could both pass
        # this check -- so the unique constraint below is the real backstop and
        # this is only here to avoid hashing a password that will be rejected.
        if await self._users.get_by_email(normalised) is not None:
            raise EmailAlreadyExistsError

        password_hash = hash_password(password)
        try:
            return await self._users.create(email=normalised, password_hash=password_hash)
        except IntegrityError as exc:
            # The race the pre-check cannot cover: another request inserted the
            # same email between the check and this insert. The database unique
            # constraint is what actually prevents the duplicate; we translate
            # its violation into the same domain error.
            raise EmailAlreadyExistsError from exc

    async def authenticate(self, email: str, password: str) -> str:
        """Verify credentials and return a signed access token.

        Always performs a password verification, even when no user exists, so
        the unknown-email and wrong-password paths take the same time and
        raise the same error.

        Args:
            email: The submitted email.
            password: The submitted plaintext password.

        Returns:
            A signed JWT access token.

        Raises:
            InvalidCredentialsError: If the email is unknown, the password is
                wrong, or the account is inactive. The caller cannot tell which.

        """
        normalised = self._normalise_email(email)
        user = await self._users.get_by_email(normalised)

        # Verify against the real hash if the user exists, otherwise against the
        # dummy hash. Either way a full Argon2 verification runs, so timing does
        # not distinguish the two cases.
        hash_to_check = user.password_hash if user is not None else _DUMMY_HASH
        password_ok = verify_password(password, hash_to_check)

        if user is None or not password_ok or not user.is_active:
            raise InvalidCredentialsError

        return create_access_token(user.id, self._settings)

    async def get_user(self, user_id: uuid.UUID) -> User:
        """Return the user for an authenticated subject.

        Args:
            user_id: The id taken from a validated token's subject claim.

        Returns:
            The ``User``.

        Raises:
            InvalidCredentialsError: If no active user matches. A token whose
                subject no longer exists or has been deactivated is treated as
                invalid, so a deactivated user cannot keep using a live token.

        """
        user = await self._users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError
        return user
