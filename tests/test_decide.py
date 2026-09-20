"""Human decisions on penned comments. No network, no key.

`Pipeline.decide` only records; the move renders on the next frame. That split
is deliberate — see `web/render.build_frame`.
"""

from feed.replay import Comment, Thread, ThreadStore
from judge.lanes import Lane
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
