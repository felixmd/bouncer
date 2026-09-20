"""Human decisions on penned comments. No network, no key.

`Pipeline.decide` only records; the move renders on the next frame. That split
is deliberate — see `web/render.build_frame`.
"""

from feed.replay import Comment, Thread, ThreadStore
from judge.lanes import Gate, Lane, Policy
from judge.pipeline import Pipeline, Verdict


def comment(index: int = 0) -> Comment:
    return Comment(
        id=f"t1:c{index:03d}", thread_id="t1", body="a comment body",
        parent_snippet=None, author_hash="amber-finch", depth=0, score=0,
    )


def pipeline() -> Pipeline:
    thread = Thread(id="t1", title="t", selftext="", source="test")
    thread.comments = [comment(0)]
    return Pipeline(ThreadStore([thread]))


def penned(index: int = 0) -> Verdict:
    return Verdict(
        comment=comment(index), lane=Lane.PEN,
        scores=dict.fromkeys(["hostility", "contempt", "substance", "on_topic"], 5.0),
        confidences=dict.fromkeys(["hostility", "contempt", "substance", "on_topic"], 0.4),
        gate_confidence=0.4,
    )


def test_a_decision_is_recorded_and_counted():
    p = pipeline()
    p.backlog.add(penned())
    assert p.decide("t1:c000", Lane.APPROVED) is True
    assert p.counters.decided == 1

    queued = p.drain_decisions()
    assert len(queued) == 1
    comment_id, moved = queued[0]
    assert comment_id == "t1:c000"
    assert moved.lane is Lane.APPROVED


def test_deciding_does_not_mutate_the_original_verdict():
    """The backlog is also what a rubric edit re-judges, so the recorded verdict
    has to stay as the model left it."""
    p = pipeline()
    original = penned()
    p.backlog.add(original)
    p.decide("t1:c000", Lane.BOUNCED)
    assert original.lane is Lane.PEN


def test_draining_decisions_empties_the_queue():
    p = pipeline()
    p.backlog.add(penned())
    p.decide("t1:c000", Lane.APPROVED)
    assert len(p.drain_decisions()) == 1
    assert p.drain_decisions() == []


def test_a_comment_aged_out_of_the_backlog_is_declined_not_crashed():
    """The card can outlive the 2,000-verdict backlog on screen; the DOM cap
    will take it shortly."""
    p = pipeline()
    assert p.decide("t1:c999", Lane.APPROVED) is False
    assert p.counters.decided == 0
    assert p.drain_decisions() == []


def test_the_backlog_finds_the_newest_verdict_for_an_id():
    p = pipeline()
    p.backlog.add(penned())
    newer = penned()
    newer.gate_confidence = 0.77
    p.backlog.add(newer)
    assert p.backlog.get("t1:c000").gate_confidence == 0.77


def test_demand_starts_open_so_the_console_runner_streams():
    assert pipeline().demand.is_set()


# --- retuning: the whole point is that this costs nothing ------------------

def scored(index: int, hostility: float, conf: float) -> Verdict:
    axes = ["hostility", "contempt", "substance", "on_topic"]
    # Probability mass concentrated on the level the score implies, so the
    # decision gate agrees with the mean rule.
    over = hostility >= 6.0
    dist = {3: 0.1, 4: 0.9} if over else {0: 0.9, 1: 0.1}
    return Verdict(
        comment=comment(index),
        lane=Lane.PEN,
        scores={**dict.fromkeys(axes, 8.0), "hostility": hostility, "contempt": 0.0},
        confidences=dict.fromkeys(axes, conf),
        probabilities={a: ({0: 0.9, 1: 0.1} if a in ("hostility", "contempt") else {4: 1.0})
                       for a in axes} | {"hostility": dist},
        gate_confidence=conf,
    )


def test_retuning_the_floor_moves_comments_out_of_the_pen():
    """A threshold change is a pure recompute over stored scores — no model
    calls. That is what makes the slider in the UI free."""
    p = pipeline()
    for i in range(5):
        p.backlog.add(scored(i, hostility=0.0, conf=0.60))

    p.retune(Policy(gate=Gate.DECISIVE, floor=0.95))
    assert all(v.lane is Lane.PEN for v in p.backlog.recent(5))

    p.retune(Policy(gate=Gate.DECISIVE, floor=0.50))
    assert all(v.lane is Lane.APPROVED for v in p.backlog.recent(5))


def test_retuning_severity_moves_comments_between_approved_and_bounced():
    p = pipeline()
    for i in range(4):
        p.backlog.add(scored(i, hostility=7.0, conf=0.99))

    p.retune(Policy(gate=Gate.DECISIVE, floor=0.5, severity=6.0))
    assert all(v.lane is Lane.BOUNCED for v in p.backlog.recent(4))

    p.retune(Policy(gate=Gate.DECISIVE, floor=0.5, severity=9.0))
    assert all(v.lane is Lane.APPROVED for v in p.backlog.recent(4))


def test_retuning_never_calls_the_model():
    """No key is set in tests, so a stray call would raise. Silence is the
    assertion."""
    p = pipeline()
    for i in range(3):
        p.backlog.add(scored(i, hostility=0.0, conf=0.9))
    p.retune(Policy(gate=Gate.DECISIVE, floor=0.5))
    assert p.client.stats.requests == 0


def test_auto_handled_share_tracks_the_policy():
    """PRD §6.1: a live number beside the control, not a hard-coded claim."""
    p = pipeline()
    for i in range(10):
        p.backlog.add(scored(i, hostility=0.0, conf=0.7))

    p.retune(Policy(gate=Gate.DECISIVE, floor=0.99))
    assert p.auto_handled == 0.0
    p.retune(Policy(gate=Gate.DECISIVE, floor=0.5))
    assert p.auto_handled == 1.0


def test_errored_verdicts_are_left_in_the_pen_by_a_retune():
    """They have no scores to recompute from, and "a human looks at it" does
    not stop being true because a threshold moved."""
    p = pipeline()
    p.backlog.add(Verdict(comment=comment(0), lane=Lane.PEN, error="boom"))
    p.retune(Policy(gate=Gate.DECISIVE, floor=0.0))
    assert p.backlog.get("t1:c000").lane is Lane.PEN
