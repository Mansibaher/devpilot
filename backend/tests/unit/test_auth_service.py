"""Unit tests for AuthService branches that are awkward to hit end-to-end.

These use an in-memory fake repository so a specific branch can be forced
directly: the registration race that only the database unique constraint
catches, and the token-for-a-deactivated-user path. Both are security-relevant,
so they get a test that pins the behaviour rather than relying on it being
exercised incidentally by an API test.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from devpilot.core.config import Settings
from devpilot.core.security import decode_access_token, hash_password
from devpilot.models.user import User
from devpilot.services.auth_service import AuthService
from devpilot.services.exceptions import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
)


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        environment="ci",
        database_url="postgresql+asyncpg://u:p@localhost:5432/devpilot_test",
        jwt_secret="unit-auth-service-secret-at-least-32-chars",
    )


class FakeUserRepository:
    """Minimal in-memory stand-in for UserRepository.

    ``raise_integrity_on_create`` simulates the race where another transaction
    inserts the same email between the service's pre-check and its insert, which
    a real database answers with an IntegrityError.
    """

    def __init__(self, *, raise_integrity_on_create: bool = False) -> None:
        self._by_email: dict[str, User] = {}
        self._by_id: dict[uuid.UUID, User] = {}
        self._raise_integrity_on_create = raise_integrity_on_create

    async def get_by_email(self, email: str) -> User | None:
        return self._by_email.get(email)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._by_id.get(user_id)

    async def create(self, *, email: str, password_hash: str) -> User:
        if self._raise_integrity_on_create:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        user = User(email=email, password_hash=password_hash)
        user.id = uuid.uuid4()
        user.is_active = True
        self._by_email[email] = user
        self._by_id[user.id] = user
        return user

    def add_active(self, user: User) -> None:
        self._by_email[user.email] = user
        self._by_id[user.id] = user


async def test_register_translates_integrity_error_to_domain_error() -> None:
    # The pre-check passes (empty repo) but the insert races and the constraint
    # fires; the service must surface the same domain error as a plain duplicate.
    service = AuthService(FakeUserRepository(raise_integrity_on_create=True), _settings())  # type: ignore[arg-type]
    with pytest.raises(EmailAlreadyExistsError):
        await service.register("racer@example.com", "a proper long passphrase")


async def test_authenticate_rejects_a_deactivated_user() -> None:
    repo = FakeUserRepository()
    user = User(email="inactive@example.com", password_hash=hash_password("a long passphrase here"))
    user.id = uuid.uuid4()
    user.is_active = False
    repo.add_active(user)
    service = AuthService(repo, _settings())  # type: ignore[arg-type]
    # Correct password, but the account is deactivated: still the generic error.
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate("inactive@example.com", "a long passphrase here")


async def test_get_user_rejects_a_deactivated_subject() -> None:
    repo = FakeUserRepository()
    user = User(email="ghost@example.com", password_hash="h")
    user.id = uuid.uuid4()
    user.is_active = False
    repo.add_active(user)
    service = AuthService(repo, _settings())  # type: ignore[arg-type]
    # A token minted while active must stop working once the user is deactivated.
    with pytest.raises(InvalidCredentialsError):
        await service.get_user(user.id)


async def test_get_user_rejects_an_unknown_subject() -> None:
    service = AuthService(FakeUserRepository(), _settings())  # type: ignore[arg-type]
    with pytest.raises(InvalidCredentialsError):
        await service.get_user(uuid.uuid4())


async def test_successful_register_then_authenticate_issues_a_usable_token() -> None:
    settings = _settings()
    service = AuthService(FakeUserRepository(), settings)  # type: ignore[arg-type]
    user = await service.register("full@example.com", "a proper long passphrase")
    token = await service.authenticate("full@example.com", "a proper long passphrase")
    assert decode_access_token(token, settings) == user.id
