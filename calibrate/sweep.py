"""TECHNICAL_SPEC §7 — pick the confidence floor from a curve, not from taste.

    uv run python -m calibrate.sweep

Offline: reads the recorded arm from experiments/out/rubric_ab.json. No network,
no key.

It sweeps two things at once, because the experiments showed the second one
matters more than the first:

  floor   the confidence threshold, 0.50 to 0.99
  gate    *which* confidences the floor is applied to

The spec gates on `min()` across all four axes. That turns out to be the reason
99% of comments land in the Pen: four axes, each with mean confidence around
0.6-0.7, and you take the worst one every time. The floor is not the problem —
taking a minimum over four draws is.

The alternative gates are not a way of lowering the bar. They apply the same
floor to the confidences that actually carry the decision:

  min4       every axis must clear the floor (the spec's rule)
  mean4      the average must clear it
  severity   only hostility and contempt, the two axes that can bounce a
             comment on their own
  decisive   the axes the lane rule actually consulted for *this* comment: the
             severity axes always, plus substance and on_topic only when the
             low-quality rule is what fired

`decisive` is the principled one. If hostility is 9.4 with confidence 0.93,
whether the model was unsure about on_topic has no bearing on the decision that
was made, and letting it veto is not caution, it is noise.
"""

import json
import statistics
from pathlib import Path

import config
from judge.lanes import Lane

AB_PATH = Path("experiments/out/rubric_ab.json")
SAMPLE = Path("data/jigsaw/sample.json")
FLOORS = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99]
GATES = ["min4", "mean4", "severity", "decisive"]


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


def gate_value(gate: str, scores: dict[str, float], confidences: dict[str, float]) -> float:
    if gate == "min4":
        return min(confidences.values())
    if gate == "mean4":
        return statistics.mean(confidences.values())
    if gate == "severity":
        return min(confidences["hostility"], confidences["contempt"])
    if gate == "decisive":
        relevant = [confidences["hostility"], confidences["contempt"]]
        quality_rule_fired = (
            scores["substance"] <= config.LOW_SUBSTANCE_THRESHOLD
            and scores["on_topic"] <= config.LOW_ON_TOPIC_THRESHOLD
        )
        if quality_rule_fired:
            relevant += [confidences["substance"], confidences["on_topic"]]
        return min(relevant)
    raise ValueError(gate)


def main() -> None:
    data = json.loads(AB_PATH.read_text(encoding="utf-8"))
    labels = {
        c["id"]: c["label_toxic"]
        for c in json.loads(SAMPLE.read_text(encoding="utf-8"))
    }

    arm = "v2/quoted"
    scored = data["scored"][arm]
    print(f"arm: {arm}   n={len(scored)}   "
          f"noise floor between identical runs: {data['noise']['score_delta']:.3f}/10\n")

    print(f"{'gate':<9} {'floor':>6} {'auto%':>7} {'pen%':>6} {'agree|auto':>11} "
          f"{'prec|auto':>10} {'rec|auto':>9}")
    print("-" * 64)

    best: tuple[float, str, float] | None = None
    for gate in GATES:
        for floor in FLOORS:
            auto, correct, tp, fp, fn = 0, 0, 0, 0, 0
            for cid, entry in scored.items():
                if gate_value(gate, entry["scores"], entry["confidences"]) < floor:
                    continue
                auto += 1
                predicted = content_lane(entry["scores"]) is Lane.BOUNCED
                actual = labels[cid]
                correct += predicted == actual
                tp += predicted and actual
                fp += predicted and not actual
                fn += not predicted and actual
            if not auto:
                print(f"{gate:<9} {floor:>6.2f} {'0%':>7} {'100%':>6}  everything penned")
                continue
            share = auto / len(scored)
            agree = correct / auto
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            print(f"{gate:<9} {floor:>6.2f} {share:>6.0%} {1 - share:>6.0%} "
                  f"{agree:>11.3f} {precision:>10.2f} {recall:>9.2f}")
            # The PRD wants most traffic auto-handled with a visible, non-empty
            # Pen. Score the knee on that shape rather than on agreement alone.
            if 0.80 <= share <= 0.95 and (best is None or agree > best[0]):
                best = (agree, gate, floor)
        print()

    if best:
        agree, gate, floor = best
        print(f"knee: gate={gate} floor={floor:.2f} -> {agree:.3f} agreement on the "
              f"auto-handled share, Pen between 5% and 20%")
    else:
        print("no (gate, floor) pair lands in the 80-95% auto-handled band")


if __name__ == "__main__":
    main()
