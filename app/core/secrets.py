"""Persistent secret loading and purpose-specific key derivation."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings

_SECRET_FILE = "secret_key"


def load_or_create_secret(settings: Settings) -> str:
    """Return the configured secret or a generated, persisted one.

    A missing ``HEARTH_SECRET_KEY`` is not an error on a home server: a random
    secret is created once in the data directory with mode 0600. Operators who
    run multiple nodes or rotate keys should set the environment variable.
    """
    if settings.secret_key:
        return settings.secret_key

    path = settings.data_dir / _SECRET_FILE
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return value


def derive_key(secret: str, purpose: str) -> bytes:
    """Derive an independent key for a purpose using HMAC-SHA256."""
    return hmac.new(secret.encode("utf-8"), purpose.encode("utf-8"), hashlib.sha256).digest()


@dataclass(frozen=True)
class Keys:
    """Derived signing keys for the two token families."""

    jwt: bytes
    media: bytes


def load_keys(settings: Settings) -> Keys:
    """Load (and if necessary create) the key set for this installation."""
    secret = load_or_create_secret(settings)
    return Keys(
        jwt=derive_key(secret, "hearth/jwt/v1"),
        media=derive_key(secret, "hearth/media/v1"),
    )


def secret_file_path(settings: Settings) -> Path:
    """Expose the secret path for diagnostics without revealing the secret."""
    return settings.data_dir / _SECRET_FILE
