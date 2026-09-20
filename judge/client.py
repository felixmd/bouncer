"""Jev calls: token bucket, semaphore, error routing.

Rate limiting is explicit rather than a side effect of the semaphore. A
semaphore bounds how many requests are *in flight*; it says nothing about the
rate at which they start. At 150ms latency a semaphore of 32 will happily issue
200 req/s and collect 429s, so the bucket is the thing actually holding the
line and the semaphore is only there to cap memory and socket use.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Question,
    RetryPolicy,
    SystemOneResponse,
    TypeSafeError,
)

import config


class TokenBucket:
    """Classic leaky bucket. `take()` blocks until a request may be issued."""

    def __init__(self, rate_per_second: float, capacity: int) -> None:
        self._rate = rate_per_second
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(deficit)


@dataclass
class Stats:
    requests: int = 0
    errors: int = 0
    input_tokens: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return self.input_tokens / 1_000_000 * config.INPUT_COST_PER_MTOK


class JudgeClient:
    """A rate-limited wrapper around one `system_one` call."""

    def __init__(
        self,
        rate_per_second: float = config.REQUESTS_PER_SECOND,
        max_concurrent: int = config.MAX_CONCURRENT_REQUESTS,
    ) -> None:
        self._client = AsyncTypeSafeClient(
            model=config.JEV_MODEL,
            retry=RetryPolicy(max_retries=config.MAX_RETRIES),
            timeout=config.API_TIMEOUT_S,
        )
        self._bucket = TokenBucket(rate_per_second, config.TOKEN_BUCKET_CAPACITY)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self.stats = Stats()

    async def ask(
        self, state: object, questions: Mapping[str, Question]
    ) -> tuple[SystemOneResponse | None, float, str | None]:
        """Returns (response, latency_ms, error). A failure is never raised at
        the caller — an unjudgeable comment is a comment a human looks at."""
        await self._bucket.take()
        started = time.perf_counter()
        async with self._semaphore:
            try:
                response = await self._client.system_one(state=state, questions=questions)
            except (TimeoutError, TypeSafeError) as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                self.stats.requests += 1
                self.stats.errors += 1
                self.stats.latencies_ms.append(latency_ms)
                return None, latency_ms, f"{type(exc).__name__}: {exc}"

        latency_ms = (time.perf_counter() - started) * 1000
        self.stats.requests += 1
        self.stats.latencies_ms.append(latency_ms)
        self.stats.input_tokens += response.usage.input_tokens or 0
        return response, latency_ms, None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "JudgeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
