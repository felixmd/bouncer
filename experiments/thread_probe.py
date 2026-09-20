"""Judge a sample from a real thread. TASKS.md 2.2 and 2.3.

    uv run --env-file .env python -m experiments.thread_probe data/threads/hn-47340079.json
    uv run --env-file .env python -m experiments.thread_probe <file> --n 150 --pen 20

The question this answers is the one `FINDINGS.md` §5 left open. Every number in
that document came from Civil Comments, which has no thread structure, so
`on_topic` was being asked what a comment is replying to with nothing in the
state to reply to — a documented cause of low confidence, induced by the
dataset. Its mean confidence there was 0.50 against 0.86 on the hand-written
Reddit corpus.

This runs the real pipeline — `JudgeClient`, `BATCH_SIZE`, quoted addressing,
thread title and parent snippets — against a real thread, and prints the lane
mix, per-axis confidence, and a sample of penned comments to read.

There is no ground truth here. Hacker News ships no labels, so this cannot
measure accuracy; it measures confidence and Pen volume, which is what the
open question is about. Accuracy stays with the Jigsaw path.
"""

import argparse
import asyncio
import json
import random
import statistics
from pathlib import Path

import config
from judge.client import JudgeClient
from judge.lanes import Lane, decisive_confidence, lane
from judge.rubric import DEFAULT_RUBRIC, normalise

OUT_DIR = Path(__file__).parent / "out"


def build_state(thread: dict) -> dict:
    """Shared across the batch, so it holds only what every comment shares.

    Per-comment parent snippets travel in the questions instead — they differ
    per comment and would be a distractor for fourteen of the fifteen.
    """
    state = {"thread_title": thread["title"]}
    if thread.get("selftext"):
        state["thread_body"] = thread["selftext"][:1200]
    return state


async def judge_batch(client: JudgeClient, thread: dict, batch: list[dict]) -> list[dict]:
    questions = {}
    for comment in batch:
        # A top-level comment is replying to the post itself, so the title
        # stands in as the referent. The on_topic levels all point at one thing;
        # leaving this None would leave them pointing at nothing.
        parent = comment.get("parent_snippet") or thread["title"]
        questions |= DEFAULT_RUBRIC.questions_for(comment["id"], comment["body"], parent)

    response, latency_ms, error = await client.ask(build_state(thread), questions)
    if response is None:
        # Invariant: an unjudgeable comment is a comment a human looks at.
        return [{**c, "lane": Lane.PEN.value, "error": error} for c in batch]

    out = []
    for comment in batch:
        scores, confidences = {}, {}
        for axis in DEFAULT_RUBRIC.keys:
            answer = response.scores.get(f"{comment['id']}_{axis}")
            if answer is None:
                break
            scores[axis] = normalise(answer.score)
            confidences[axis] = answer.confidence
        if len(scores) < len(DEFAULT_RUBRIC.keys):
            out.append({**comment, "lane": Lane.PEN.value, "error": "missing answers"})
            continue
        out.append({
            **comment,
            "scores": scores,
            "confidences": confidences,
            "decisive": decisive_confidence(scores, confidences),
            "lane": lane(scores, confidences).value,
            "latency_ms": latency_ms,
            "error": None,
        })
    return out


async def main(args: argparse.Namespace) -> None:
    document = json.loads(args.thread.read_text(encoding="utf-8"))
    thread, comments = document["thread"], document["comments"]

    random.seed(args.seed)
    sample = random.sample(comments, min(args.n, len(comments)))
    batches = [sample[i:i + config.BATCH_SIZE] for i in range(0, len(sample), config.BATCH_SIZE)]

    print(f"{thread['source']}  {thread['title'][:70]}")
    print(f"  {len(sample)} of {len(comments)} comments, B={config.BATCH_SIZE}, "
          f"gate={config.CONFIDENCE_GATE} floor={config.CONFIDENCE_FLOOR}\n")

    async with JudgeClient() as client:
        results = [r for part in await asyncio.gather(
            *(judge_batch(client, thread, b) for b in batches)
        ) for r in part]
        stats = client.stats

    ok = [r for r in results if not r.get("error")]
    if not ok:
        raise SystemExit("every request failed — check the key and the rate limit")

    print("lanes")
    for value in (Lane.APPROVED, Lane.BOUNCED, Lane.PEN):
        n = sum(1 for r in results if r["lane"] == value.value)
        print(f"  {value.value:<9} {n:>4}/{len(results)}  ({n / len(results):.0%})")
    auto = sum(1 for r in results if r["lane"] != Lane.PEN.value) / len(results)
    print(f"  auto-handled   {auto:.0%}")

    print("\nper-axis confidence (Civil Comments figures for comparison)")
    jigsaw = {"hostility": 0.702, "contempt": 0.666, "substance": 0.681, "on_topic": 0.500}
    for axis in DEFAULT_RUBRIC.keys:
        values = [r["confidences"][axis] for r in ok]
        mean = statistics.mean(values)
        delta = mean - jigsaw[axis]
        print(f"  {axis:<11} mean {mean:.3f}  median {statistics.median(values):.3f}"
              f"   vs jigsaw {jigsaw[axis]:.3f}  ({delta:+.3f})")

    dec = [r["decisive"] for r in ok]
    print(f"\n  decisive confidence  mean {statistics.mean(dec):.3f}  "
          f"median {statistics.median(dec):.3f}")

    print("\n  floor    auto-handled")
    for floor in (0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        share = sum(1 for d in dec if d >= floor) / len(dec)
        mark = "  <- current" if abs(floor - config.CONFIDENCE_FLOOR) < 1e-9 else ""
        print(f"  {floor:.2f}     {share:>5.0%}{mark}")

    lat = sorted(r["latency_ms"] for r in ok)
    print(f"\n  {stats.requests} requests, {stats.errors} errors, "
          f"p50 {lat[len(lat) // 2]:.0f}ms, {stats.input_tokens:,} tokens, "
          f"${stats.cost_usd:.4f}")

    # TASKS.md 2.3 is a human reading these and deciding whether they are hard.
    penned = [r for r in ok if r["lane"] == Lane.PEN.value]
    print(f"\n{'=' * 96}\nPENNED — read these and try to call them (TASKS.md 2.3)\n{'=' * 96}")
    for r in random.sample(penned, min(args.pen, len(penned))):
        worst = min(DEFAULT_RUBRIC.keys, key=lambda a: r["confidences"][a])
        print(f"\n[{r['id']}] decisive {r['decisive']:.2f}, least sure on {worst} "
              f"({r['confidences'][worst]:.2f})")
        print("  scores  " + "  ".join(
            f"{a[:4]} {r['scores'][a]:.1f}" for a in DEFAULT_RUBRIC.keys))
        if r.get("parent_snippet"):
            print(f"  re: {r['parent_snippet'][:110]}")
        print(f"  {r['body'][:300]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"probe-{args.thread.stem}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("thread", type=Path)
    parser.add_argument("--n", type=int, default=150)
    parser.add_argument("--pen", type=int, default=12, help="penned comments to print")
    parser.add_argument("--seed", type=int, default=0)
    asyncio.run(main(parser.parse_args()))
