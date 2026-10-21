"""Password hashing and username validation tests."""

import pytest

from app.core.config import Settings
from app.core.errors import ValidationError
from app.core.passwords import (
    hash_password,
    needs_rehash,
    normalize_username,
    validate_password,
    validate_username,
    verify_password,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(env="testing", log_level="WARNING", password_min_length=8)


def test_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert first.startswith("$argon2id$")
    assert "correct horse" not in first
    assert verify_password(first, "correct horse battery staple") is True
    assert verify_password(first, "wrong") is False


def test_malformed_hash_fails_closed() -> None:
    assert verify_password("not-a-hash", "anything") is False
    assert verify_password("", "anything") is False


def test_needs_rehash_for_unknown_or_weak_hash() -> None:
    assert needs_rehash("not-a-hash") is True
    assert needs_rehash(hash_password("a-long-enough-password")) is False


def test_password_policy_enforces_minimum(settings: Settings) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_password("short", settings)

    assert excinfo.value.code == "weak_password"
    validate_password("12345678", settings)


def test_password_policy_bounds_hasher_input(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        validate_password("x" * 1025, settings)


def test_username_normalization_and_validation() -> None:
    assert normalize_username("  Alice ") == "alice"
    assert validate_username("Alice_01") == "alice_01"


@pytest.mark.parametrize("username", ["ab", "", "has space", "bad/slash", "-leading", "ümlaut"])
def test_invalid_usernames_are_rejected(username: str) -> None:
    with pytest.raises(ValidationError):
        validate_username(username)
