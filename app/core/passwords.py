"""Password hashing (Argon2id) and account input validation."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.errors import ValidationError

# OWASP-recommended Argon2id baseline: 19 MiB, 2 iterations, 1 lane.
_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

_USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}")
MAX_PASSWORD_LENGTH = 1024


def hash_password(password: str) -> str:
    """Return an Argon2id hash with a fresh random salt."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return whether ``password`` matches ``password_hash``.

    Malformed hashes are treated as a failed verification rather than an
    internal error so a corrupted row cannot take down the login endpoint.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return whether the stored hash uses weaker parameters than current."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password(password: str, settings: Settings) -> None:
    """Enforce the account password policy, raising a unified API error."""
    if len(password) < settings.password_min_length:
        raise ValidationError(
            f"Password must be at least {settings.password_min_length} characters",
            code="weak_password",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters",
            code="weak_password",
        )


def normalize_username(username: str) -> str:
    """Trim and lowercase a username for storage and lookup."""
    return username.strip().lower()


def validate_username(username: str) -> str:
    """Validate and return a normalized username.

    Stored lowercase so login is case-insensitive and the unique index is
    portable across SQLite and Postgres.
    """
    normalized = normalize_username(username)
    if not _USERNAME_RE.fullmatch(normalized):
        raise ValidationError(
            "Username must be 3-64 characters: lowercase letters, digits, '.', '_' or '-'",
            code="invalid_username",
        )
    return normalized
