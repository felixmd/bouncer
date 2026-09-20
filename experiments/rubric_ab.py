"""Why is confidence low, and can we fix it without touching the threshold?

    uv run --env-file .env python -m experiments.rubric_ab

The batch sweep found no accuracy knee, so batch size is not the constraint.
What is left is a mean confidence of 0.62 that puts 99% of comments in the Pen,
and a recall of 0.45 against human labels.

The Score docs give three causes of low confidence — the levels overlap for
this state, the question measures more than one thing, the state doesn't say
enough — and one instruction: "describe situations, not degrees". The v1 rubric
is a degree ladder ("mildly pointed", "openly critical", "sustained abuse") and
its contempt level 0 reads "treats disagreement as legitimate, *or* does not
engage with opposing views at all", which is two situations in one level. So v1
commits at least two of the three documented sins.

Two independent changes, tested as a 2x2 at fixed B=15:

  rubric    v1 (degrees, overlapping) vs v2 (situations, disjoint)
  address   index  — comments live in the state, question says "for comment X"
            quoted — state holds *no* comments at all; each question carries
                     only the one comment it asks about

`quoted` is the interesting one. Context rot is about unrelated material in the
state; at B=15 the other 14 comments are exactly that. Moving each comment into
its own question keeps 15 comments per request — so throughput is unchanged —
while leaving the state with nothing irrelevant in it.

Also runs a noise floor: B=1 twice with everything identical. Without it we
cannot tell whether the 0.6-point drift in the sweep was a batch effect or just
run-to-run sampling.
"""

import asyncio
import json
import statistics
from pathlib import Path

from typesafe_sdk import Score

import config
from judge.client import JudgeClient
from judge.lanes import Lane
from judge.rubric import DEFAULT_RUBRIC, Axis, Rubric, normalise

# v1 is frozen here rather than imported. judge/rubric.py now *is* v2 — this
# experiment is why — and an import would quietly turn the A/B into B/B.
V1 = Rubric(
    axes=[
        Axis(
            key="hostility",
            question="is it attacking a person?",
            levels=[
                "Addresses ideas, events or the situation only. No personal remarks "
                "about anyone.",
                "Mildly pointed at a person — a dig or a sarcastic aside, not an attack.",
                "Openly critical of a person's character, motives or intelligence, but "
                "not abusive.",
                "A direct personal attack: insults, name-calling, or open hostility "
                "toward someone.",
                "Sustained abuse of a person: degrading language, or wishing harm on them.",
            ],
            higher_is_worse=True,
            needs_thread_context=False,
        ),
        Axis(
            key="contempt",
            question="does it dismiss anyone who disagrees as not worth answering?",
            levels=[
                "Treats disagreement as legitimate, or does not engage with opposing "
                "views at all.",
                "Confident and one-sided, but leaves room for the other view to exist.",
                "Implies people who disagree are uninformed or have not thought it through.",
                "States or strongly implies that opposing views are not worth engaging with.",
                "Writes off everyone who disagrees as stupid, bad faith, or beneath response.",
            ],
            higher_is_worse=True,
            needs_thread_context=False,
        ),
        Axis(
            key="substance",
            question="does it add anything to the discussion?",
            levels=[
                "Adds nothing: a bare reaction, an agreement, a meme, or noise.",
                "States a position with no reasoning, evidence or detail behind it.",
                "Gives a brief reason, or one concrete detail.",
                "Makes a clear argument with reasoning, an example, or relevant experience.",
                "Adds substantial information, evidence, or a developed argument.",
            ],
            higher_is_worse=False,
            needs_thread_context=True,
        ),
        Axis(
            key="on_topic",
            question="does it engage with what it is replying to?",
            levels=[
                "Unrelated to the thread and to the comment it replies to.",
                "Loosely connected — uses the thread as a springboard for something else.",
                "Related to the general subject but not to the specific point being made.",
                "Engages with the thread's question, or with the parent comment's point.",
                "Responds directly and specifically to what it is replying to.",
            ],
            higher_is_worse=False,
            needs_thread_context=True,
        ),
    ]
)

SAMPLE = Path("data/jigsaw/sample.json")
OUT_PATH = Path(__file__).parent / "out" / "rubric_ab.json"
BATCH = 15
CONTEXT = "A comment section on a news article. Comments are independent of each other."

