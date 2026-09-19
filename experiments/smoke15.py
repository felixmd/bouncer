"""Does the plan work? Fifteen comments, four axes, one request each.

    uv run --env-file .env python -m experiments.smoke15

This is the cheapest possible check on the core product claim, run before any
of the throughput machinery exists. It answers four questions:

1. Does `ScoreAnswer.confidence` actually vary, or does it sit near 1.0? If it
   does not move, the Pen is empty and the demo has no differentiator.
2. Do the four axes spread, or do they pile at the extremes the way the PRD
   warns Nouls do?
3. Do the lanes match a human prior often enough to be worth showing?
4. What does one comment cost and how long does it take?

Deliberately B=1: this establishes the baseline the batch-size experiment in
TECHNICAL_SPEC §3.2 measures degradation *against*. Do not read a throughput
number off this script — it is 15 sequential requests and nothing more.

Responses are written to experiments/out/smoke15.json so that later unit tests
can replay them without a key.
"""

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, TypeSafeError

import config
from experiments.corpus import COMMENTS, THREADS, Comment
from judge.lanes import Lane, lane
from judge.rubric import DEFAULT_RUBRIC, normalise

OUT_PATH = Path(__file__).parent / "out" / "smoke15.json"


@dataclass
class Result:
    id: str
    subreddit: str
    body: str
    expect: str
    note: str
    lane: str
    scores: dict[str, float]
    raw_scores: dict[str, float]
    confidences: dict[str, float]
    min_confidence: float
    latency_ms: float
    input_tokens: int | None
    model: str
    error: str | None = None


def build_state(comment: Comment) -> dict:
    """Thread context once, then the comment block — the §3.3 shape at B=1."""
    thread = THREADS[comment.thread_id]
    return {
        "thread_title": thread.title,
        "thread_body": thread.selftext[:1200],
        "comments": [
            {
                "id": comment.id,
                "replying_to": comment.parent_snippet,
                "text": comment.body,
            }
        ],
    }


async def judge_one(client: AsyncTypeSafeClient, comment: Comment) -> Result:
    thread = THREADS[comment.thread_id]
    questions = DEFAULT_RUBRIC.questions_for(comment.id)
    started = time.perf_counter()

    try:
        response = await client.system_one(state=build_state(comment), questions=questions)
    except TypeSafeError as exc:
        # The specified failure mode: a comment we could not judge is a comment
        # a human looks at.
        return Result(
            id=comment.id,
            subreddit=thread.subreddit,
            body=comment.body,
            expect=comment.expect.value,
            note=comment.note,
            lane=Lane.PEN.value,
            scores={},
            raw_scores={},
            confidences={},
            min_confidence=0.0,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=None,
            model="",
            error=f"{type(exc).__name__}: {exc}",
        )

    latency_ms = (time.perf_counter() - started) * 1000
    raw_scores, scores, confidences = {}, {}, {}
    for axis in DEFAULT_RUBRIC.keys:
        answer = response.scores[f"{comment.id}_{axis}"]
        raw_scores[axis] = answer.score
        scores[axis] = normalise(answer.score)
        confidences[axis] = answer.confidence

    return Result(
        id=comment.id,
        subreddit=thread.subreddit,
        body=comment.body,
        expect=comment.expect.value,
        note=comment.note,
        lane=lane(scores, confidences).value,
        scores=scores,
        raw_scores=raw_scores,
        confidences=confidences,
        min_confidence=min(confidences.values()),
        latency_ms=latency_ms,
        input_tokens=response.usage.input_tokens,
        model=response.model,
    )


def bar(value: float, width: int = 10) -> str:
    filled = round(value / config.SCORE_SCALE_MAX * width)
    return "#" * filled + "." * (width - filled)


