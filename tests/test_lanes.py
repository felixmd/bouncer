"""Lane policy. Runs with no network and no key — that is the point of keeping
`judge.lanes` a pure function.

Each test names the gate it exercises rather than leaning on the configured
default, so changing `config.CONFIDENCE_GATE` does not silently rewrite what
these assert.
"""

import pytest

import config
from judge.lanes import Gate, Lane, decision_confidence, decisive_confidence, lane

AXES = ["hostility", "contempt", "substance", "on_topic"]
CONFIDENT = dict.fromkeys(AXES, 0.95)
FINE = {"hostility": 0.0, "contempt": 1.0, "substance": 8.0, "on_topic": 9.0}
NOISE = {"hostility": 0.0, "contempt": 0.0, "substance": 1.0, "on_topic": 1.0}
UNSURE = config.CONFIDENCE_FLOOR - 0.01


def decisive(scores, confidences):
    return lane(scores, confidences, None, Gate.DECISIVE)


# --- the content rules, independent of any gate ----------------------------

def test_confident_and_fine_is_approved():
    assert decisive(FINE, CONFIDENT) == Lane.APPROVED


@pytest.mark.parametrize("axis", ["hostility", "contempt"])
def test_severity_on_either_axis_bounces(axis):
    assert decisive(FINE | {axis: config.SEVERITY_THRESHOLD}, CONFIDENT) == Lane.BOUNCED


def test_severity_is_inclusive_at_the_threshold():
    just_under = FINE | {"hostility": config.SEVERITY_THRESHOLD - 0.01}
    assert decisive(just_under, CONFIDENT) == Lane.APPROVED


def test_noise_needs_both_low_substance_and_low_on_topic():
    assert decisive(NOISE, CONFIDENT) == Lane.BOUNCED
    # "This. So much this." — vacuous but squarely on topic. Not the bouncer's job.
    assert decisive(NOISE | {"on_topic": 9.0}, CONFIDENT) == Lane.APPROVED


def test_missing_confidences_go_to_the_pen():
    """An errored judgement degrades into 'a human looks at it', never into Approved."""
    assert decisive(FINE, {}) == Lane.PEN


# --- the decisive gate: scalar confidence, on the axes that decided --------

def test_low_confidence_on_a_severity_axis_beats_a_clean_score():
    """The invariant. A low-confidence 'clearly fine' still goes to the Pen."""
    assert decisive(FINE, CONFIDENT | {"contempt": UNSURE}) == Lane.PEN


def test_low_confidence_beats_an_over_the_line_score():
    assert decisive(FINE | {"hostility": 10.0}, CONFIDENT | {"hostility": UNSURE}) == Lane.PEN


def test_gate_never_averages_away_one_shaky_severity_axis():
    """Both severity axes carry every verdict, so either one can pen a comment."""
    assert decisive(FINE, CONFIDENT | {"hostility": 0.10}) == Lane.PEN
    assert decisive(FINE, CONFIDENT | {"contempt": 0.10}) == Lane.PEN


def test_quality_axes_cannot_veto_a_verdict_they_had_no_part_in():
    """An unsure on_topic says nothing about a clear hostility call."""
    clearly_hostile = FINE | {"hostility": 9.4}
    assert decisive(clearly_hostile, CONFIDENT | {"on_topic": 0.10}) == Lane.BOUNCED
    # ...but the conservative gate still pens it, and that stays available.
    assert lane(clearly_hostile, CONFIDENT | {"on_topic": 0.10}, None, Gate.MIN_ALL) == Lane.PEN


def test_quality_axes_do_gate_when_the_quality_rule_is_what_fired():
    assert decisive(NOISE, CONFIDENT | {"on_topic": UNSURE}) == Lane.PEN


def test_decisive_confidence_widens_only_when_the_quality_rule_fires():
    shaky_quality = CONFIDENT | {"substance": 0.20}
    assert decisive_confidence(FINE, shaky_quality) == pytest.approx(0.95)
    assert decisive_confidence(NOISE, shaky_quality) == pytest.approx(0.20)


# --- the decision gate: probability mass on one side of the line -----------
#
# Severity axes cross at OVER_AT_LEVEL (3), quality axes at LOW_AT_LEVEL (1),
# so "straddling the line" is a different pair of levels for each.

def dist(**by_level: float) -> dict[int, float]:
    return {int(level[1:]): p for level, p in by_level.items()}


SEVERITY_CLEARLY_UNDER = dist(l0=0.95, l1=0.05)
SEVERITY_SPREAD_UNDER = dist(l0=0.34, l1=0.33, l2=0.33)  # unsure level, sure side
SEVERITY_STRADDLING = dist(l2=0.50, l3=0.50)
QUALITY_CLEARLY_FINE = dist(l3=0.10, l4=0.90)
QUALITY_CLEARLY_LOW = dist(l0=0.95, l1=0.05)
QUALITY_STRADDLING = dist(l1=0.50, l2=0.50)


def probs(**overrides) -> dict[str, dict[int, float]]:
    base = {
        "hostility": SEVERITY_CLEARLY_UNDER,
        "contempt": SEVERITY_CLEARLY_UNDER,
        "substance": QUALITY_CLEARLY_FINE,
        "on_topic": QUALITY_CLEARLY_FINE,
    }
    return base | overrides


def decision(scores, probabilities):
    return lane(scores, CONFIDENT, probabilities, Gate.DECISION)


def test_spread_across_levels_below_the_line_is_a_certain_decision():
    """The case the gate exists for. Probability split evenly across levels 0,
    1 and 2 is low scalar confidence and a completely certain decision, because
    none of those levels is over the line."""
    spread = probs(hostility=SEVERITY_SPREAD_UNDER)
    assert decision_confidence(FINE, spread) == pytest.approx(1.0)
    assert decision(FINE, spread) == Lane.APPROVED
    # The scalar gate sees an unsure axis and pens the same comment.
    assert decisive(FINE, CONFIDENT | {"hostility": 0.34}) == Lane.PEN


def test_probability_straddling_the_line_still_pens():
    """The mechanism has to keep working, or this is just a lower floor."""
    straddling = probs(hostility=SEVERITY_STRADDLING)
    assert decision_confidence(FINE, straddling) == pytest.approx(0.5)
    assert decision(FINE, straddling) == Lane.PEN


def test_decision_gate_reads_the_low_end_for_the_quality_axes():
    """substance and on_topic are over the line when they are *low*, so their
    boundary is a different one."""
    confident_noise = probs(substance=QUALITY_CLEARLY_LOW, on_topic=QUALITY_CLEARLY_LOW)
    assert decision(NOISE, confident_noise) == Lane.BOUNCED
    assert decision(NOISE, confident_noise | {"substance": QUALITY_STRADDLING}) == Lane.PEN


def test_quality_axes_still_cannot_veto_a_verdict_they_had_no_part_in():
    assert decision(FINE, probs(on_topic=QUALITY_STRADDLING)) == Lane.APPROVED


def test_a_confident_attack_bounces_under_the_decision_gate():
    hostile = FINE | {"hostility": 9.4}
    assert decision(hostile, probs(hostility=dist(l3=0.10, l4=0.90))) == Lane.BOUNCED


def test_decision_gate_without_probabilities_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="probabilities"):
        lane(FINE, CONFIDENT, None, Gate.DECISION)