# --- rubric v2: situations, not degrees; one idea per level ------------------

V2 = Rubric(
    axes=[
        Axis(
            key="hostility",
            question="is it attacking a person?",
            levels=[
                "The comment is about a topic, an event, a policy or an idea. "
                "No person is described.",
                "The comment reports what a person did or said, without judging them for it.",
                "The comment says a person is wrong, mistaken, hypocritical, or behaving badly.",
                "The comment calls a person a name, mocks them, or says what kind of person "
                "they are.",
                "The comment swears at a person, uses degrading language about them, or says "
                "they deserve harm.",
            ],
            higher_is_worse=True,
            needs_thread_context=False,
        ),
        Axis(
            key="contempt",
            question="how does it treat people who hold the opposing view?",
            levels=[
                "The comment does not refer to anyone who disagrees.",
                "The comment answers an opposing argument on its merits.",
                "The comment says the opposing side is wrong without saying why.",
                "The comment says the opposing argument is not worth discussing, or refuses "
                "to engage with it.",
                "The comment says the people who hold the opposing view are stupid, dishonest, "
                "or beneath replying to.",
            ],
            higher_is_worse=True,
            needs_thread_context=False,
        ),
        Axis(
            key="substance",
            question="what does it add to the discussion?",
            levels=[
                "The comment is a reaction, an agreement, a joke, or a single phrase.",
                "The comment states an opinion and stops there.",
                "The comment states an opinion and gives one reason or one concrete detail.",
                "The comment makes an argument with more than one step, or draws on personal "
                "experience.",
                "The comment introduces information, figures or sources not already in the "
                "thread.",
            ],
            higher_is_worse=False,
            needs_thread_context=True,
        ),
        Axis(
            key="on_topic",
            question="what is it responding to?",
            levels=[
                "The comment is about a different subject entirely.",
                "The comment starts from the thread's subject and moves on to a different one.",
                "The comment is about the thread's general subject.",
                "The comment addresses the specific question or claim the thread is about.",
                "The comment restates or quotes a specific point and responds to that point.",
            ],
            higher_is_worse=False,
            needs_thread_context=True,
        ),
    ]
)

RUBRICS = {"v1": V1, "v2": V2}


def build(batch: list[dict], rubric: Rubric, address: str) -> tuple[dict, dict[str, Score]]:
    questions: dict[str, Score] = {}
    for comment in batch:
        for axis in rubric.axes:
            if address == "index":
                instructions = f"For comment {comment['id']}: {axis.question}"
            else:
                instructions = (
                    f"The comment is:\n\"\"\"\n{comment['text']}\n\"\"\"\n{axis.question}"
                )
            questions[f"{comment['id']}_{axis.key}"] = Score(
                instructions=instructions, criteria=list(axis.levels)
            )

    if address == "index":
        state = {"context": CONTEXT, "comments": [
            {"id": c["id"], "text": c["text"]} for c in batch
        ]}
    else:
        # Nothing irrelevant to any one question is left in the state.
        state = {"context": CONTEXT}
    return state, questions


async def run_arm(
    client: JudgeClient, comments: list[dict], rubric_name: str, address: str, size: int
) -> dict[str, dict]:
    rubric = RUBRICS[rubric_name]
    batches = [comments[i:i + size] for i in range(0, len(comments), size)]

    async def one(batch: list[dict]) -> dict[str, dict]:
        state, questions = build(batch, rubric, address)
        response, _, error = await client.ask(state, questions)
        if response is None:
            print(f"    error: {error}")
            return {}
        out = {}
        for comment in batch:
            scores, confidences = {}, {}
            for axis in rubric.keys:
                answer = response.scores.get(f"{comment['id']}_{axis}")
                if answer is None:
                    break
                scores[axis] = normalise(answer.score)
                confidences[axis] = answer.confidence
            if len(scores) == len(rubric.keys):
                out[comment["id"]] = {"scores": scores, "confidences": confidences}
        return out

    merged: dict[str, dict] = {}
    for part in await asyncio.gather(*(one(b) for b in batches)):
        merged.update(part)
    return merged


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


