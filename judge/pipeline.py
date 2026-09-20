"""The judge pipeline, wired.

    uv run --env-file .env python -m judge.pipeline            # console stream
    uv run --env-file .env python -m judge.pipeline --rate 300 --seconds 30

    replay ──> in_queue ──> batcher ──> N workers ──> out_queue ──> consumer
     (disk)   (bounded)   (same thread)  (bucket +    (verdicts)   (12Hz tick)
                                          semaphore)

**Workers never touch the consumer.** They write verdicts to `out_queue` and
stop; something else drains that queue on a fixed tick. That separation is
invariant 1 and it is the whole reason this is split into two halves — coupling
them means one WebSocket frame per classification, hundreds of DOM swaps a
second, and a dead page inside two minutes. The console consumer below drains on
the same 12Hz tick the browser will, so the shape is exercised before any HTML
exists.
"""

import argparse
import asyncio
import contextlib
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace

import config
from feed.replay import Comment, Replay, ThreadStore
from judge.batcher import Batcher
from judge.client import JudgeClient
from judge.lanes import DEFAULT_POLICY, Lane, Policy, decision_confidence, lane
from judge.rubric import DEFAULT_RUBRIC, Rubric, normalise


@dataclass
class Verdict:
    comment: Comment
    lane: Lane
    scores: dict[str, float] = field(default_factory=dict)
    confidences: dict[str, float] = field(default_factory=dict)
    probabilities: dict[str, dict[int, float]] = field(default_factory=dict)
    gate_confidence: float = 0.0
    error: str | None = None

    @property
    def least_sure_axis(self) -> str | None:
        """Which axis put this in the Pen. The Pen is unreadable without it."""
        if not self.confidences:
            return None
        return min(self.confidences, key=lambda axis: self.confidences[axis])


@dataclass
class RejudgeReport:
    """What a rubric edit actually did — paired, on the same comments."""

    rubric: Rubric
    verdicts: list[Verdict] = field(default_factory=list)
    n: int = 0
    moved: int = 0
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0
    cost: float = 0.0

    def delta(self, axis: str) -> float:
        return self.after.get(axis, 0.0) - self.before.get(axis, 0.0)

    @property
    def moved_share(self) -> float:
        return self.moved / self.n if self.n else 0.0


class Backlog:
    """The last N verdicts, for the rubric-edit re-sort (spec §6).

    Verdicts only — raw text already lives in the `ThreadStore`, so re-judging
    never touches disk.
    """

    def __init__(self, size: int = config.BACKLOG_SIZE) -> None:
        self._items: deque[Verdict] = deque(maxlen=size)

    def add(self, verdict: Verdict) -> None:
        self._items.append(verdict)

    def recent(self, count: int) -> list[Verdict]:
        return list(self._items)[-count:]

    def get(self, comment_id: str) -> Verdict | None:
        """Look one up so a human's Allow/Bounce can re-render its card.

        Searched newest-first: a comment a human is looking at right now is at
        the recent end, and the deque is only 2,000 long.
        """
        for verdict in reversed(self._items):
            if verdict.comment is not None and verdict.comment.id == comment_id:
                return verdict
        return None

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class Counters:
    judged: int = 0
    errored: int = 0
    decided: int = 0  # penned comments a human resolved
    lanes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(Lane, 0))
    started_at: float = field(default_factory=time.monotonic)

    def record(self, verdict: Verdict) -> None:
        self.judged += 1
        self.lanes[verdict.lane] = self.lanes.get(verdict.lane, 0) + 1
        if verdict.error:
            self.errored += 1

    @property
    def elapsed(self) -> float:
        return max(time.monotonic() - self.started_at, 1e-9)

    @property
    def per_second(self) -> float:
        return self.judged / self.elapsed


