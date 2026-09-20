"""Lane policy. Runs with no network and no key — that is the point of keeping
`judge.lanes` a pure function.
"""

import pytest

import config
from judge.lanes import Gate, Lane, decisive_confidence, lane

CONFIDENT = dict.fromkeys(["hostility", "contempt", "substance", "on_topic"], 0.95)
FINE = {"hostility": 0.0, "contempt": 1.0, "substance": 8.0, "on_topic": 9.0}
UNSURE = config.CONFIDENCE_FLOOR - 0.01


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


def test_low_confidence_on_a_severity_axis_beats_a_clean_score():
    """The invariant. A low-confidence 'clearly fine' still goes to the Pen."""
    assert lane(FINE, CONFIDENT | {"contempt": UNSURE}) == Lane.PEN


def test_low_confidence_beats_an_over_the_line_score():
    assert lane(FINE | {"hostility": 10.0}, CONFIDENT | {"hostility": UNSURE}) == Lane.PEN


def test_gate_never_averages_away_one_shaky_severity_axis():
    """Both severity axes carry every verdict, so either one can pen a comment."""
    assert lane(FINE, CONFIDENT | {"hostility": 0.10}) == Lane.PEN
    assert lane(FINE, CONFIDENT | {"contempt": 0.10}) == Lane.PEN


def test_quality_axes_cannot_veto_a_decision_they_had_no_part_in():
    """The measured fix. An unsure on_topic says nothing about a clear hostility call."""
    clearly_hostile = FINE | {"hostility": 9.4}
    assert lane(clearly_hostile, CONFIDENT | {"on_topic": 0.10}) == Lane.BOUNCED
    # ...but under the conservative gate it still pens, and that stays available.
    assert lane(clearly_hostile, CONFIDENT | {"on_topic": 0.10}, Gate.MIN_ALL) == Lane.PEN


def test_quality_axes_do_gate_when_the_quality_rule_is_what_fired():
    noise = {"hostility": 0.0, "contempt": 0.0, "substance": 1.0, "on_topic": 1.0}
    assert lane(noise, CONFIDENT) == Lane.BOUNCED
    assert lane(noise, CONFIDENT | {"on_topic": UNSURE}) == Lane.PEN


def test_decisive_confidence_widens_only_when_the_quality_rule_fires():
    noise = {"hostility": 0.0, "contempt": 0.0, "substance": 1.0, "on_topic": 1.0}
    shaky_quality = CONFIDENT | {"substance": 0.20}
    assert decisive_confidence(FINE, shaky_quality) == pytest.approx(0.95)
    assert decisive_confidence(noise, shaky_quality) == pytest.approx(0.20)


def test_missing_confidences_go_to_the_pen():
    """An errored judgement degrades into 'a human looks at it', never into Approved."""
    assert lane(FINE, {}) == Lane.PEN
