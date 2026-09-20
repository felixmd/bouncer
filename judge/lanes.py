"""Scores plus confidence to a lane.

Pure function. No I/O, no model calls, no clock. The model returns scores and
confidence; the *policy* is ours, and it stays here where it can be unit-tested
without a key.

On the confidence gate
----------------------
The gate is checked first and that has not changed: a low-confidence "clearly
fine" still goes to the Pen, which is the entire mechanism this demo exists to
show.

What changed is *which* confidences the floor is applied to. The original rule
took `min()` across all four axes. Measured against 300 labelled Civil Comments
that put 99-100% of traffic in the Pen — four axes, each averaging around
0.6-0.7 confidence, and you take the worst one every time. Nothing is left.

The fix is not a lower floor. The Score docs are explicit that if there is
nowhere for uncertain cases to go you will be tempted to lower the threshold
instead, and then you have rebuilt the thing you were escaping. The fix is to
stop letting an axis veto a decision it had no part in. If hostility is 9.4 with
confidence 0.93, the comment is bounced on hostility alone, and whether the
model was unsure about `on_topic` is not information about that decision.

`DECISIVE` gates on the axes the lane rule actually consulted for this comment.
`MIN_ALL` is kept because it is the conservative reading and the UI exposes the
choice. See calibrate/sweep.py for the curve both were picked from.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import config


class Lane(StrEnum):
    APPROVED = "approved"
    BOUNCED = "bounced"
    PEN = "pen"


class Gate(StrEnum):
    MIN_ALL = "min_all"
    DECISIVE = "decisive"
    DECISION = "decision"


# config holds the plain string so it stays importable from anywhere without a
# cycle; resolve it to the enum once, here, so a typo fails at import.
DEFAULT_GATE = Gate(config.CONFIDENCE_GATE)


@dataclass(frozen=True)
class Policy:
    """Every knob the lane decision turns on, in one value.

    Passed in rather than read from `config` at call time so the UI can move a
    threshold without mutating module globals — and, more importantly, so that
    **re-sorting the backlog under a new policy costs nothing.** The scores and
    probabilities are already stored; a new floor or gate is a pure recompute,
    no model call. That is what makes the threshold slider in the UI free.
    """

    gate: Gate = DEFAULT_GATE
    floor: float = config.CONFIDENCE_FLOOR
    severity: float = config.SEVERITY_THRESHOLD
    low_substance: float = config.LOW_SUBSTANCE_THRESHOLD
    low_on_topic: float = config.LOW_ON_TOPIC_THRESHOLD


DEFAULT_POLICY = Policy()


def _quality_rule_fires(
    scores: Mapping[str, float], policy: Policy = DEFAULT_POLICY
) -> bool:
    """Low substance *and* off topic. Vacuous-but-relevant is not the bouncer's job."""
    return (
        scores["substance"] <= policy.low_substance
        and scores["on_topic"] <= policy.low_on_topic
    )


def _over_the_line(
    scores: Mapping[str, float], policy: Policy = DEFAULT_POLICY
) -> bool:
    return (
        scores["hostility"] >= policy.severity
        or scores["contempt"] >= policy.severity
        or _quality_rule_fires(scores, policy)
    )


def _over_the_line_p(probabilities: Mapping[str, Mapping[int, float]]) -> bool:
    """The same verdict, read off the distribution rather than the mean.

    Used with `Gate.DECISION` so that the rule and the gate are asking about the
    *same line*. They were not: the rule thresholded the mean at 6.0 while the
    gate asked about `P(level >= 3)`, which is 7.5 on the normalised scale. A
    comment scoring 6.7 was therefore "over the line" by the rule while the gate
    reported how unsure it was about a stricter line nobody was applying.

    Accuracy-neutral — the two rules disagreed on five of 300 labelled comments
    (FINDINGS §13) — so this is bought purely for coherence.
    """
    severity = any(
        _p_side(probabilities[axis], axis) > 0.5 for axis in ("hostility", "contempt")
    )
    quality = all(
        _p_side(probabilities[axis], axis) > 0.5 for axis in ("substance", "on_topic")
    )
    return severity or quality


