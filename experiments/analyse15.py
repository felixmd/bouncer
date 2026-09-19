"""Post-mortem on the smoke run. Reads the recording, touches no network.

    uv run python -m experiments.analyse15
"""

import json
import statistics
from pathlib import Path

import config
from judge.lanes import Lane
from judge.rubric import DEFAULT_RUBRIC

RESULTS = json.loads((Path(__file__).parent / "out" / "smoke15.json").read_text(encoding="utf-8"))
AXES = DEFAULT_RUBRIC.keys


def content_lane(scores: dict[str, float]) -> Lane:
    """The lane the scores alone would give, with the confidence gate removed.

    Not a policy — a diagnostic. It separates "the axes are wrong" from
    "the confidence floor is wrong", which the live run cannot distinguish
    because the gate fires first and hides everything behind it.
    """
    over = (
        scores["hostility"] >= config.SEVERITY_THRESHOLD
        or scores["contempt"] >= config.SEVERITY_THRESHOLD
        or (
            scores["substance"] <= config.LOW_SUBSTANCE_THRESHOLD
            and scores["on_topic"] <= config.LOW_ON_TOPIC_THRESHOLD
        )
    )
    return Lane.BOUNCED if over else Lane.APPROVED


print("=" * 92)
print("PER-AXIS CONFIDENCE")
print("-" * 92)
print(f"{'axis':<12} {'min':>7} {'p25':>7} {'median':>7} {'p75':>7} {'max':>7}   {'<0.80':>6}")
for axis in AXES:
    vals = sorted(r["confidences"][axis] for r in RESULTS)
    q = statistics.quantiles(vals, n=4)
    below = sum(1 for v in vals if v < config.CONFIDENCE_FLOOR)
    print(f"{axis:<12} {vals[0]:>7.3f} {q[0]:>7.3f} {q[1]:>7.3f} {q[2]:>7.3f} {vals[-1]:>7.3f}"
          f"   {below:>3}/{len(vals)}")

print("\nwhich axis is the binding minimum (i.e. sends the comment to the Pen)")
print("-" * 92)
binder: dict[str, int] = {}
for r in RESULTS:
    worst = min(AXES, key=lambda a: r["confidences"][a])
    binder[worst] = binder.get(worst, 0) + 1
for axis, n in sorted(binder.items(), key=lambda kv: -kv[1]):
    print(f"  {axis:<12} {n:>2}/{len(RESULTS)}")

print("\n" + "=" * 92)
print("IS CONFIDENCE CALIBRATED, OR JUST LOW?")
print("-" * 92)
print("If low confidence tracks mid-rubric scores, the model is telling us something")
print("real. If it is flat, the floor is just a volume knob.\n")
print(f"{'axis':<12} {'conf @ decisive score':>24} {'conf @ mid score':>18}")
for axis in AXES:
    decisive, mid = [], []
    for r in RESULTS:
        raw = r["raw_scores"][axis]
        dist_to_level = abs(raw - round(raw))
        (mid if dist_to_level > 0.25 else decisive).append(r["confidences"][axis])
    d = f"{statistics.mean(decisive):.3f} (n={len(decisive)})" if decisive else "-"
    m = f"{statistics.mean(mid):.3f} (n={len(mid)})" if mid else "-"
    print(f"{axis:<12} {d:>24} {m:>18}")

print("\n" + "=" * 92)
print("CONTENT DECISION WITH THE CONFIDENCE GATE LIFTED")
print("-" * 92)
print("Does the four-axis policy agree with the human prior when the gate is not")
print("swallowing every comment?\n")
hits = 0
for r in RESULTS:
    cl = content_lane(r["scores"])
    prior = r["expect"]
    # A PEN prior means "I would not have called this myself" — unscoreable here.
    mark = "-" if prior == "pen" else ("ok" if cl.value == prior else "MISS")
    if mark == "ok":
        hits += 1
    print(f"  {r['id']}  scores->{cl.value:<9} prior->{prior:<9} {mark:<5} {r['note'][:46]}")
scoreable = sum(1 for r in RESULTS if r["expect"] != "pen")
print(f"\n  agreement on the {scoreable} comments I was willing to call: "
      f"{hits}/{scoreable} ({hits / scoreable:.0%})")

print("\n" + "=" * 92)
print("WHERE THE FLOOR WOULD HAVE TO SIT")
print("-" * 92)
print(f"{'floor':>6} {'pen':>5} {'approved':>9} {'bounced':>8}   {'auto-handled':>12}")
for floor in [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]:
    pen = approved = bounced = 0
    for r in RESULTS:
        if min(r["confidences"].values()) < floor:
            pen += 1
        elif content_lane(r["scores"]) is Lane.BOUNCED:
            bounced += 1
        else:
            approved += 1
    auto = (approved + bounced) / len(RESULTS)
    print(f"{floor:>6.2f} {pen:>5} {approved:>9} {bounced:>8}   {auto:>11.0%}")

print("\nsame sweep, but gating on the mean confidence instead of the minimum")
print("-" * 92)
print(f"{'floor':>6} {'pen':>5} {'approved':>9} {'bounced':>8}   {'auto-handled':>12}")
for floor in [0.9, 0.8, 0.7, 0.6, 0.5]:
    pen = approved = bounced = 0
    for r in RESULTS:
        if statistics.mean(r["confidences"].values()) < floor:
            pen += 1
        elif content_lane(r["scores"]) is Lane.BOUNCED:
            bounced += 1
        else:
            approved += 1
    auto = (approved + bounced) / len(RESULTS)
    print(f"{floor:>6.2f} {pen:>5} {approved:>9} {bounced:>8}   {auto:>11.0%}")

print("\n" + "=" * 92)
print("TOKENS")
print("-" * 92)
toks = [r["input_tokens"] for r in RESULTS if r["input_tokens"]]
print(f"  mean {statistics.mean(toks):.0f} input tokens per comment at B=1")
print("  spec §3.1 assumed ~440 for a standalone comment (300 context + 140)")
print(f"  rubric text is {sum(len(lvl) for a in DEFAULT_RUBRIC.axes for lvl in a.levels)} chars, "
      f"resent on every question")
print("=" * 92)
