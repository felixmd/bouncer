"""Groups comments from the same thread into request-sized batches.

Two rules, and the second is the one that bites.

**Same thread per batch.** All comments in a request share one `state`. Mixing
threads would put one thread's title in front of another thread's comments.

**Flush on a timeout as well as on a full batch.** Without it the pipeline
stalls at low drip rates — at 5 items/sec a batch of 15 takes three seconds to
fill, and the demo looks frozen with the counters stopped. The timeout is what
makes the drip-rate slider usable across its whole range rather than only at the
top.
"""

import asyncio
import time
from collections.abc import AsyncIterator

import config
from feed.replay import Comment


class Batcher:
    """Drains an input queue into same-thread batches."""

    def __init__(
        self,
        size: int = config.BATCH_SIZE,
        timeout_s: float = config.BATCH_TIMEOUT_S,
    ) -> None:
        self.size = size
        self.timeout_s = timeout_s
        self.batches = 0
        self.partial = 0  # flushed on the timer rather than when full

    async def batches_from(self, source: asyncio.Queue) -> AsyncIterator[list[Comment]]:
        pending: dict[str, list[Comment]] = {}
        oldest: dict[str, float] = {}

        while True:
            try:
                comment = await asyncio.wait_for(source.get(), timeout=self.timeout_s)
            except TimeoutError:
                # Nothing arriving. Flush whatever is held rather than sitting
                # on it — a held comment is an invisible comment.
                for thread_id in list(pending):
                    yield self._take(pending, oldest, thread_id, full=False)
                continue

            group = pending.setdefault(comment.thread_id, [])
            oldest.setdefault(comment.thread_id, time.monotonic())
            group.append(comment)

            if len(group) >= self.size:
                yield self._take(pending, oldest, comment.thread_id, full=True)
                continue

            # A slow thread must not be held hostage by a fast one sharing the
            # same queue, so age is checked per thread on every arrival.
            now = time.monotonic()
            for thread_id in list(pending):
                if now - oldest[thread_id] >= self.timeout_s:
                    yield self._take(pending, oldest, thread_id, full=False)

    def _take(
        self,
        pending: dict[str, list[Comment]],
        oldest: dict[str, float],
        thread_id: str,
        *,
        full: bool,
    ) -> list[Comment]:
        batch = pending.pop(thread_id)
        oldest.pop(thread_id, None)
        self.batches += 1
        if not full:
            self.partial += 1
        return batch
