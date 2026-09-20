"""Replays recorded verdicts instead of calling the model.

    uv run python -m web.app --offline

Why this exists, in order of how much each reason matters:

1. **The API ran out of credits twice mid-build.** A wall where every card reads
   "could not judge" is not a demo of anything. This is the same mitigation
   invariant 5 already applies to Reddit — pre-bake it and the upstream cannot
   take the demo down — applied to the model.
2. **$0.41 a minute** (FINDINGS §19). An ambient display that runs all day
   costs about two hundred dollars live and nothing from a recording.
3. It makes reading the Pen repeatable. `TASKS.md` 2.3 needs a human to judge
   30 penned comments cold, more than once, and paying for the same verdicts
   each time is silly.

**It fakes at the client boundary, not the pipeline.** `OfflineClient` returns
the same shape `system_one` does, so the batcher, the rubric, the lane policy,
the gate and the render loop are the real ones. That means moving a threshold
still works offline — it is a pure recompute over stored probabilities — and
what is on screen is what the live app would show for those comments.

What it cannot do is answer a *new* question: a rubric edit or a typed comment
needs the model. Both are disabled in offline mode rather than quietly
returning stale numbers, which would be the one dishonest option.
"""

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from judge.client import Stats

RECORDING = Path("data/recorded/verdicts.json")


@dataclass(frozen=True)
class RecordedAnswer:
    """Duck-types `typesafe_sdk.ScoreAnswer` for the fields the pipeline reads."""

    score: float
    confidence: float
    probabilities: dict[int, float]


@dataclass(frozen=True)
class RecordedUsage:
    input_tokens: int = 0


@dataclass(frozen=True)
class RecordedResponse:
    scores: dict[str, RecordedAnswer]
    usage: RecordedUsage = field(default_factory=RecordedUsage)
    model: str = "recorded"


class OfflineClient:
    """Same interface as `JudgeClient`, backed by a file."""

    def __init__(self, path: Path = RECORDING) -> None:
        if not path.exists():
            raise SystemExit(
                f"no recording at {path} — run: uv run python -m feed.bake"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.verdicts = {
            comment_id: {
                axis: RecordedAnswer(
                    score=answer["score"],
                    confidence=answer["confidence"],
                    # JSON keys are strings; the gate reads integer levels.
                    probabilities={int(k): v for k, v in answer["probabilities"].items()},
                )
                for axis, answer in axes.items()
            }
            for comment_id, axes in raw.items()
        }
        # Axis names are needed to parse question keys, and cannot be recovered
        # by splitting: keys are `{comment_id}_{axis}` and `on_topic` contains
        # an underscore, so `rpartition("_")` yields the comment id plus "topic"
        # and nothing ever matches.
        self.axes = {axis for axes in self.verdicts.values() for axis in axes}
        self.stats = Stats()

    def _split(self, key: str) -> tuple[str, str] | None:
        for axis in self.axes:
            suffix = "_" + axis
            if key.endswith(suffix):
                return key[: -len(suffix)], axis
        return None

    @property
    def comment_ids(self) -> set[str]:
        return set(self.verdicts)

    async def ask(self, state: object, questions: dict) -> tuple:
        """Answer from the recording. Never touches the network.

        A question about a comment with no recorded verdict is left unanswered,
        which the pipeline already handles by routing to the Pen — the same path
        a real missing answer takes. In practice the replay is restricted to
        recorded comments, so this is a guard rather than a code path.
        """
        answers: dict[str, RecordedAnswer] = {}
        for key in questions:
            parsed = self._split(key)
            if parsed is None:
                continue
            comment_id, axis = parsed
            recorded = self.verdicts.get(comment_id, {}).get(axis)
            if recorded is not None:
                answers[key] = recorded

        self.stats.requests += 1
        # A plausible latency so the wall moves the way the live one does rather
        # than snapping into place, which would look like a mock.
        latency = random.uniform(0.6, 1.4) * 285
        self.stats.latencies_ms.append(latency)
        return RecordedResponse(scores=answers), latency, None

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "OfflineClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def restrict(store, ids: set[str]) -> int:
    """Drop comments with no recorded verdict from every thread.

    Offline mode shows only real model output, so a comment we never scored is
    not shown at all. Returns how many survive.
    """
    for thread in store.threads:
        thread.comments = [c for c in thread.comments if c.id in ids]
    store.threads = [t for t in store.threads if t.comments]
    store.by_id = {t.id: t for t in store.threads}
    return store.total_comments


BANNER = (
    "Replaying recorded verdicts — no model calls. Every score on screen is a "
    "real answer, captured earlier; thresholds and the gate still work because "
    "they are a pure recompute. Rubric editing and test-your-own-comment need "
    "the model and are disabled."
)
