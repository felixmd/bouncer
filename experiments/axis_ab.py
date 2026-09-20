"""TASKS.md 2.1 — pick the rubric levels one axis at a time.

    uv run --env-file .env python -m experiments.axis_ab

`FINDINGS.md` §3 found the v1→v2 rewrite was a trade, not an improvement: much
better on `on_topic`, significantly worse on `hostility`, accuracy a coin flip.
The obvious move — keep the better levels per axis — was never tested. §10 then
found that `hostility` and `contempt`, both sitting near 0.67, are what pen most
comments under `decisive` gating. So this is the task that stands between the
current state and a demo with three visible lanes.

Design note, and it matters
---------------------------
Every variant for every axis goes in the **same request**. Jev evaluates
questions independently against one shared state, so `c000__hostility__v1` and
`c000__hostility__v3` are simply two questions about the same comment. That
buys three things the previous experiment did not have:

- perfect pairing — same state, same request, same moment;
- no arm-order confound, which `FINDINGS.md` §7 listed as an open weakness
  ("any API drift during the run is confounded with the arm order");
- one run instead of six.

Two corpora, each asked what it can answer:

  Hacker News    real thread context, so all four axes are meaningful.
                 No labels, so confidence only.
  Civil Comments human toxicity labels, so `hostility` and `contempt` can be
                 scored for accuracy. Both are intrinsic to the comment's own
                 text and need no thread, which is exactly what this corpus is.
                 `substance` and `on_topic` are skipped here — asking "how far
                 does it engage with the text it is replying to" against a
                 corpus with no parent is what produced the bad §5 reading.
"""

import argparse
import asyncio
import json
import statistics
from pathlib import Path

import config
from judge.client import JudgeClient
from judge.rubric import CONTEMPT, HOSTILITY, ON_TOPIC, SUBSTANCE, Axis, normalise

OUT_PATH = Path(__file__).parent / "out" / "axis_ab.json"
JIGSAW = Path("data/jigsaw/sample.json")

# --- candidate levels -------------------------------------------------------
# v1 and v2 are the historical arms, frozen. v3 is a new attempt at the two
# axes that are actually binding, built on what the v1-vs-v2 result implies.

HOSTILITY_V1 = Axis(
    key="hostility", question="is it attacking a person?",
    levels=[
        "Addresses ideas, events or the situation only. No personal remarks about anyone.",
        "Mildly pointed at a person — a dig or a sarcastic aside, not an attack.",
        "Openly critical of a person's character, motives or intelligence, but not abusive.",
        "A direct personal attack: insults, name-calling, or open hostility toward someone.",
        "Sustained abuse of a person: degrading language, or wishing harm on them.",
    ],
    higher_is_worse=True, needs_thread_context=False,
)

# v2 lost to v1 by 0.083. The likely reason is level 1 — "reports what a person
# did or said, without judging them" — which is a *different dimension*, not a
# lower rung on the same ladder. Most comments are neither that nor level 0, so
# probability splits between two descriptions of "not hostile".
# v3 keeps v2's situational phrasing but makes every rung the same ladder:
# nobody criticised → argument criticised → person criticised → person labelled
# → person abused.
HOSTILITY_V3 = Axis(
    key="hostility", question="is it attacking a person?",
    levels=[
        "No person is criticised. The comment is about an idea, a topic, an event or a thing.",
        "A person's argument is disagreed with, but the person is not criticised.",
        "A person is criticised: called wrong, careless, hypocritical, or acting in bad faith.",
        "A person is insulted, mocked, or labelled — told what they are, not what they did.",
        "A person is sworn at, degraded, or told they deserve harm.",
    ],
    higher_is_worse=True, needs_thread_context=False,
)

CONTEMPT_V1 = Axis(
    key="contempt", question="does it dismiss anyone who disagrees as not worth answering?",
    levels=[
        "Treats disagreement as legitimate, or does not engage with opposing views at all.",
        "Confident and one-sided, but leaves room for the other view to exist.",
        "Implies people who disagree are uninformed or have not thought it through.",
        "States or strongly implies that opposing views are not worth engaging with.",
        "Writes off everyone who disagrees as stupid, bad faith, or beneath response.",
    ],
    higher_is_worse=True, needs_thread_context=False,
)