def report(results: list[Result]) -> None:
    ok = [r for r in results if r.error is None]

    print("\n" + "=" * 100)
    print(f"{'id':<4} {'sub':<19} {'host':>5} {'cont':>5} {'subs':>5} {'ontp':>5} "
          f"{'minconf':>8}  {'lane':<9} {'expect':<9} {'ms':>6}")
    print("-" * 100)
    for r in results:
        if r.error:
            print(f"{r.id:<4} {r.subreddit:<19} {'ERROR — routed to Pen: ' + r.error}")
            continue
        flag = " " if r.lane == r.expect else "*"
        print(
            f"{r.id:<4} {r.subreddit:<19} "
            f"{r.scores['hostility']:>5.1f} {r.scores['contempt']:>5.1f} "
            f"{r.scores['substance']:>5.1f} {r.scores['on_topic']:>5.1f} "
            f"{r.min_confidence:>8.3f}  {r.lane:<9} {r.expect:<9} {r.latency_ms:>6.0f} {flag}"
        )

    print("\nfingerprints (normalised 0-10: hostility / contempt / substance / on_topic)")
    print("-" * 100)
    for r in ok:
        head = r.body[:58].replace("\n", " ")
        print(f"{r.id}  {bar(r.scores['hostility'])} {bar(r.scores['contempt'])} "
              f"{bar(r.scores['substance'])} {bar(r.scores['on_topic'])}  {head}")

    if not ok:
        print("\nNo successful calls — nothing to summarise.")
        return

    print("\n" + "=" * 100)
    lanes = [r.lane for r in ok]
    for value in (Lane.APPROVED, Lane.BOUNCED, Lane.PEN):
        n = lanes.count(value.value)
        print(f"  {value.value:<9} {n:>2}/{len(ok)}  ({n / len(ok):.0%})")

    agree = sum(1 for r in ok if r.lane == r.expect)
    print(f"\n  agreement with human prior   {agree}/{len(ok)}  ({agree / len(ok):.0%})")

    confs = sorted(r.min_confidence for r in ok)
    all_confs = sorted(c for r in ok for c in r.confidences.values())
    print(f"  min-confidence range         {confs[0]:.3f} .. {confs[-1]:.3f}")
    print(f"  per-axis confidence range    {all_confs[0]:.3f} .. {all_confs[-1]:.3f}")
    print(f"  per-axis confidence median   {all_confs[len(all_confs) // 2]:.3f}")
    print(f"  confidence floor in use      {config.CONFIDENCE_FLOOR}")

    spread = sorted(v for r in ok for v in r.scores.values())
    at_rails = sum(1 for v in spread if v <= 0.5 or v >= 9.5)
    print(f"  scores at the rails          {at_rails}/{len(spread)} ({at_rails / len(spread):.0%})")

    lat = sorted(r.latency_ms for r in ok)
    print(f"\n  latency  p50 {lat[len(lat) // 2]:.0f}ms   min {lat[0]:.0f}ms   max {lat[-1]:.0f}ms")

    tokens = [r.input_tokens for r in ok if r.input_tokens is not None]
    if tokens:
        total = sum(tokens)
        cost = total / 1_000_000 * config.INPUT_COST_PER_MTOK
        print(f"  tokens   {total} in for {len(ok)} comments "
              f"({total / len(ok):.0f}/comment)  =  ${cost:.6f}")
        print(f"  at 1M comments that is       ${cost / len(ok) * 1_000_000:.2f}")
    print(f"  model    {ok[0].model}")
    print("=" * 100)


async def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit(
            "TYPESAFE_API_KEY is not set. Run with: "
            "uv run --env-file .env python -m experiments.smoke15"
        )

    async with AsyncTypeSafeClient(
        retry=RetryPolicy(max_retries=config.MAX_RETRIES),
        timeout=config.API_TIMEOUT_S,
    ) as client:
        results = []
        for comment in COMMENTS:
            result = await judge_one(client, comment)
            print(f"  judged {result.id}  {result.lane:<9} {result.latency_ms:>6.0f}ms")
            results.append(result)

    report(results)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    print(f"\nrecorded to {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
