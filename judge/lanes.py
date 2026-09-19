"""Scores plus confidence to a lane.

Pure function. No I/O, no model calls, no clock. The model returns scores and
confidence; the *policy* is ours, and it stays here where it can be unit-tested
without a key.
"""

from collections.abc import Mapping
from enum import StrEnum

import config


class Lane(StrEnum):
    APPROVED = "approved"
    BOUNCED = "bounced"
    PEN = "pen"


def lane(scores: Mapping[str, float], confidences: Mapping[str, float]) -> Lane:
    """Route one comment. Scores are on the normalised 0-10 scale.

    The confidence gate is checked *first* and deliberately: a low-confidence
    "clearly fine" still goes to the Pen. Short-circuiting that to reduce Pen
    volume would remove the only thing this demo exists to show.
    """
    if not confidences or min(confidences.values()) < config.CONFIDENCE_FLOOR:
        return Lane.PEN

    over_the_line = (
        scores["hostility"] >= config.SEVERITY_THRESHOLD
        or scores["contempt"] >= config.SEVERITY_THRESHOLD
        or (
            scores["substance"] <= config.LOW_SUBSTANCE_THRESHOLD
            and scores["on_topic"] <= config.LOW_ON_TOPIC_THRESHOLD
        )
    )
    return Lane.BOUNCED if over_the_line else Lane.APPROVED
