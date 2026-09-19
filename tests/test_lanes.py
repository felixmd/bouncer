"""Lane policy. Runs with no network and no key — that is the point of keeping
`judge.lanes` a pure function.
"""

import pytest

import config
from judge.lanes import Lane, lane

CONFIDENT = dict.fromkeys(["hostility", "contempt", "substance", "on_topic"], 0.95)
FINE = {"hostility": 0.0, "contempt": 1.0, "substance": 8.0, "on_topic": 9.0}


def test_confident_and_fine_is_approved():
    assert lane(FINE, CONFIDENT) == Lane.APPROVED


@pytest.mark.parametrize("axis", ["hostility", "contempt"])
def test_severity_on_either_axis_bounces(axis):
    scores = FINE | {axis: config.SEVERITY_THRESHOLD}
    assert lane(scores, CONFIDENT) == Lane.BOUNCED


def test_severity_is_inclusive_at_the_threshold():
    just_under = FINE | {"hostility": config.SEVERITY_THRESHOLD - 0.01}
    assert lane(just_under, CONFIDENT) == Lane.APPROVED


def test_noise_needs_both_low_substance_and_low_on_topic():
    noise = {"hostility": 0.0, "contempt": 0.0, "substance": 1.0, "on_topic": 1.0}
    assert lane(noise, CONFIDENT) == Lane.BOUNCED

    # "This. So much this." — vacuous but squarely on topic. Not the bouncer's job.
    vacuous_but_relevant = noise | {"on_topic": 9.0}
    assert lane(vacuous_but_relevant, CONFIDENT) == Lane.APPROVED


def test_low_confidence_beats_a_clean_score():
    """The invariant. A low-confidence 'clearly fine' still goes to the Pen."""
    unsure = CONFIDENT | {"substance": config.CONFIDENCE_FLOOR - 0.01}
    assert lane(FINE, unsure) == Lane.PEN


def test_low_confidence_beats_an_over_the_line_score():
    unsure = CONFIDENT | {"hostility": config.CONFIDENCE_FLOOR - 0.01}
    assert lane(FINE | {"hostility": 10.0}, unsure) == Lane.PEN


def test_confidence_gate_reads_the_minimum_not_the_mean():
    """One shaky axis is enough. Averaging would hide it."""
    one_shaky = CONFIDENT | {"on_topic": 0.10}
    assert lane(FINE, one_shaky) == Lane.PEN


def test_missing_confidences_go_to_the_pen():
    """An errored judgement degrades into 'a human looks at it', never into Approved."""
    assert lane(FINE, {}) == Lane.PEN
