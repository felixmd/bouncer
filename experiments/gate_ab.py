"""TASKS.md 2.1b — gate on the decision, not on the level.

    uv run --env-file .env python -m experiments.gate_ab

`FINDINGS.md` §11 took rubric work about as far as it goes and 84% of traffic
still pens. §12 argues the gate is measuring the wrong quantity.

`ScoreAnswer.confidence` is how concentrated probability is across the five
levels. The lane turns on which **side of the severity threshold** a comment
falls. A comment spread evenly across levels 0, 1 and 2 has low confidence and a
completely certain decision — every one of those levels is far below the line.
Today it pens, and a human is asked to adjudicate something the model was never
unsure about.

`ScoreAnswer.probabilities` gives the per-level distribution, so the right
quantity is directly computable.

A second, independent argument for the same change
--------------------------------------------------
`jev-1.13` is documented as "weak in numerical calibration" for score levels:
you cannot read the fractional part as a magnitude. Our `SEVERITY_THRESHOLD` of
6.0 on the normalised scale sits **between** level 2 (5.0) and level 3 (7.5) —
precisely the reading the docs say is unreliable. Thresholding on
`P(level >= 3)` puts the decision on a level boundary instead, which is a
quantity the model is documented as good at.

So this run tests both halves, separately, because they could disagree:

  decision   mean >= 6.0          vs  P(level >= 3) > 0.5
  gate       scalar confidence    vs  max(p_over, 1 - p_over)

Measured on Civil Comments, where a human label says which decision was right.
An increase in auto-handled volume is only good if agreement holds — otherwise
it is a lower floor wearing a disguise, which is the move §4 rules out.
"""

import argparse
import asyncio
import json
import statistics
from pathlib import Path

import config
from judge.client import JudgeClient
from judge.rubric import DEFAULT_RUBRIC, normalise

OUT_PATH = Path(__file__).parent / "out" / "gate_ab.json"
JIGSAW = Path("data/jigsaw/sample.json")

# Level 3 of 0..4 is the first rung the rubric describes as over the line — "a
# direct personal attack" for hostility, "not worth discussing" for contempt.
# Normalised that is 7.5, against the current threshold of 6.0.
OVER_AT = 3
LOW_AT = 1  # levels 0 and 1 are the "adds nothing" end of the quality axes


def p_at_or_above(probabilities: dict[str, float], level: int) -> float:
    return sum(p for lvl, p in probabilities.items() if int(lvl) >= level)


async def score(client: JudgeClient, comments: list[dict], state: dict,
                axes: list[str], parents: dict, batch_size: int) -> dict:
    """Capture the full per-level distribution, not just the scalar summary."""
    batches = [comments[i:i + batch_size] for i in range(0, len(comments), batch_size)]
    rubric_axes = [a for a in DEFAULT_RUBRIC.axes if a.key in axes]

    async def one(batch: list[dict]) -> dict:
        questions = {}
        for comment in batch:
            for axis in rubric_axes:
                questions[f"{comment['id']}__{axis.key}"] = axis.score_question(
                    comment["id"], comment["body"], config.ADDRESSING, parents[comment["id"]]
                )
        response, _, error = await client.ask(state, questions)
        if response is None:
            print(f"    error: {error}")
            return {}
        out = {}
        for comment in batch:
            entry = {}
            for axis in rubric_axes:
                answer = response.scores.get(f"{comment['id']}__{axis.key}")
                if answer is not None:
                    entry[axis.key] = {
                        "score": normalise(answer.score),
                        "confidence": answer.confidence,
                        "probabilities": {str(k): v for k, v in answer.probabilities.items()},
                    }
            if len(entry) == len(rubric_axes):
                out[comment["id"]] = entry
        return out

    merged = {}
    for part in await asyncio.gather(*(one(b) for b in batches)):
        merged.update(part)
    return merged


# --- the two decision rules and the two gates, on the severity axes ---------

def decision_mean(entry: dict) -> bool:
    return any(
        entry[axis]["score"] >= config.SEVERITY_THRESHOLD for axis in ("hostility", "contempt")
    )


def decision_level(entry: dict) -> bool:
    return any(
        p_at_or_above(entry[axis]["probabilities"], OVER_AT) > 0.5
        for axis in ("hostility", "contempt")
    )


def gate_scalar(entry: dict) -> float:
    return min(entry[axis]["confidence"] for axis in ("hostility", "contempt"))


def gate_decision(entry: dict) -> float:
    """How sure is the model which *side of the line* this falls, per axis."""
    values = []
    for axis in ("hostility", "contempt"):
        p_over = p_at_or_above(entry[axis]["probabilities"], OVER_AT)
        values.append(max(p_over, 1.0 - p_over))
    return min(values)


