"""Pairwise comparison of the A/B arms. Reads the recording, no network.

    uv run python -m experiments.paired

All four arms scored the same 300 comments in the same order, so every contrast
is paired per comment. This script exists because the first reading of that
experiment went off aggregate rates, and a lane-agreement move of 0.793 -> 0.800
turned out to be two comments.

Two comparisons, both paired:

  accuracy    of the comments where two arms disagree, which one is right more
              often? Ties carry no information and are discarded, which is the
              McNemar construction. A near-even discordant split means the arms
              are differently wrong, not better and worse.

  confidence  mean of the per-comment difference, with its standard error.
              Reported per axis, because the aggregate hides sign flips: the
              v1 -> v2 rubric change helps on_topic and hurts hostility.
"""

import json
import statistics
from pathlib import Path

import config
from judge.rubric import DEFAULT_RUBRIC

AB = Path("experiments/out/rubric_ab.json")
SAMPLE = Path("data/jigsaw/sample.json")

CONTRASTS = [
    ("v1/index", "v1/quoted", "addressing, holding rubric at v1"),
    ("v2/index", "v2/quoted", "addressing, holding rubric at v2"),
    ("v1/quoted", "v2/quoted", "rubric, holding addressing at quoted"),
    ("v1/index", "v2/index", "rubric, holding addressing at index"),
]


def main() -> None:
    scored = json.loads(AB.read_text(encoding="utf-8"))["scored"]
    labels = {
        c["id"]: c["label_toxic"]
        for c in json.loads(SAMPLE.read_text(encoding="utf-8"))
    }

    def called_toxic(entry: dict) -> bool:
        # Civil Comments' label is insult and abuse, which is the hostility axis
        # alone. The other three measure Reddit-shaped things the label has no
        # counterpart for.
        return entry["scores"]["hostility"] >= config.SEVERITY_THRESHOLD

    print("ACCURACY, paired (discordant pairs only)")
    print("-" * 74)
    for left, right, what in CONTRASTS:
        ids = set(scored[left]) & set(scored[right])
        only_left = only_right = 0
        for cid in ids:
            lhs = called_toxic(scored[left][cid]) == labels[cid]
            rhs = called_toxic(scored[right][cid]) == labels[cid]
            only_left += lhs and not rhs
            only_right += rhs and not lhs
        total = only_left + only_right
        verdict = "even split - no evidence either way" if total and min(
            only_left, only_right
        ) / total > 0.35 else "leans " + (right if only_right > only_left else left)
        print(f"  {what:<38} {only_left:>3} : {only_right:<3} of {total:<3}  {verdict}")

    print("\nCONFIDENCE, paired mean delta +/- sem, per axis")
    print("-" * 74)
    for left, right, what in CONTRASTS[:3]:
        print(f"  {what}")
        ids = set(scored[left]) & set(scored[right])
        for axis in DEFAULT_RUBRIC.keys:
            deltas = [
                scored[right][c]["confidences"][axis] - scored[left][c]["confidences"][axis]
                for c in ids
            ]
            mean = statistics.mean(deltas)
            sem = statistics.stdev(deltas) / len(deltas) ** 0.5
            stars = "***" if abs(mean) > 3 * sem else ("*" if abs(mean) > 2 * sem else "ns")
            print(f"    {axis:<11} {mean:+.3f} +/- {sem:.3f}  {stars}")
        print()


if __name__ == "__main__":
    main()
