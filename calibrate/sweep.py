"""TECHNICAL_SPEC §7 — pick the confidence floor from a curve, not from taste.

    uv run python -m calibrate.sweep            # print the table
    uv run python -m calibrate.sweep --write    # also refresh calibrate/curve.json

Offline. Reads a recorded run and the human labels; no network, no key.

This is the evidence behind PRD §2's answer to the accuracy objection. The claim
is not "this model is right"; it is "this model knows when it isn't". A curve is
the only way to show that, and the curve is what the second tab renders.

It sweeps two things, because the second turned out to matter more:

  floor   the confidence threshold, 0.50 to 0.99
  gate    *which* confidences the floor is applied to

  min2      both severity axes must clear the floor (scalar confidence)
  mean2     their average must clear it
  decision  probability mass on one side of the line, not concentration across
            levels — the gate the app actually ships (FINDINGS §13)

**Agreement alone overstates the result, so this reports the baseline beside
it.** Raising the floor removes *toxic* comments from the auto-handled subset,
not just uncertain ones — 30% toxic at floor 0.50, 5.7% at 0.99 — and a subset
that is one-class is easy. `always` is what "approve everything" scores on the
same subset, which is an implementable strategy rather than an oracle because
the benign class is the majority at every floor. `lift` is the difference and
it is the honest number. `balacc` is the mean of TPR and TNR, immune to the
shift entirely, and it *peaks near floor 0.65 and falls after it*. See
`calibrate/baseline.py` for the full analysis, and `RESEARCH.md` §5.

**What this measures, and what it does not.** Agreement is against Civil
Comments' human `toxicity` label, which is overwhelmingly insult and abuse — so
it is a fair test of `hostility` and `contempt` and no test at all of
`substance` or `on_topic`. Those two need thread context the labelled corpus
does not have (FINDINGS §5, §9), which is why the recorded run covers the two
intrinsic axes only. The curve is honest about the axes it can score.
"""

import argparse
import json
import statistics
from pathlib import Path

import config

RECORDING = Path("experiments/out/gate_ab.json")
SAMPLE = Path("data/jigsaw/sample.json")
CURVE = Path("calibrate/curve.json")

FLOORS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99]
SEVERITY_AXES = ("hostility", "contempt")


def p_over(entry: dict, axis: str) -> float:
    probabilities = entry[axis]["probabilities"]
    return sum(p for level, p in probabilities.items() if int(level) >= config.OVER_AT_LEVEL)


def called_toxic(entry: dict) -> bool:
    """The lane decision, restricted to the axes this corpus can score."""
    return any(p_over(entry, axis) > 0.5 for axis in SEVERITY_AXES)


def gate_value(entry: dict, gate: str) -> float:
    scalars = [entry[axis]["confidence"] for axis in SEVERITY_AXES]
    if gate == "min2":
        return min(scalars)
    if gate == "mean2":
        return statistics.mean(scalars)
    if gate == "decision":
        return min(max(p_over(entry, a), 1 - p_over(entry, a)) for a in SEVERITY_AXES)
    raise ValueError(gate)


GATES = ("min2", "mean2", "decision")


def sweep(scored: dict, labels: dict[str, bool]) -> dict:
    out: dict = {"n": len(scored), "floors": FLOORS, "gates": {}}
    for gate in GATES:
        series = []
        for floor in FLOORS:
            auto = [e for e in scored.values() if gate_value(e, gate) >= floor]
            if not auto:
                series.append(
                    {"floor": floor, "auto": 0.0, "agreement": None,
                     "always": None, "lift": None, "balanced": None,
                     "precision": None, "recall": None, "n": 0}
                )
                continue
            ids = [c for c, e in scored.items() if gate_value(e, gate) >= floor]
            tp = fp = fn = correct = 0
            for cid in ids:
                predicted, actual = called_toxic(scored[cid]), labels[cid]
                correct += predicted == actual
                tp += predicted and actual
                fp += predicted and not actual
                fn += (not predicted) and actual
            n = len(ids)
            tn = correct - tp
            agreement = correct / n
            # the majority class is "not toxic" at every floor, so this is
            # simply "approve everything" — something you could actually ship
            always = (n - tp - fn) / n
            tpr = tp / (tp + fn) if tp + fn else None
            tnr = tn / (tn + fp) if tn + fp else None
            series.append(
                {
                    "floor": floor,
                    "auto": len(auto) / len(scored),
                    "agreement": agreement,
                    "always": always,
                    "lift": agreement - always,
                    "balanced": (tpr + tnr) / 2 if tpr is not None and tnr is not None else None,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tpr,
                    "n": n,
                }
            )
        out["gates"][gate] = series
    return out


def cell(point: dict, key: str, spec: str = ".3f") -> str:
    value = point[key]
    return f"{value:{spec}}" if value is not None else "—"


def knee(series: list[dict]) -> dict | None:
    """The best **lift over always-approve**, among points that keep most
    traffic automated.

    This used to maximise raw agreement, and that was wrong for the reason the
    module docstring gives: agreement rises as the auto-handled subset loses
    its toxic comments, so maximising it picks the strictest floor available
    and calls a subset that is 94% one class a success. It chose floor 0.95,
    where the gate beats doing nothing by 0.017.

    Lift is the honest target and it picks floor 0.60–0.65, which is also where
    balanced accuracy peaks. **The app ships 0.85 anyway** — a deliberate
    choice of a different objective, precision on automated actions, which
    keeps climbing past the lift peak (0.82 at 0.65, 1.00 at 0.90). The knee is
    reported so the gap between the two is visible rather than implied.
    """
    candidates = [p for p in series if 0.55 <= p["auto"] <= 0.95 and p["lift"] is not None]
    return max(candidates, key=lambda p: p["lift"]) if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="refresh calibrate/curve.json")
    args = parser.parse_args()

    scored = json.loads(RECORDING.read_text(encoding="utf-8"))["jigsaw"]
    labels = {
        c["id"]: c["label_toxic"]
        for c in json.loads(SAMPLE.read_text(encoding="utf-8"))
    }
    curve = sweep(scored, labels)

    print(f"{curve['n']} labelled comments, {sum(labels.values())} toxic\n")
    for gate, series in curve["gates"].items():
        print(f"  {gate}")
        print(f"    {'floor':>6} {'auto':>6} {'agree':>7} {'always':>7} {'lift':>7} "
              f"{'balacc':>7} {'prec':>6} {'rec':>6} {'n':>5}")
        for point in series:
            print(f"    {point['floor']:>6.2f} {point['auto']:>5.0%} "
                  f"{cell(point, 'agreement'):>7} {cell(point, 'always'):>7} "
                  f"{cell(point, 'lift', '+.3f'):>7} {cell(point, 'balanced'):>7} "
                  f"{cell(point, 'precision', '.2f'):>6} "
                  f"{cell(point, 'recall', '.2f'):>6} {point['n']:>5}")
        best = knee(series)
        if best:
            print(f"    knee: floor {best['floor']:.2f} → "
                  f"{best['auto']:.0%} auto-handled, {best['agreement']:.3f} agreement, "
                  f"{best['lift']:+.3f} over always-approve")
        print()

    if args.write:
        curve["shipped"] = {
            "gate": config.CONFIDENCE_GATE,
            "floor": config.CONFIDENCE_FLOOR,
        }
        CURVE.write_text(json.dumps(curve, indent=2), encoding="utf-8")
        print(f"wrote {CURVE}")


if __name__ == "__main__":
    main()
