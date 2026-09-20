"""Batching rules. No network, no key — the batcher never talks to anything."""

import asyncio

import pytest

from feed.replay import Comment
from judge.batcher import Batcher


def comment(index: int, thread_id: str = "t1") -> Comment:
    return Comment(
        id=f"{thread_id}:c{index:03d}", thread_id=thread_id, body=f"body {index}",
        parent_snippet=None, author_hash="amber-finch", depth=0, score=0,
    )


async def collect(batcher: Batcher, comments: list[Comment], count: int) -> list[list[Comment]]:
    """Push `comments`, then take the first `count` batches the batcher yields."""
    queue: asyncio.Queue = asyncio.Queue()
    for item in comments:
        queue.put_nowait(item)
    out = []
    async for batch in batcher.batches_from(queue):
        out.append(batch)
        if len(out) >= count:
            break
    return out


async def test_a_full_batch_is_emitted_immediately():
    batcher = Batcher(size=3, timeout_s=10)  # timeout long enough to prove it is unused
    batches = await collect(batcher, [comment(i) for i in range(6)], 2)
    assert [len(b) for b in batches] == [3, 3]
    assert batcher.partial == 0


async def test_batches_never_mix_threads():
    """All comments in a request share one state, so a mixed batch would put
    one thread's title in front of another thread's comments."""
    batcher = Batcher(size=2, timeout_s=10)
    interleaved = [comment(0, "a"), comment(0, "b"), comment(1, "a"), comment(1, "b")]
    batches = await collect(batcher, interleaved, 2)
    for batch in batches:
        assert len({c.thread_id for c in batch}) == 1


async def test_a_partial_batch_flushes_on_the_timer():
    """Without this the demo freezes at low drip rates — at 5 items/sec a batch
    of 15 takes three seconds to fill and the counters stop."""
    batcher = Batcher(size=15, timeout_s=0.05)
    batches = await collect(batcher, [comment(i) for i in range(2)], 1)
    assert len(batches[0]) == 2
    assert batcher.partial == 1


async def test_a_slow_thread_is_not_held_hostage_by_a_fast_one():
    """Age is tracked per thread, so a thread that has gone quiet still flushes
    while another keeps filling."""
    batcher = Batcher(size=3, timeout_s=0.05)
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(comment(0, "slow"))
    await asyncio.sleep(0.08)  # "slow" is now older than the timeout
    for i in range(3):
        queue.put_nowait(comment(i, "fast"))

    seen = []
    async for batch in batcher.batches_from(queue):
        seen.append(batch)
        if len(seen) >= 2:
            break
    assert {b[0].thread_id for b in seen} == {"slow", "fast"}


async def test_every_comment_comes_out_exactly_once():
    batcher = Batcher(size=4, timeout_s=0.05)
    comments = [comment(i, f"t{i % 3}") for i in range(20)]
    batches = await collect(batcher, comments, 6)
    ids = [c.id for batch in batches for c in batch]
    assert len(ids) == len(set(ids))
    assert set(ids) <= {c.id for c in comments}


@pytest.mark.parametrize("size", [1, 2, 15])
def test_batch_size_is_never_exceeded(size):
    async def run():
        batcher = Batcher(size=size, timeout_s=10)
        batches = await collect(batcher, [comment(i) for i in range(size * 2)], 2)
        assert all(len(b) <= size for b in batches)

    asyncio.run(run())
