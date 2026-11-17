"""Password hashing and username validation tests."""

import threading

import pytest

from app.core import passwords
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


async def test_async_password_helpers_run_in_worker_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argon2 helpers must never execute on the event loop thread."""
    loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def fake_hash(password: str) -> str:
        worker_threads.append(threading.get_ident())
        return f"hash:{password}"

    def fake_verify(password_hash: str, password: str) -> bool:
        worker_threads.append(threading.get_ident())
        return password_hash == f"hash:{password}"

    monkeypatch.setattr(passwords, "hash_password", fake_hash)
    monkeypatch.setattr(passwords, "verify_password", fake_verify)

    assert await passwords.hash_password_async("secret") == "hash:secret"
    assert await passwords.verify_password_async("hash:secret", "secret") is True
    assert worker_threads
    assert all(thread_id != loop_thread for thread_id in worker_threads)
