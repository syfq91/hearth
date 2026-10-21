"""Unit tests for the sliding-window limiter."""

import pytest

from app.core.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_limiter_allows_until_limit_then_reports_retry() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=3, window_seconds=10, clock=clock)

    assert limiter.retry_after("ip:1") == 0
    limiter.hit("ip:1")
    limiter.hit("ip:1")
    assert limiter.retry_after("ip:1") == 0
    limiter.hit("ip:1")

    assert limiter.retry_after("ip:1") == pytest.approx(10)

    clock.advance(4)
    assert limiter.retry_after("ip:1") == pytest.approx(6)

    clock.advance(6)
    assert limiter.retry_after("ip:1") == 0


def test_keys_are_independent_and_reset_clears_one() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=30, clock=clock)
    limiter.hit("ip:1")
    limiter.hit("user:alice")

    assert limiter.retry_after("ip:1") > 0
    assert limiter.retry_after("user:alice") > 0
    assert limiter.retry_after("user:bob") == 0

    limiter.reset("user:alice")
    assert limiter.retry_after("user:alice") == 0
    assert limiter.retry_after("ip:1") > 0


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(limit=0, window_seconds=10)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(limit=1, window_seconds=0)
