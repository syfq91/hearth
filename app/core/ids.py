"""Time-sortable identifier helpers.

The plan requires UUIDv7 (or ULID) identifiers stored as text so rows sort by
creation time. Python 3.12's standard library does not ship UUIDv7, so we
implement the RFC 9562 layout directly: a 48-bit millisecond timestamp, the
version and variant bits, and cryptographically random tail bits.
"""

import secrets
import time
import uuid


def uuid7() -> str:
    """Return a new UUIDv7 as a lowercase hyphenated string."""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def uuid7_timestamp_ms(value: str) -> int:
    """Return the millisecond timestamp embedded in a UUIDv7 string."""
    return uuid.UUID(value).int >> 80


def new_id() -> str:
    """Default column callable for primary keys."""
    return uuid7()
