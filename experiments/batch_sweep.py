"""TECHNICAL_SPEC §3.2 — where does accuracy fall off as batch size grows?

    uv run --env-file .env python -m experiments.batch_sweep

300 labelled Civil Comments, scored at B = 1, 3, 5, 8, 12, 15, 20, 30, under
two ways of pointing a question at one comment inside a shared state:

  index   state carries the comments; the question says "For comment j007: ...".
          The model has to *locate* j007. This is the shape in §3.3.

  inline  identical state, but the question also quotes the comment it is about.
          Nothing has to be located. Costs tokens, and we are request-limited,
          not token-limited.

The second variant exists because of what the vendor documents rather than a
hunch. jev-1.13's jaggedness page says it "cannot reliably count items, with
error growing with the size of the thing being counted", and that "accuracy
falls as the state grows with content unrelated to the decision". Those are two
different failure modes and the spec's design is exposed to both. Quoting the
comment inline removes the first one without touching the second, so running
both separates them.

Ground truth is the human `toxicity >= 0.5` label, so this measures agreement
with people, not just self-consistency with B=1.
"""

import asyncio
import json
import statistics
import time
from pathlib import Path

from typesafe_sdk import Score

import config
from judge.client import JudgeClient
from judge.lanes import Lane
from judge.rubric import DEFAULT_RUBRIC, normalise

SAMPLE = Path("data/jigsaw/sample.json")
OUT_PATH = Path(__file__).parent / "out" / "batch_sweep.json"
BATCH_SIZES = [1, 3, 5, 8, 12, 15, 20, 30]
MODES = ["index", "inline"]

# Civil Comments has no thread structure. A fixed, neutral frame keeps the
# state shape identical to production without inventing a thread.
CONTEXT = "A comment section on a news article. Comments are independent of each other."


def build_questions(batch: list[dict], mode: str) -> dict[str, Score]:
    questions: dict[str, Score] = {}
    for comment in batch:
        for axis in DEFAULT_RUBRIC.axes:
            if mode == "index":
                instructions = f"For comment {comment['id']}: {axis.question}"
            else:
                instructions = (
                    f"For comment {comment['id']}, which reads:\n"
                    f"\"\"\"\n{comment['text']}\n\"\"\"\n"
                    f"{axis.question}"
                )
            questions[f"{comment['id']}_{axis.key}"] = Score(
                instructions=instructions, criteria=list(axis.levels)
            )
    return questions


def build_state(batch: list[dict]) -> dict:
    return {
        "context": CONTEXT,
        "comments": [{"id": c["id"], "text": c["text"]} for c in batch],
    }


async def run_batch(client: JudgeClient, batch: list[dict], mode: str) -> dict:
    response, latency_ms, error = await client.ask(build_state(batch), build_questions(batch, mode))
    out: dict = {"latency_ms": latency_ms, "error": error, "scored": {}}
    if response is None:
        return out
    out["input_tokens"] = response.usage.input_tokens
    for comment in batch:
        scores, confidences = {}, {}
        for axis in DEFAULT_RUBRIC.keys:
            answer = response.scores.get(f"{comment['id']}_{axis}")
            if answer is None:  # a question the model did not answer
                break
            scores[axis] = normalise(answer.score)
            confidences[axis] = answer.confidence
        if len(scores) == len(DEFAULT_RUBRIC.keys):
            out["scored"][comment["id"]] = {"scores": scores, "confidences": confidences}
    return out


