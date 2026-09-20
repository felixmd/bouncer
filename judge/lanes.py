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
from enum import StrEnum

import config


class Lane(StrEnum):
    APPROVED = "approved"
    BOUNCED = "bounced"
    PEN = "pen"


class Gate(StrEnum):
    MIN_ALL = "min_all"
    DECISIVE = "decisive"


# config holds the plain string so it stays importable from anywhere without a
# cycle; resolve it to the enum once, here, so a typo fails at import.
DEFAULT_GATE = Gate(config.CONFIDENCE_GATE)


def _quality_rule_fires(scores: Mapping[str, float]) -> bool:
    """Low substance *and* off topic. Vacuous-but-relevant is not the bouncer's job."""
    return (
        scores["substance"] <= config.LOW_SUBSTANCE_THRESHOLD
        and scores["on_topic"] <= config.LOW_ON_TOPIC_THRESHOLD
    )


def _over_the_line(scores: Mapping[str, float]) -> bool:
    return (
        scores["hostility"] >= config.SEVERITY_THRESHOLD
        or scores["contempt"] >= config.SEVERITY_THRESHOLD
        or _quality_rule_fires(scores)
    )


def decisive_confidence(
    scores: Mapping[str, float], confidences: Mapping[str, float]
) -> float:
    """The weakest confidence among the axes this comment's lane actually rests on.

    The two severity axes always count: an Approved verdict is a claim that
    neither of them crossed the line, so both have to be trusted. The quality
    axes count only when the low-quality rule is what fired, because otherwise
    they did not contribute to the outcome.
    """
    relevant = [confidences["hostility"], confidences["contempt"]]
    if _quality_rule_fires(scores):
        relevant += [confidences["substance"], confidences["on_topic"]]
    return min(relevant)


def confidence_for(
    scores: Mapping[str, float], confidences: Mapping[str, float], gate: Gate
) -> float:
    if gate is Gate.MIN_ALL:
        return min(confidences.values())
    return decisive_confidence(scores, confidences)


def lane(
    scores: Mapping[str, float],
    confidences: Mapping[str, float],
    gate: Gate = DEFAULT_GATE,
    floor: float = config.CONFIDENCE_FLOOR,
) -> Lane:
    """Route one comment. Scores are on the normalised 0-10 scale."""
    if not confidences or confidence_for(scores, confidences, gate) < floor:
        return Lane.PEN
    return Lane.BOUNCED if _over_the_line(scores) else Lane.APPROVED
