"""Library path validation against the configured media mount."""

import os
from pathlib import Path

from app.core.config import Settings
from app.core.errors import ValidationError


def validate_library_paths(settings: Settings, raw_paths: list[str]) -> list[str]:
    """Validate and normalize library root paths.

    A root must be an existing, readable directory that resolves inside the
    configured media mount. Resolving symlinks before the containment check
    prevents a link inside the library from escaping to arbitrary host paths.
    """
    if not raw_paths:
        raise ValidationError("A library needs at least one root path", code="invalid_library_path")

    media_root = settings.media_dir.resolve()
    normalized: list[str] = []

    for raw in dict.fromkeys(raw_paths):
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise ValidationError(
                f"Library path must be absolute: {raw}",
                code="invalid_library_path",
                details={"path": raw},
            )

        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValidationError(
                f"Library path does not exist: {raw}",
                code="invalid_library_path",
                details={"path": raw},
            ) from None

        if not resolved.is_dir():
            raise ValidationError(
                f"Library path is not a directory: {raw}",
                code="invalid_library_path",
                details={"path": raw},
            )

        if not resolved.is_relative_to(media_root):
            raise ValidationError(
                f"Library path is outside the media mount {media_root}: {raw}",
                code="invalid_library_path",
                details={"path": raw},
            )

        if not os.access(resolved, os.R_OK | os.X_OK):
            raise ValidationError(
                f"Library path is not readable: {raw}",
                code="invalid_library_path",
                details={"path": raw},
            )

        normalized.append(str(resolved))

    return normalized