async def sweep_one(client: JudgeClient, comments: list[dict], size: int, mode: str) -> dict:
    batches = [comments[i:i + size] for i in range(0, len(comments), size)]
    started = time.perf_counter()
    results = await asyncio.gather(*(run_batch(client, b, mode) for b in batches))
    wall_s = time.perf_counter() - started

    scored: dict[str, dict] = {}
    for result in results:
        scored.update(result["scored"])
    latencies = sorted(r["latency_ms"] for r in results)
    tokens = sum(r.get("input_tokens") or 0 for r in results)
    errors = [r["error"] for r in results if r["error"]]

    return {
        "batch_size": size,
        "mode": mode,
        "requests": len(batches),
        "returned": len(scored),
        "missing": len(comments) - len(scored),
        "errors": errors[:3],
        "error_count": len(errors),
        "wall_s": wall_s,
        "p50_latency_ms": latencies[len(latencies) // 2],
        "max_latency_ms": latencies[-1],
        "input_tokens": tokens,
        "tokens_per_comment": tokens / max(len(scored), 1),
        "scored": scored,
    }


def content_lane(scores: dict[str, float]) -> Lane:
    over = (
        scores["hostility"] >= config.SEVERITY_THRESHOLD
        or scores["contempt"] >= config.SEVERITY_THRESHOLD
        or (
            scores["substance"] <= config.LOW_SUBSTANCE_THRESHOLD
            and scores["on_topic"] <= config.LOW_ON_TOPIC_THRESHOLD
        )
    )
    return Lane.BOUNCED if over else Lane.APPROVED


def evaluate(run: dict, labels: dict[str, bool], baseline: dict | None) -> dict:
    scored = run["scored"]
    if not scored:
        return run | {"scored": None, "usable": False}

    tp = fp = tn = fn = 0
    for cid, entry in scored.items():
        predicted = content_lane(entry["scores"]) is Lane.BOUNCED
        actual = labels[cid]
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1

    confidences = [c for e in scored.values() for c in e["confidences"].values()]
    mins = [min(e["confidences"].values()) for e in scored.values()]

    drift = None
    lane_agree = None
    if baseline is not None:
        shared = set(scored) & set(baseline["scored"])
        if shared:
            deltas = [
                abs(scored[c]["scores"][a] - baseline["scored"][c]["scores"][a])
                for c in shared
                for a in DEFAULT_RUBRIC.keys
            ]
            drift = statistics.mean(deltas)
            lane_agree = sum(
                content_lane(scored[c]["scores"]) is content_lane(baseline["scored"][c]["scores"])
                for c in shared
            ) / len(shared)

    return {
        **{k: v for k, v in run.items() if k != "scored"},
        "usable": True,
        "n": len(scored),
        "label_agreement": (tp + tn) / len(scored),
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "mean_confidence": statistics.mean(confidences),
        "median_confidence": statistics.median(confidences),
        "mean_min_confidence": statistics.mean(mins),
        "pen_rate_at_0_80": sum(1 for m in mins if m < 0.80) / len(mins),
        "score_drift_vs_b1": drift,
        "lane_agreement_vs_b1": lane_agree,
    }


async def main(comments: list[dict]) -> None:
    labels = {c["id"]: c["label_toxic"] for c in comments}
    print(f"{len(comments)} comments, {sum(labels.values())} labelled toxic\n")

    rows: list[dict] = []
    async with JudgeClient() as client:
        for mode in MODES:
            baseline = None
            for size in BATCH_SIZES:
                run = await sweep_one(client, comments, size, mode)
                if size == 1:
                    baseline = run
                row = evaluate(run, labels, baseline)
                rows.append(row)
                if row["usable"]:
                    print(
                        f"  {mode:<6} B={size:<3} "
                        f"agree {row['label_agreement']:.3f}  "
                        f"conf {row['mean_confidence']:.3f}  "
                        f"drift {row['score_drift_vs_b1'] or 0:.2f}  "
                        f"missing {row['missing']:<3} "
                        f"{row['wall_s']:.1f}s  {row['tokens_per_comment']:.0f} tok/c"
                    )
                else:
                    print(f"  {mode:<6} B={size:<3} UNUSABLE  {row['errors'][:1]}")
        print(f"\n  {client.stats.requests} requests, {client.stats.errors} errors, "
              f"{client.stats.input_tokens:,} tokens, ${client.stats.cost_usd:.4f}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main(json.loads(SAMPLE.read_text(encoding="utf-8"))))