class Pipeline:
    """Owns the queues and the tasks. Start it, drain `out_queue`, stop it."""

    def __init__(
        self,
        store: ThreadStore,
        rubric: Rubric = DEFAULT_RUBRIC,
        rate: float = config.DRIP_RATE_PER_SECOND,
        workers: int = config.PIPELINE_WORKERS,
    ) -> None:
        self.store = store
        self.rubric = rubric
        # Mutable at runtime from the UI. Changing it re-sorts the backlog with
        # no model calls, because scores and probabilities are already stored.
        self.policy = DEFAULT_POLICY
        # Set by default so the console runner streams. web/app.py clears it
        # while no browser is connected — see Replay.demand.
        self.demand = asyncio.Event()
        self.demand.set()
        self.replay = Replay(store, rate=rate, demand=self.demand)
        self.batcher = Batcher()
        self.client = JudgeClient()
        self.in_queue: asyncio.Queue[Comment] = asyncio.Queue(config.IN_QUEUE_MAX)
        self.out_queue: asyncio.Queue[Verdict] = asyncio.Queue(config.OUT_QUEUE_MAX)
        self.backlog = Backlog()
        self.counters = Counters()
        # Human decisions waiting to go out on the next frame. They travel the
        # same channel as everything else on purpose: two channels mutating the
        # same DOM subtree race, and the busy lane loses. See FINDINGS §20.
        self.decisions: deque[tuple[str, Verdict]] = deque()
        self._worker_count = workers
        self._tasks: list[asyncio.Task] = []
        self._batch_queue: asyncio.Queue[list[Comment]] = asyncio.Queue(config.IN_QUEUE_MAX)

    # --- the three stages --------------------------------------------------

    async def _feed_batches(self) -> None:
        async for batch in self.batcher.batches_from(self.in_queue):
            await self._batch_queue.put(batch)

    async def _worker(self) -> None:
        while True:
            batch = await self._batch_queue.get()
            for verdict in await self.judge(batch):
                self.backlog.add(verdict)
                await self.out_queue.put(verdict)

    async def judge(self, batch: list[Comment]) -> list[Verdict]:
        """One request, one batch. Also used by the rubric-edit re-sort."""
        thread = self.store.by_id[batch[0].thread_id]
        questions: dict = {}
        for comment in batch:
            questions |= self.rubric.questions_for(
                comment.id, comment.body, comment.parent_snippet
            )

        response, _, error = await self.client.ask(thread.state(), questions)
        if response is None:
            # Spec §3.4: a comment we could not judge is a comment a human
            # looks at. Degrading into the Pen is the correct failure here.
            return [Verdict(comment=c, lane=Lane.PEN, error=error) for c in batch]

        verdicts = []
        for comment in batch:
            scores, confidences, probabilities = {}, {}, {}
            for axis in self.rubric.keys:
                answer = response.scores.get(f"{comment.id}_{axis}")
                if answer is None:
                    break
                scores[axis] = normalise(answer.score)
                confidences[axis] = answer.confidence
                probabilities[axis] = dict(answer.probabilities)
            if len(scores) < len(self.rubric.keys):
                verdicts.append(
                    Verdict(comment=comment, lane=Lane.PEN, error="missing answers")
                )
                continue
            verdicts.append(
                Verdict(
                    comment=comment,
                    lane=lane(scores, confidences, probabilities, policy=self.policy),
                    scores=scores,
                    confidences=confidences,
                    probabilities=probabilities,
                    gate_confidence=decision_confidence(
                        scores, probabilities, self.policy
                    ),
                )
            )
        return verdicts

    async def judge_one(self, text: str, context: str = "") -> Verdict:
        """Judge an ad-hoc comment — the same gate, the same rubric, one request.

        Not added to the backlog or to any lane: it belongs to no thread, and
        putting it in the stream would misrepresent the thread's lane mix.

        `context` is what `on_topic` is scored against. A typed comment has no
        parent, and scoring `on_topic` against nothing is a documented cause of
        low confidence (FINDINGS §9) — it would pen the comment for a reason
        that has nothing to do with what was typed.
        """
        referent = context.strip() or config.TEST_DEFAULT_CONTEXT
        comment = Comment(
            id="test", thread_id="test", body=text.strip(),
            parent_snippet=referent, author_hash="you", depth=0, score=0,
        )
        questions = self.rubric.questions_for(comment.id, comment.body, referent)
        response, _, error = await self.client.ask({"thread_title": referent}, questions)
        if response is None:
            return Verdict(comment=comment, lane=Lane.PEN, error=error)

        scores, confidences, probabilities = {}, {}, {}
        for axis in self.rubric.keys:
            answer = response.scores.get(f"{comment.id}_{axis}")
            if answer is None:
                return Verdict(comment=comment, lane=Lane.PEN, error="missing answers")
            scores[axis] = normalise(answer.score)
            confidences[axis] = answer.confidence
            probabilities[axis] = dict(answer.probabilities)

        return Verdict(
            comment=comment,
            lane=lane(scores, confidences, probabilities, policy=self.policy),
            scores=scores,
            confidences=confidences,
            probabilities=probabilities,
            gate_confidence=decision_confidence(scores, probabilities, self.policy),
        )

    async def rejudge(self, rubric: Rubric, window: int | None = None) -> "RejudgeReport":
        """Swap the rubric and re-score the retained backlog. Spec §6.

        **This is the expensive interaction.** Thresholds are a free recompute
        (`retune`); level *wording* changes the question, so every comment has
        to go back to the model. Raw text is already in memory, so nothing
        touches disk — but the requests are real.

        Reports per-axis confidence **before and after on the same comments**,
        paired. That is deliberate: `FINDINGS.md` §11 found the v1→v2 rewrite
        was a trade rather than an improvement, so "watch it get better" is not
        a claim this can make. "Watch the confidence on the axis you edited
        move, and watch what it costs you elsewhere" is both truer and more
        interesting.
        """
        window = window or config.REJUDGE_WINDOW
        targets = [
            v for v in self.backlog.recent(window)
            if not v.error and v.confidences and v.comment is not None
        ]
        if not targets:
            return RejudgeReport(rubric=rubric)

        before_lanes = {v.comment.id: v.lane for v in targets}
        before_conf = {
            axis: statistics.mean(v.confidences[axis] for v in targets)
            for axis in self.rubric.keys
        }
        spend_before = self.client.stats.cost_usd
        started = time.monotonic()

        self.rubric = rubric
        by_thread: dict[str, list[Comment]] = defaultdict(list)
        for verdict in targets:
            by_thread[verdict.comment.thread_id].append(verdict.comment)

        batches = [
            group[i:i + config.BATCH_SIZE]
            for group in by_thread.values()
            for i in range(0, len(group), config.BATCH_SIZE)
        ]
        fresh: dict[str, Verdict] = {}
        for part in await asyncio.gather(*(self.judge(b) for b in batches)):
            for verdict in part:
                fresh[verdict.comment.id] = verdict

        # Update in place so the backlog, the DOM ids and the Pen's buttons all
        # keep referring to the same comments.
        moved, rescored = 0, []
        for verdict in targets:
            new = fresh.get(verdict.comment.id)
            if new is None or new.error:
                continue
            if new.lane is not before_lanes[verdict.comment.id]:
                moved += 1
            verdict.lane = new.lane
            verdict.scores = new.scores
            verdict.confidences = new.confidences
            verdict.probabilities = new.probabilities
            verdict.gate_confidence = new.gate_confidence
            rescored.append(verdict)

        after_conf = {
            axis: statistics.mean(v.confidences[axis] for v in rescored)
            for axis in rubric.keys
        } if rescored else {}

        return RejudgeReport(
            rubric=rubric,
            verdicts=rescored,
            n=len(rescored),
            moved=moved,
            before=before_conf,
            after=after_conf,
            seconds=time.monotonic() - started,
            cost=self.client.stats.cost_usd - spend_before,
        )

    def retune(self, policy: Policy) -> list[Verdict]:
        """Apply a new policy and re-sort the backlog. **No model calls.**

        This is the cheapest interesting thing in the demo: the scores and the
        per-level probabilities are already stored, so moving a threshold or
        switching the gate is a pure recompute over verdicts we have. A viewer
        can drag the floor and watch the whole wall re-sort for nothing.

        Contrast `judge/rubric.py`: editing *level wording* changes the question
        and does need re-judging (spec §6). Thresholds do not.
        """
        self.policy = policy
        out = []
        for verdict in self.backlog.recent(config.REJUDGE_WINDOW):
            if verdict.error or not verdict.confidences:
                continue
            verdict.lane = lane(
                verdict.scores, verdict.confidences, verdict.probabilities,
                policy=policy,
            )
            verdict.gate_confidence = decision_confidence(
                verdict.scores, verdict.probabilities, policy
            )
            out.append(verdict)
        return out

    @property
    def auto_handled(self) -> float:
        """Share of the retained backlog the model handled without a human.

        PRD §6.1 argues this should be a live number beside the threshold rather
        than a hard-coded claim, so that a viewer can move the control and watch
        the trade instead of being told about it.
        """
        recent = [
            v for v in self.backlog.recent(config.REJUDGE_WINDOW) if not v.error
        ]
        if not recent:
            return 0.0
        return sum(1 for v in recent if v.lane is not Lane.PEN) / len(recent)

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.replay.run(self.in_queue), name="replay"),
            asyncio.create_task(self._feed_batches(), name="batcher"),
            *(
                asyncio.create_task(self._worker(), name=f"worker-{i}")
                for i in range(self._worker_count)
            ),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.aclose()

    def drain(self) -> list[Verdict]:
        """Everything judged since the last tick. **Never call this per
        classification** — that is invariant 1."""
        out = []
        while True:
            try:
                out.append(self.out_queue.get_nowait())
            except asyncio.QueueEmpty:
                return out

    def drain_decisions(self) -> list[tuple[str, Verdict]]:
        """(dom id to remove, the resolved verdict) for each human decision."""
        out = list(self.decisions)
        self.decisions.clear()
        return out

    def decide(self, comment_id: str, lane: Lane) -> bool:
        """Record a human's Allow/Bounce. Renders on the next frame.

        Nothing persists — PRD §8 has no accounts and no storage. The visible
        effect is the point: the Pen *drains* as people work it.
        """
        verdict = self.backlog.get(comment_id)
        if verdict is None:
            return False
        self.counters.decided += 1
        self.decisions.append((comment_id, replace(verdict, lane=lane)))
        return True


