"""Rate limiting on /test — the one route a stranger can reach. Spec §9.

Pure logic, so the clock is injected rather than slept through.
"""

from web.limits import RateLimiter


def test_requests_under_the_limit_are_allowed():
    limiter = RateLimiter(per_minute=3, total_cap=100)
    assert [limiter.check("1.1.1.1", now=t) for t in (0.0, 1.0, 2.0)] == [None] * 3


def test_the_fourth_request_in_a_minute_is_held():
    limiter = RateLimiter(per_minute=3, total_cap=100)
    for t in (0.0, 1.0, 2.0):
        limiter.check("1.1.1.1", now=t)
    held = limiter.check("1.1.1.1", now=3.0)
    assert held is not None
    assert "try again in 57s" in held


def test_the_window_slides_rather_than_resetting_on_the_minute():
    limiter = RateLimiter(per_minute=2, total_cap=100)
    limiter.check("1.1.1.1", now=0.0)
    limiter.check("1.1.1.1", now=30.0)
    assert limiter.check("1.1.1.1", now=45.0) is not None
    # The first request has aged out by 61s, so one slot is free again.
    assert limiter.check("1.1.1.1", now=61.0) is None


def test_clients_are_limited_independently():
    """One person hammering it must not lock out the room."""
    limiter = RateLimiter(per_minute=1, total_cap=100)
    assert limiter.check("1.1.1.1", now=0.0) is None
    assert limiter.check("1.1.1.1", now=1.0) is not None
    assert limiter.check("2.2.2.2", now=1.0) is None


def test_the_total_cap_stops_everyone():
    """The per-IP window is no defence against a botnet or a shared NAT, so
    there is a hard ceiling for the process run."""
    limiter = RateLimiter(per_minute=100, total_cap=3)
    for i in range(3):
        assert limiter.check(f"{i}.0.0.1", now=float(i)) is None
    refused = limiter.check("9.9.9.9", now=4.0)
    assert refused is not None
    assert "all the test comments" in refused
    assert limiter.remaining == 0


def test_a_held_request_does_not_consume_budget():
    """Otherwise someone retrying in a loop would burn the total cap without
    ever getting an answer."""
    limiter = RateLimiter(per_minute=1, total_cap=10)
    limiter.check("1.1.1.1", now=0.0)
    for _ in range(5):
        limiter.check("1.1.1.1", now=1.0)
    assert limiter.total == 1
    assert limiter.remaining == 9
