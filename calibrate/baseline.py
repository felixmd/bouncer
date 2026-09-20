"""What the calibration curve looks like once you subtract the base rate.

    uv run python -m calibrate.baseline

Offline. Reads the same recording and labels as `sweep.py`; no network, no key.

`sweep.py` reports agreement on the auto-handled subset, and it rises
monotonically with the confidence floor — 0.807 to 0.950. That was read as
"confidence predicts accuracy". It is not that simple, and this module is what
shows why.

Raising the floor does not only remove comments the model is unsure about. It
removes **toxic** comments: the auto-handled subset goes from 30% toxic at floor
0.50 to 5.7% at 0.99. A subset that is 94% one class is easy, so agreement on it
rises whether or not the model got better. Three things separate the two
explanations:

  always-approve   the accuracy of predicting "fine" for everything in that
                   same subset — an implementable strategy, not an oracle,
                   because the negative class is the majority at every floor
  balanced acc     mean of TPR and TNR, immune to the base-rate shift
  AUC              threshold-free ranking quality over the whole corpus

The answer, in one line: the lift over always-approve collapses from +0.107 to
+0.007 as the floor rises, and balanced accuracy *peaks* near floor 0.65 and
falls after it. The confidence signal is real and it is much smaller than the
agreement curve implies. See `RESEARCH.md` §5.
"""

import json
import math

from calibrate.sweep import FLOORS, RECORDING, SAMPLE, called_toxic, gate_value, p_over

GATE = "decision"
SEVERITY_AXES = ("hostility", "contempt")


def confusion(scored: dict, labels: dict[str, bool], ids: list[str]) -> dict:
    tp = fp = tn = fn = 0
    for cid in ids:
        predicted, actual = called_toxic(scored[cid]), labels[cid]
        tp += predicted and actual
        fp += predicted and not actual
        tn += (not predicted) and (not actual)
        fn += (not predicted) and actual
    n = len(ids)
    tpr = tp / (tp + fn) if tp + fn else float("nan")
    tnr = tn / (tn + fp) if tn + fp else float("nan")
    precision = tp / (tp + fp) if tp + fp else float("nan")
    denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return {
        "n": n,
        "toxic_share": (tp + fn) / n,
        "tpr": tpr,
        "tnr": tnr,
        "balanced": (tpr + tnr) / 2,
        "precision": precision,
        "f1": (
            2 * precision * tpr / (precision + tpr)
            if precision + tpr
            else float("nan")
        ),
        "mcc": (tp * tn - fp * fn) / denominator if denominator else float("nan"),
        "agreement": (tp + tn) / n,
        # the majority class is "not toxic" at every floor, so this is simply
        # "approve everything" — a strategy anyone could actually ship
        "always_approve": (tn + fp) / n,
    }


def auc(scored: dict, labels: dict[str, bool], score) -> float:
    """Mann-Whitney U. Threshold-free, base-rate immune, and the number to
    quote at anyone comparing this to a trained classifier."""
    positive = [score(e) for cid, e in scored.items() if labels[cid]]
    negative = [score(e) for cid, e in scored.items() if not labels[cid]]
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0 for p in positive for n in negative
    )
    return wins / (len(positive) * len(negative))


def severity(entry: dict) -> float:
    return max(p_over(entry, axis) for axis in SEVERITY_AXES)


def main() -> None:
    scored = json.loads(RECORDING.read_text(encoding="utf-8"))["jigsaw"]
    labels = {
        c["id"]: c["label_toxic"]
        for c in json.loads(SAMPLE.read_text(encoding="utf-8"))
    }

    toxic = sum(labels.values())
    print(f"{len(scored)} labelled comments, {toxic} toxic ({toxic / len(scored):.1%})")
    print("\nranking quality over the whole corpus, no threshold anywhere:")
    print(f"  AUC  max P(level>=3) over both severity axes : {auc(scored, labels, severity):.3f}")
    for axis in SEVERITY_AXES:
        value = auc(scored, labels, lambda e, a=axis: p_over(e, a))
        print(f"  AUC  {axis:<9} alone                          : {value:.3f}")

    print(
        f"\n{'floor':>6} {'auto':>6} {'n':>5} {'toxic':>6} {'agree':>7} "
        f"{'always':>7} {'lift':>7} {'balacc':>7} {'F1':>6} {'MCC':>7} "
        f"{'TPR':>6} {'TNR':>6}"
    )
    for floor in FLOORS:
        ids = [c for c, e in scored.items() if gate_value(e, GATE) >= floor]
        if len(ids) < 20:
            continue
        m = confusion(scored, labels, ids)
        print(
            f"{floor:6.2f} {m['n'] / len(scored):6.1%} {m['n']:5d} "
            f"{m['toxic_share']:6.1%} {m['agreement']:7.3f} "
            f"{m['always_approve']:7.3f} "
            f"{m['agreement'] - m['always_approve']:+7.3f} "
            f"{m['balanced']:7.3f} {m['f1']:6.3f} {m['mcc']:7.3f} "
            f"{m['tpr']:6.3f} {m['tnr']:6.3f}"
        )

    print(
        "\nagreement rises because the subset gets easier. balanced accuracy and"
        "\nF1 peak near floor 0.65 and fall after it — above that the gate is"
        "\nbuying class balance, not skill. RESEARCH.md section 5."
    )


if __name__ == "__main__":
    main()