# v3 escalates along one axis — from how the *view* is treated to how the
# *people* are treated — instead of mixing "how confident" with "how dismissive".
CONTEMPT_V3 = Axis(
    key="contempt", question="how is a differing view treated?",
    levels=[
        "No differing view is addressed.",
        "A differing view is answered on its merits.",
        "A differing view is called wrong, with no reason given.",
        "A differing view is called not worth discussing, or is refused engagement.",
        "The people who hold it are called stupid, dishonest, or not worth replying to.",
    ],
    higher_is_worse=True, needs_thread_context=False,
)

ON_TOPIC_V2 = Axis(
    key="on_topic", question="what is it responding to?",
    levels=[
        "The comment is about a different subject entirely.",
        "The comment starts from the thread's subject and moves on to a different one.",
        "The comment is about the thread's general subject.",
        "The comment addresses the specific question or claim the thread is about.",
        "The comment restates or quotes a specific point and responds to that point.",
    ],
    higher_is_worse=False, needs_thread_context=True,
)

SUBSTANCE_V1 = Axis(
    key="substance", question="does it add anything to the discussion?",
    levels=[
        "Adds nothing: a bare reaction, an agreement, a meme, or noise.",
        "States a position with no reasoning, evidence or detail behind it.",
        "Gives a brief reason, or one concrete detail.",
        "Makes a clear argument with reasoning, an example, or relevant experience.",
        "Adds substantial information, evidence, or a developed argument.",
    ],
    higher_is_worse=False, needs_thread_context=True,
)

# HOSTILITY / CONTEMPT / SUBSTANCE / ON_TOPIC imported from judge.rubric are the
# current defaults — v2 for the first three, the single-referent rewrite for
# on_topic (FINDINGS §9), labelled v3 here because that is what it is.
VARIANTS: dict[str, dict[str, Axis]] = {
    "hostility": {"v1": HOSTILITY_V1, "v2": HOSTILITY, "v3": HOSTILITY_V3},
    "contempt": {"v1": CONTEMPT_V1, "v2": CONTEMPT, "v3": CONTEMPT_V3},
    "substance": {"v1": SUBSTANCE_V1, "v2": SUBSTANCE},
    "on_topic": {"v2": ON_TOPIC_V2, "v3": ON_TOPIC},
}
INTRINSIC = ["hostility", "contempt"]  # meaningful without thread context


def build_questions(batch: list[dict], axes: list[str], parents: dict[str, str | None]) -> dict:
    questions = {}
    for comment in batch:
        for axis in axes:
            for name, variant in VARIANTS[axis].items():
                questions[f"{comment['id']}__{axis}__{name}"] = variant.score_question(
                    comment["id"], comment["body"], config.ADDRESSING, parents[comment["id"]]
                )
    return questions


async def run(client: JudgeClient, comments: list[dict], state: dict,
              axes: list[str], parents: dict[str, str | None], batch_size: int) -> dict:
    batches = [comments[i:i + batch_size] for i in range(0, len(comments), batch_size)]

    async def one(batch: list[dict]) -> dict:
        response, _, error = await client.ask(state, build_questions(batch, axes, parents))
        if response is None:
            print(f"    error: {error}")
            return {}
        out: dict = {}
        for comment in batch:
            entry: dict = {}
            for axis in axes:
                for name in VARIANTS[axis]:
                    answer = response.scores.get(f"{comment['id']}__{axis}__{name}")
                    if answer is not None:
                        entry[f"{axis}__{name}"] = {
                            "score": normalise(answer.score),
                            "confidence": answer.confidence,
                        }
            out[comment["id"]] = entry
        return out

    merged: dict = {}
    for part in await asyncio.gather(*(one(b) for b in batches)):
        merged.update(part)
    return merged


def paired_confidence(scored: dict, axis: str) -> None:
    """Mean per-comment delta against the axis's incumbent, with standard error."""
    names = list(VARIANTS[axis])
    base = names[0]
    for name in names[1:]:
        deltas = [
            entry[f"{axis}__{name}"]["confidence"] - entry[f"{axis}__{base}"]["confidence"]
            for entry in scored.values()
            if f"{axis}__{name}" in entry and f"{axis}__{base}" in entry
        ]
        if len(deltas) < 2:
            continue
        mean = statistics.mean(deltas)
        sem = statistics.stdev(deltas) / len(deltas) ** 0.5
        stars = "***" if abs(mean) > 3 * sem else ("*" if abs(mean) > 2 * sem else "ns")
        print(f"    {base} -> {name}   {mean:+.3f} +/- {sem:.3f}  {stars}")


