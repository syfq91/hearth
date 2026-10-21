"""Stable partial hashing for rename detection."""

import hashlib
import os
from pathlib import Path

CHUNK_SIZE = 64 * 1024


def partial_hash(path: Path | str, *, chunk_size: int = CHUNK_SIZE) -> str:
    """Hash a file's size plus its first and last ``chunk_size`` bytes.

    Reading both ends catches truncated/append-only changes without hashing a
    multi-gigabyte file on every scan. The size is part of the digest so two
    files with identical edges but different lengths cannot collide.
    """
    target = Path(path)
    size = os.stat(target).st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))

    with target.open("rb") as handle:
        digest.update(handle.read(chunk_size))
        if size > chunk_size:
            handle.seek(max(chunk_size, size - chunk_size))
            digest.update(handle.read(chunk_size))

    return digest.hexdigest()
