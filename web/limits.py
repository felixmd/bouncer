"""Rate limiting for the one route a stranger can reach.

Spec §9: if the wall is ever exposed through a tunnel, the API key is sitting on
the presenter's laptop and `/test` is an open text box pointed at a paid API.
One comment costs about $0.000025, so this is about abuse rather than about the
cost of honest use — but $0.000025 times an afternoon of someone's script is a
different number.

Deliberately in-process and in-memory. There is no database (PRD §8), the demo
runs from one laptop, and a limiter that needs infrastructure would not get
turned on.
"""

import time
from collections import defaultdict, deque

import config


class RateLimiter:
    """A per-IP sliding window plus a hard total for the process run."""

    def __init__(
        self,
        per_minute: int = config.TEST_PER_IP_PER_MINUTE,
        total_cap: int = config.TEST_TOTAL_CAP,
    ) -> None:
        self.per_minute = per_minute
        self.total_cap = total_cap
        self.total = 0
        self._seen: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client: str, now: float | None = None) -> str | None:
        """Returns None to allow, or a message to show the user.

        The message is deliberately plain rather than an HTTP error: this is a
        demo in front of a room, and "you are going a bit fast" reads better on
        a card than a 429.
        """
        now = time.monotonic() if now is None else now
        if self.total >= self.total_cap:
            return (
                "This demo has taken all the test comments it is going to take. "
                "Restart it to reset the cap."
            )

        window = self._seen[client]
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self.per_minute:
            wait = 60.0 - (now - window[0])
            return f"One at a time — try again in {wait:.0f}s."

        window.append(now)
        self.total += 1
        return None

    @property
    def remaining(self) -> int:
        return max(0, self.total_cap - self.total)