def absolute_confidence(scored: dict, axes: list[str]) -> None:
    print(f"\n  {'axis':<11} " + "".join(f"{n:>9}" for n in ("v1", "v2", "v3")))
    for axis in axes:
        row = f"  {axis:<11} "
        for name in ("v1", "v2", "v3"):
            if name in VARIANTS[axis]:
                values = [
                    e[f"{axis}__{name}"]["confidence"]
                    for e in scored.values() if f"{axis}__{name}" in e
                ]
                row += f"{statistics.mean(values):>9.3f}" if values else f"{'-':>9}"
            else:
                row += f"{'-':>9}"
        print(row)


def mcnemar(scored: dict, labels: dict[str, bool], axis: str) -> None:
    """Of the comments where two variants disagree, which is right more often?

    Ties carry no information and are discarded. A near-even discordant split
    means the variants are differently wrong, not better and worse.
    """
    names = list(VARIANTS[axis])
    base = names[0]
    for name in names[1:]:
        only_base = only_new = 0
        for cid, entry in scored.items():
            if f"{axis}__{name}" not in entry or f"{axis}__{base}" not in entry:
                continue
            lhs = (entry[f"{axis}__{base}"]["score"] >= config.SEVERITY_THRESHOLD) == labels[cid]
            rhs = (entry[f"{axis}__{name}"]["score"] >= config.SEVERITY_THRESHOLD) == labels[cid]
            only_base += lhs and not rhs
            only_new += rhs and not lhs
        total = only_base + only_new
        if not total:
            print(f"    {base} -> {name}   identical on every comment")
            continue
        verdict = (
            "even — differently wrong, not better"
            if min(only_base, only_new) / total > 0.35
            else f"leans {name if only_new > only_base else base}"
        )
        print(f"    {base} -> {name}   {only_base:>3} : {only_new:<3} of {total:<3}  {verdict}")


async def main(args: argparse.Namespace, document: dict, jig: list[dict]) -> None:
    results: dict[str, dict] = {}
    async with JudgeClient() as client:
        # --- Hacker News: all four axes, real thread context ---------------
        thread = document["thread"]
        hn = document["comments"][:args.n]
        parents = {
            c["id"]: (c.get("parent_snippet") or thread["title"]) for c in hn
        }
        state = {"thread_title": thread["title"]}
        print(f"Hacker News — {len(hn)} comments, all four axes, B={args.batch}")
        results["hn"] = await run(
            client, hn, state, list(VARIANTS), parents, args.batch
        )
        print(f"  {client.stats.requests} requests, {client.stats.errors} errors")

        # --- Civil Comments: the two intrinsic axes, with labels ------------
        jig_comments = [{"id": c["id"], "body": c["text"]} for c in jig]
        labels = {c["id"]: c["label_toxic"] for c in jig}
        print(f"\nCivil Comments — {len(jig)} comments, hostility and contempt only")
        results["jigsaw"] = await run(
            client, jig_comments,
            {"context": "A comment section on a news article."},
            INTRINSIC, dict.fromkeys(labels, None), args.batch,
        )
        print(f"  {client.stats.requests} requests total, ${client.stats.cost_usd:.4f}")

    print("\n" + "=" * 78)
    print("CONFIDENCE — paired per-comment delta vs the axis incumbent")
    print("=" * 78)
    print("\n  Hacker News (real thread context)")
    for axis in VARIANTS:
        print(f"  {axis}")
        paired_confidence(results["hn"], axis)
    absolute_confidence(results["hn"], list(VARIANTS))

    print("\n  Civil Comments (no thread context)")
    for axis in INTRINSIC:
        print(f"  {axis}")
        paired_confidence(results["jigsaw"], axis)
    absolute_confidence(results["jigsaw"], INTRINSIC)

    print("\n" + "=" * 78)
    print("ACCURACY vs human label — paired, discordant comments only")
    print("=" * 78)
    for axis in INTRINSIC:
        print(f"  {axis}")
        mcnemar(results["jigsaw"], labels, axis)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread", type=Path, default=Path("data/threads/hn-47340079.json"))
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument(
        "--batch", type=int, default=8,
        help="smaller than BATCH_SIZE on purpose: ten variants per comment is "
             "ten questions, and the ceiling is 64K for state plus all questions",
    )
    parsed = parser.parse_args()
    asyncio.run(main(
        parsed,
        json.loads(parsed.thread.read_text(encoding="utf-8")),
        json.loads(JIGSAW.read_text(encoding="utf-8"))[:parsed.n],
    ))