def decisive_confidence(
    scores: Mapping[str, float],
    confidences: Mapping[str, float],
    policy: Policy = DEFAULT_POLICY,
) -> float:
    """The weakest confidence among the axes this comment's lane actually rests on.

    The two severity axes always count: an Approved verdict is a claim that
    neither of them crossed the line, so both have to be trusted. The quality
    axes count only when the low-quality rule is what fired, because otherwise
    they did not contribute to the outcome.
    """
    relevant = [confidences["hostility"], confidences["contempt"]]
    if _quality_rule_fires(scores, policy):
        relevant += [confidences["substance"], confidences["on_topic"]]
    return min(relevant)


def _p_side(probabilities: Mapping[int, float], axis: str) -> float:
    """Probability mass on the over-the-line side of this axis's threshold."""
    if axis in ("hostility", "contempt"):
        return sum(p for level, p in probabilities.items() if int(level) >= config.OVER_AT_LEVEL)
    return sum(p for level, p in probabilities.items() if int(level) <= config.LOW_AT_LEVEL)


def decision_confidence(
    scores: Mapping[str, float],
    probabilities: Mapping[str, Mapping[int, float]],
    policy: Policy = DEFAULT_POLICY,
) -> float:
    """How sure the model is which *side of the line* this falls, on the axes
    that carried the verdict.

    `ScoreAnswer.confidence` answers a different question: how concentrated the
    probability is across the five levels. A comment spread evenly over levels
    0, 1 and 2 has low confidence and a completely certain decision, because
    every one of those levels is far below the line. Under a scalar gate it
    pens, and a human is asked to adjudicate something the model was never
    unsure about.

    Measured, this is **not** more discriminative than the scalar gate — at
    matched auto-handled volume the two agree with human labels within ±0.03,
    with no consistent direction, and their rankings correlate at 0.845. It is
    the same curve. What it buys is that the number means the thing the lane
    turns on, so a floor of 0.85 reads as "85% of the probability is on one side
    of the line" rather than as an arbitrary setting that happened to leave 17%
    of traffic auto-handled. See FINDINGS §13.
    """
    axes = ["hostility", "contempt"]
    if _quality_rule_fires(scores, policy):
        axes += ["substance", "on_topic"]
    values = []
    for axis in axes:
        p = _p_side(probabilities[axis], axis)
        values.append(max(p, 1.0 - p))
    return min(values)


def confidence_for(
    scores: Mapping[str, float],
    confidences: Mapping[str, float],
    gate: Gate,
    probabilities: Mapping[str, Mapping[int, float]] | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> float:
    if gate is Gate.MIN_ALL:
        return min(confidences.values())
    if gate is Gate.DECISION:
        if probabilities is None:
            raise ValueError("Gate.DECISION needs per-level probabilities")
        return decision_confidence(scores, probabilities, policy)
    return decisive_confidence(scores, confidences, policy)


def lane(
    scores: Mapping[str, float],
    confidences: Mapping[str, float],
    probabilities: Mapping[str, Mapping[int, float]] | None = None,
    gate: Gate | None = None,
    floor: float | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> Lane:
    """Route one comment. Scores are on the normalised 0-10 scale.

    `gate` and `floor` override the policy for a single call, which is what the
    calibration sweep wants; everything else comes from `policy`.
    """
    gate = policy.gate if gate is None else gate
    floor = policy.floor if floor is None else floor

    if not confidences:
        return Lane.PEN
    if confidence_for(scores, confidences, gate, probabilities, policy) < floor:
        return Lane.PEN
    over = (
        _over_the_line_p(probabilities)
        if gate is Gate.DECISION
        else _over_the_line(scores, policy)
    )
    return Lane.BOUNCED if over else Lane.APPROVED