def summarise(name: str, scored: dict[str, dict], labels: dict[str, bool]) -> dict:
    confs = [c for e in scored.values() for c in e["confidences"].values()]
    mins = [min(e["confidences"].values()) for e in scored.values()]
    per_axis = {
        axis: statistics.mean(e["confidences"][axis] for e in scored.values())
        for axis in DEFAULT_RUBRIC.keys
    }

    tp = fp = fn = tn = 0
    htp = hfp = hfn = htn = 0
    for cid, entry in scored.items():
        actual = labels[cid]
        predicted = content_lane(entry["scores"]) is Lane.BOUNCED
        tp, fp, fn, tn = (
            tp + (predicted and actual), fp + (predicted and not actual),
            fn + (not predicted and actual), tn + (not predicted and not actual),
        )
        # Civil Comments "toxic" is mostly insult and abuse, which is our
        # hostility axis alone. The other three axes are Reddit-shaped and have
        # no counterpart in the label, so judging them against it is unfair.
        h = entry["scores"]["hostility"] >= config.SEVERITY_THRESHOLD
        htp, hfp, hfn, htn = (
            htp + (h and actual), hfp + (h and not actual),
            hfn + (not h and actual), htn + (not h and not actual),
        )

    n = len(scored)
    return {
        "arm": name,
        "n": n,
        "mean_confidence": statistics.mean(confs),
        "median_confidence": statistics.median(confs),
        "mean_min_confidence": statistics.mean(mins),
        "pen_rate_at_0_80": sum(1 for m in mins if m < 0.80) / n,
        "pen_rate_at_0_60": sum(1 for m in mins if m < 0.60) / n,
        "per_axis_confidence": per_axis,
        "lane_agreement": (tp + tn) / n,
        "lane_precision": tp / (tp + fp) if tp + fp else 0.0,
        "lane_recall": tp / (tp + fn) if tp + fn else 0.0,
        "hostility_agreement": (htp + htn) / n,
        "hostility_precision": htp / (htp + hfp) if htp + hfp else 0.0,
        "hostility_recall": htp / (htp + hfn) if htp + hfn else 0.0,
    }


async def main(comments: list[dict]) -> None:
    labels = {c["id"]: c["label_toxic"] for c in comments}
    results, scored_by_arm = [], {}

    async with JudgeClient() as client:
        print("noise floor: B=1, v1, index, run twice\n")
        first = await run_arm(client, comments, "v1", "index", 1)
        second = await run_arm(client, comments, "v1", "index", 1)
        shared = set(first) & set(second)
        noise = statistics.mean(
            abs(first[c]["scores"][a] - second[c]["scores"][a])
            for c in shared for a in DEFAULT_RUBRIC.keys
        )
        lane_noise = sum(
            content_lane(first[c]["scores"]) is content_lane(second[c]["scores"]) for c in shared
        ) / len(shared)
        print(f"  mean |score delta| between two identical B=1 runs : {noise:.3f} / 10")
        print(f"  lane agreement between two identical B=1 runs     : {lane_noise:.3f}\n")

        print(f"2x2 at B={BATCH}\n")
        for rubric_name in ("v1", "v2"):
            for address in ("index", "quoted"):
                name = f"{rubric_name}/{address}"
                scored = await run_arm(client, comments, rubric_name, address, BATCH)
                scored_by_arm[name] = scored
                row = summarise(name, scored, labels)
                results.append(row)
                print(
                    f"  {name:<12} conf {row['mean_confidence']:.3f}  "
                    f"minconf {row['mean_min_confidence']:.3f}  "
                    f"pen@.80 {row['pen_rate_at_0_80']:.2f}  "
                    f"hostility P/R {row['hostility_precision']:.2f}/"
                    f"{row['hostility_recall']:.2f}  "
                    f"lane agree {row['lane_agreement']:.3f}"
                )

        print(f"\n  {client.stats.requests} requests, {client.stats.errors} errors, "
              f"${client.stats.cost_usd:.4f}")

    print("\nper-axis mean confidence")
    print(f"  {'arm':<12}" + "".join(f"{a:>12}" for a in DEFAULT_RUBRIC.keys))
    for row in results:
        print(f"  {row['arm']:<12}"
              + "".join(f"{row['per_axis_confidence'][a]:>12.3f}" for a in DEFAULT_RUBRIC.keys))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"noise": {"score_delta": noise, "lane_agreement": lane_noise},
                    "arms": results, "scored": scored_by_arm}, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main(json.loads(SAMPLE.read_text(encoding="utf-8"))))
