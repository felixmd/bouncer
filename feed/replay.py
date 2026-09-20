"""Reads pre-baked threads from disk and drips them into the pipeline.

**Reddit, Hacker News and ConvoKit are never called here** — invariant 5. Every
fetcher is a dev-time script that writes `data/threads/*.json`; this module only
reads them. That is what makes the demo independent of any upstream being alive,
makes two runs identical, and means the pile has been eyeballed in advance.

A real thread arrives as one bulk fetch, so the stream timing is synthetic
whatever we do. Better to own that than to pretend otherwise: the drip rate is a
deliberate knob, exposed in the UI.
"""

import asyncio
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import config


@dataclass(frozen=True)
class Comment:
    """One comment, with everything the judge needs and nothing it does not."""

    id: str  # unique across threads, unlike the per-thread id on disk
    thread_id: str
    body: str
    parent_snippet: str | None
    author_hash: str
    depth: int
    score: int

    @property
    def referent(self) -> str | None:
        """What `on_topic` is judged against. Set by the store so a top-level
        comment falls back to the thread title rather than to nothing."""
        return self.parent_snippet


@dataclass
class Thread:
    id: str
    title: str
    selftext: str
    source: str
    comments: list[Comment] = field(default_factory=list)

    def state(self) -> dict:
        """The shared half of a request. Small by design — under `quoted`
        addressing every comment carries its own text and parent in its own
        question, so this is the only thing amortised across a batch."""
        state = {"thread_title": self.title}
        if self.selftext:
            state["thread_body"] = self.selftext[:1200]
        return state


class ThreadStore:
    """Every thread on disk, held in memory.

    Also serves §6's requirement that re-judging never touches disk: the raw
    text is already here, so a rubric edit re-reads from memory.
    """

    def __init__(self, threads: list[Thread]) -> None:
        self.threads = threads
        self.by_id = {thread.id: thread for thread in threads}

    @classmethod
    def load(cls, directory: str | Path = config.THREADS_DIR) -> "ThreadStore":
        paths = sorted(Path(directory).glob("*.json"))
        if not paths:
            raise SystemExit(
                f"no threads in {directory} — run one of the fetchers first, "
                "e.g. uv run python -m feed.hn_fetch --search"
            )
        return cls([cls._read(path) for path in paths])

    @staticmethod
    def _read(path: Path) -> Thread:
        document = json.loads(path.read_text(encoding="utf-8"))
        meta = document["thread"]
        thread = Thread(
            id=meta["id"],
            title=meta.get("title", ""),
            selftext=meta.get("selftext", ""),
            source=meta.get("source") or meta.get("subreddit", ""),
        )
        thread.comments = [
            Comment(
                id=f"{thread.id}:{c['id']}",
                thread_id=thread.id,
                body=c["body"],
                # A top-level comment is replying to the post, so the title is
                # the referent. Leaving this None would point the on_topic
                # levels at nothing — see FINDINGS §9.
                parent_snippet=c.get("parent_snippet") or thread.title,
                author_hash=c.get("author_hash", ""),
                depth=c.get("depth", 0),
                score=c.get("score", 0),
            )
            for c in document["comments"]
        ]
        return thread

    @property
    def total_comments(self) -> int:
        return sum(len(t.comments) for t in self.threads)

    def describe(self) -> str:
        parts = [f"{t.source} ({len(t.comments)})" for t in self.threads]
        return f"{self.total_comments} comments in {len(self.threads)} threads: " + ", ".join(parts)


class Replay:
    """Drips comments into a queue, forever.

    Emits **one thread at a time** rather than interleaving. Batches are formed
    from a single thread so they can share one `state`, and a reader that
    interleaved would hand the batcher a stream it could only cut into fragments.

    The cost is a visible seam when the stream rolls over to the next thread,
    which is a real thing a viewer will notice and a deliberate trade.
    """

    def __init__(self, store: ThreadStore, rate: float = config.DRIP_RATE_PER_SECOND,
                 shuffle: bool = True, demand: asyncio.Event | None = None) -> None:
        self.store = store
        self.rate = rate
        self.shuffle = shuffle
        # When set, the reader only drips while `demand` is set. The web app
        # clears it whenever no browser is connected.
        #
        # This is not an optimisation, it is a cost control. Eight dev servers
        # orphaned by restarts kept judging at ~225 items/sec with nobody
        # watching and drained the account's credits. A judge with no audience
        # should not be spending money. `None` means always-on, which is what
        # the console runner wants.
        self.demand = demand
        self.emitted = 0
        self.laps = 0

    async def run(self, out: asyncio.Queue) -> None:
        while True:
            for thread in self.store.threads:
                comments = list(thread.comments)
                if self.shuffle:
                    # Files are in tree order, which front-loads the top-level
                    # comments and makes the first minute unrepresentative.
                    random.shuffle(comments)
                for comment in comments:
                    if self.demand is not None:
                        await self.demand.wait()
                    # An awaited put is the backpressure: if the workers cannot
                    # keep up, the reader slows down instead of the queue
                    # growing until the process dies.
                    await out.put(comment)
                    self.emitted += 1
                    # Always yield, even at rate 0 ("as fast as the workers can
                    # take them"). `Queue.put` returns without suspending while
                    # there is room, so without this the reader monopolises the
                    # event loop and nothing downstream ever runs.
                    await asyncio.sleep(1.0 / self.rate if self.rate > 0 else 0)
            self.laps += 1
