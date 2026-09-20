"""Replay reader and the thread store. No network — invariant 5 in test form."""

import asyncio
import json

import pytest

from feed.replay import Replay, ThreadStore
from judge.lanes import Lane
from judge.pipeline import Backlog, Verdict

THREAD = {
    "thread": {
        "id": "t1", "title": "A thread title", "selftext": "",
        "source": "HackerNews", "origin": "https://example.test",
    },
    "comments": [
        {"id": "c000", "author_hash": "amber-finch", "body": "top level",
         "parent_snippet": None, "depth": 0, "score": 3, "controversiality": 0},
        {"id": "c001", "author_hash": "jade-wren", "body": "a reply",
         "parent_snippet": "top level", "depth": 1, "score": 1, "controversiality": 0},
    ],
}


@pytest.fixture
def store(tmp_path):
    (tmp_path / "t1.json").write_text(json.dumps(THREAD), encoding="utf-8")
    return ThreadStore.load(tmp_path)


def test_loads_threads_from_disk(store):
    assert store.total_comments == 2
    assert store.by_id["t1"].source == "HackerNews"


def test_comment_ids_are_unique_across_threads(store):
    """The per-thread ids on disk collide once several threads are in play, and
    they are also the question keys in a batched request."""
    assert {c.id for c in store.by_id["t1"].comments} == {"t1:c000", "t1:c001"}


def test_a_top_level_comment_falls_back_to_the_thread_title(store):
    """on_topic is judged against this string. Leaving it None would point the
    levels at nothing — FINDINGS §9."""
    top, reply = store.by_id["t1"].comments
    assert top.parent_snippet == "A thread title"
    assert reply.parent_snippet == "top level"


def test_state_is_small_and_omits_an_empty_selftext(store):
    """Under quoted addressing this is the only thing amortised across a batch."""
    assert store.by_id["t1"].state() == {"thread_title": "A thread title"}


def test_an_empty_directory_fails_loudly(tmp_path):
    with pytest.raises(SystemExit, match="no threads"):
        ThreadStore.load(tmp_path)


async def test_replay_emits_every_comment_and_loops(store):
    queue: asyncio.Queue = asyncio.Queue()
    replay = Replay(store, rate=0, shuffle=False)  # rate 0 = as fast as possible
    task = asyncio.create_task(replay.run(queue))
    seen = [await asyncio.wait_for(queue.get(), timeout=2) for _ in range(5)]
    task.cancel()

    assert [c.id for c in seen[:2]] == ["t1:c000", "t1:c001"]
    assert seen[2].id == "t1:c000"  # looped


async def test_an_unlimited_rate_still_yields_to_the_event_loop(store):
    """`Queue.put` returns without suspending while there is room, so a reader
    that only sleeps when a rate is set will monopolise the loop and starve
    every consumer. This hung the suite before it was fixed."""
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(Replay(store, rate=0, shuffle=False).run(queue))
    await asyncio.sleep(0.05)  # a competing coroutine must get a turn
    task.cancel()
    assert queue.qsize() > 0


async def test_a_bounded_queue_applies_backpressure(store):
    """If the workers cannot keep up the reader slows down, rather than the
    queue growing until the process dies."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    replay = Replay(store, rate=0, shuffle=False)
    task = asyncio.create_task(replay.run(queue))
    await asyncio.sleep(0.05)
    assert queue.qsize() == 2
    assert replay.emitted <= 3  # two queued, one blocked on put
    task.cancel()


def test_backlog_keeps_only_the_most_recent():
    backlog = Backlog(size=3)
    for i in range(10):
        backlog.add(Verdict(comment=None, lane=Lane.APPROVED, gate_confidence=i))
    assert len(backlog) == 3
    assert [v.gate_confidence for v in backlog.recent(3)] == [7, 8, 9]