# --- console runner: the build-order step before any HTML ------------------

BAR = 10


def _bar(value: float) -> str:
    filled = round(value / config.SCORE_SCALE_MAX * BAR)
    return "#" * filled + "." * (BAR - filled)


async def _console(pipeline: Pipeline, seconds: float, show: int) -> None:
    tick = 1.0 / config.RENDER_TICK_HZ
    deadline = time.monotonic() + seconds
    last_report = 0.0
    errors: dict[str, int] = {}

    await pipeline.start()
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(tick)
            batch = pipeline.drain()  # one drain per tick, not per verdict
            for verdict in batch:
                pipeline.counters.record(verdict)
                if verdict.error:
                    errors[verdict.error] = errors.get(verdict.error, 0) + 1

            now = time.monotonic()
            if now - last_report < 1.0:
                continue
            last_report = now

            counters, stats = pipeline.counters, pipeline.client.stats
            lanes = "  ".join(
                f"{value.value[:4]} {counters.lanes.get(value, 0):>5}" for value in Lane
            )
            print(
                f"\n[{counters.elapsed:5.1f}s] {counters.per_second:6.1f}/s  {lanes}  "
                f"req {stats.requests:>4}  err {stats.errors}  "
                f"${stats.cost_usd:.4f}  in_q {pipeline.in_queue.qsize():>3}  "
                f"backlog {len(pipeline.backlog)}"
            )
            for verdict in batch[:show]:
                if verdict.error:
                    print(f"   {verdict.lane.value:<8} ERROR {verdict.error[:60]}")
                    continue
                bars = " ".join(_bar(verdict.scores[a]) for a in pipeline.rubric.keys)
                head = verdict.comment.body[:46].replace("\n", " ")
                print(f"   {verdict.lane.value:<8} {bars} {verdict.gate_confidence:.2f}  {head}")
    finally:
        await pipeline.stop()

    counters = pipeline.counters
    print(f"\n{'=' * 78}")
    print(f"  judged {counters.judged} in {counters.elapsed:.1f}s "
          f"= {counters.per_second:.1f}/sec")
    for value in Lane:
        n = counters.lanes.get(value, 0)
        share = n / counters.judged if counters.judged else 0
        print(f"  {value.value:<9} {n:>6}  ({share:.0%})")
    print(f"  batches {pipeline.batcher.batches} "
          f"({pipeline.batcher.partial} flushed on the timer)")
    print(f"  requests {pipeline.client.stats.requests}, "
          f"errors {pipeline.client.stats.errors}, "
          f"{pipeline.client.stats.input_tokens:,} tokens, "
          f"${pipeline.client.stats.cost_usd:.4f}")
    if pipeline.client.stats.latencies_ms:
        latencies = sorted(pipeline.client.stats.latencies_ms)
        print(f"  latency p50 {latencies[len(latencies) // 2]:.0f}ms  "
              f"p95 {latencies[int(len(latencies) * 0.95)]:.0f}ms")

    tokens_per_comment = (
        pipeline.client.stats.input_tokens / counters.judged if counters.judged else 0
    )
    tokens_per_second = pipeline.client.stats.input_tokens / counters.elapsed
    print(f"  {tokens_per_comment:.0f} tokens/comment, "
          f"{tokens_per_second:,.0f} tokens/sec "
          f"({tokens_per_second / 250_000:.0%} of the published 250K ceiling)")
    print(f"  {pipeline.client.stats.requests / counters.elapsed:.1f} req/sec "
          f"of the 20/sec budget")
    if errors:
        print("  errors:")
        for kind, count in sorted(errors.items(), key=lambda kv: -kv[1])[:4]:
            print(f"    {count:>4}  {kind[:96]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the judge pipeline to the console.")
    parser.add_argument("--rate", type=float, default=config.DRIP_RATE_PER_SECOND)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=config.PIPELINE_WORKERS)
    parser.add_argument("--show", type=int, default=3, help="cards printed per second")
    args = parser.parse_args()

    store = ThreadStore.load()
    print(store.describe())
    print(f"drip {args.rate}/s, B={config.BATCH_SIZE}, {args.workers} workers, "
          f"gate={config.CONFIDENCE_GATE} floor={config.CONFIDENCE_FLOOR}")

    pipeline = Pipeline(store, rate=args.rate, workers=args.workers)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_console(pipeline, args.seconds, args.show))


if __name__ == "__main__":
    main()