ARMS = {
    "mean + scalar conf  (current)": (decision_mean, gate_scalar),
    "mean + decision conf": (decision_mean, gate_decision),
    "level + scalar conf": (decision_level, gate_scalar),
    "level + decision conf": (decision_level, gate_decision),
}
FLOORS = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


def curve(scored: dict, labels: dict[str, bool]) -> None:
    print(f"\n{'arm':<30} {'floor':>6} {'auto':>6} {'agree|auto':>11} {'prec':>6} {'rec':>6}")
    print("-" * 70)
    for name, (decide, gate) in ARMS.items():
        for floor in FLOORS:
            auto = correct = tp = fp = fn = 0
            for cid, entry in scored.items():
                if gate(entry) < floor:
                    continue
                auto += 1
                predicted, actual = decide(entry), labels[cid]
                correct += predicted == actual
                tp += predicted and actual
                fp += predicted and not actual
                fn += (not predicted) and actual
            if not auto:
                continue
            print(f"{name:<30} {floor:>6.2f} {auto / len(scored):>5.0%} "
                  f"{correct / auto:>11.3f} "
                  f"{tp / (tp + fp) if tp + fp else 0:>6.2f} "
                  f"{tp / (tp + fn) if tp + fn else 0:>6.2f}")
        print()


def decision_agreement(scored: dict, labels: dict[str, bool]) -> None:
    """Ungated: does moving the threshold to a level boundary change accuracy?"""
    print("DECISION RULE, ignoring the gate — paired, discordant comments only")
    print("-" * 70)
    only_mean = only_level = 0
    for cid, entry in scored.items():
        lhs = decision_mean(entry) == labels[cid]
        rhs = decision_level(entry) == labels[cid]
        only_mean += lhs and not rhs
        only_level += rhs and not lhs
    total = only_mean + only_level
    verdict = (
        "even — differently wrong" if total and min(only_mean, only_level) / total > 0.35
        else "leans " + ("level" if only_level > only_mean else "mean")
    )
    print(f"  mean>=6.0 : P(level>=3)>0.5   {only_mean} : {only_level} of {total}   {verdict}")
    for name, decide in (("mean>=6.0", decision_mean), ("P(level>=3)>0.5", decision_level)):
        called = sum(1 for e in scored.values() if decide(e))
        agree = sum(1 for c, e in scored.items() if decide(e) == labels[c]) / len(scored)
        print(f"    {name:<18} calls {called:>3}/{len(scored)} toxic, "
              f"agreement {agree:.3f}")


async def main(args: argparse.Namespace, jig: list[dict], document: dict) -> None:
    labels = {c["id"]: c["label_toxic"] for c in jig}
    async with JudgeClient() as client:
        print(f"Civil Comments — {len(jig)} comments, severity axes, with labels")
        jigsaw = await score(
            client,
            [{"id": c["id"], "body": c["text"]} for c in jig],
            {"context": "A comment section on a news article."},
            ["hostility", "contempt"], dict.fromkeys(labels, None), args.batch,
        )

        thread = document["thread"]
        hn = document["comments"][:args.n]
        print(f"Hacker News — {len(hn)} comments, severity axes, real thread context")
        hn_scored = await score(
            client, hn, {"thread_title": thread["title"]}, ["hostility", "contempt"],
            {c["id"]: c.get("parent_snippet") or thread["title"] for c in hn}, args.batch,
        )
        print(f"  {client.stats.requests} requests, {client.stats.errors} errors, "
              f"${client.stats.cost_usd:.4f}\n")

    print("=" * 70)
    decision_agreement(jigsaw, labels)
    print("\n" + "=" * 70)
    print("GATE — auto-handled share and agreement on it (Civil Comments)")
    print("=" * 70)
    curve(jigsaw, labels)

    print("=" * 70)
    print("GATE VALUES on a real thread (Hacker News, no labels)")
    print("=" * 70)
    for name, gate in (("scalar confidence", gate_scalar), ("decision confidence", gate_decision)):
        values = [gate(e) for e in hn_scored.values()]
        print(f"\n  {name}:  mean {statistics.mean(values):.3f}  "
              f"median {statistics.median(values):.3f}")
        for floor in (0.70, 0.80, 0.85, 0.90, 0.95):
            share = sum(1 for v in values if v >= floor) / len(values)
            mark = "  <- current floor" if abs(floor - config.CONFIDENCE_FLOOR) < 1e-9 else ""
            print(f"    floor {floor:.2f}   auto-handled {share:>5.0%}{mark}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"jigsaw": jigsaw, "hn": hn_scored}, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread", type=Path, default=Path("data/threads/hn-47340079.json"))
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--batch", type=int, default=15)
    parsed = parser.parse_args()
    asyncio.run(main(
        parsed,
        json.loads(JIGSAW.read_text(encoding="utf-8"))[:parsed.n],
        json.loads(parsed.thread.read_text(encoding="utf-8")),
    ))
