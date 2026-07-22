"""Request and response schemas for authentication.

These types are the API contract. Two of them are the enforcement points for
security properties: ``UserRegister`` is where the password policy lives, and
``UserRead`` is where the absence of a password field makes hash leakage
structurally impossible rather than merely avoided by discipline.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# Passphrase-friendly policy: length is the only rule. NIST guidance favours
# length over composition, and composition rules mostly push users toward
# predictable substitutions. The lower bound is deliberately high for a
# passphrase; the upper bound exists to bound request size and hashing cost,
# not for security of the password itself.
_MIN_PASSWORD_LENGTH = 12
_MAX_PASSWORD_LENGTH = 128


class UserRegister(BaseModel):
    """Registration request.

    Attributes:
        email: Any RFC-valid address. Normalisation to lowercase happens in the
            service, not here, so the raw value is preserved for validation.
        password: A passphrase. Spaces and Unicode are allowed; the only
            constraints are length bounds, checked before the value is ever
            handed to the hasher.

    """

    email: EmailStr
    password: str = Field(min_length=_MIN_PASSWORD_LENGTH, max_length=_MAX_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def _reject_whitespace_only(cls, value: str) -> str:
        """Reject a password that is only whitespace.

        Length bounds allow spaces so that passphrases work, but a value that
        is *entirely* spaces is a mistake, not a passphrase. This is the one
        content rule, and it exists to catch an accident rather than to impose
        composition requirements.
        """
        if not value.strip():
            raise ValueError("password must not be entirely whitespace")
        return value


class UserLogin(BaseModel):
    """Login request.

    No length validation here on purpose. Rejecting a login for a
    policy-violating password would tell an attacker that the stored password
    is short, and legacy passwords predating a policy change must still be able
    to authenticate. Login validates by verifying the hash, nothing more.
    """

    email: EmailStr
    password: str


class UserRead(BaseModel):
    """Public representation of a user.

    Deliberately has no ``password_hash`` field. Because responses are built by
    validating a ``User`` ORM instance against this model, a field that does
    not exist here cannot appear in a response, whatever a caller does upstream.
    This is enforcement by construction, not by remembering to strip a field.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    """Access-token response, following the OAuth2 bearer convention.

    Attributes:
        access_token: The signed JWT.
        token_type: Always ``bearer``. Present because the OAuth2 conventions
            and most HTTP clients expect it, so tooling can consume the
            response without special-casing.

    """

    access_token: str
    token_type: str = "bearer"
