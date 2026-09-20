"""Frame construction. No network, no key — this is all pure string building.

The render layer had three bugs that only showed as a blank wall in a browser,
so it is worth pinning the contract down here: the frame must be real HTML, it
must carry `hx-swap-oob` on every top-level element, and the ids must match the
lanes the page actually renders.
"""

from fasthtml.common import to_xml

import config
from feed.replay import Comment
from judge.lanes import Lane
from judge.pipeline import Counters, Pipeline, Verdict
from web.render import LANE_IDS, build_frame, card, sample


class FakeStats:
    requests = 3
    errors = 0
    input_tokens = 1000
    cost_usd = 0.0123


class FakeClient:
    stats = FakeStats()


class FakePipeline:
    """Only what `build_frame` reads."""

    counters = Counters()
    client = FakeClient()


def comment(index: int = 0, body: str = "a comment body") -> Comment:
    return Comment(
        id=f"t1:c{index:03d}", thread_id="t1", body=body, parent_snippet=None,
        author_hash="amber-finch", depth=0, score=1,
    )


def verdict(lane: Lane, index: int = 0, **kw) -> Verdict:
    return Verdict(
        comment=comment(index),
        lane=lane,
        scores=dict.fromkeys(["hostility", "contempt", "substance", "on_topic"], 5.0),
        confidences=dict.fromkeys(["hostility", "contempt", "substance", "on_topic"], 0.9),
        gate_confidence=0.9,
        **kw,
    )


def test_frame_is_html_not_a_stringified_object():
    """`str()` on an FT object yields its children joined, so the frame once went
    out as the literal text "lane-approvedlane-pentick" and htmx silently found
    nothing to swap. It must be rendered with `to_xml`."""
    html = build_frame(FakePipeline(), [verdict(Lane.APPROVED)])
    assert html.startswith("<div")
    assert 'id="lane-approved"' in html
    assert "a comment body" in html


def test_every_top_level_element_carries_an_oob_attribute():
    """The htmx ws extension only swaps top-level children that declare it;
    anything else is silently dropped."""
    html = build_frame(
        FakePipeline(), [verdict(Lane.APPROVED), verdict(Lane.PEN, 1)]
    )
    assert html.count("hx-swap-oob") == 3  # two lanes plus the counter div


def test_lane_ids_match_the_page():
    from web.app import lane as lane_column

    for lane_id in LANE_IDS.values():
        assert f'id="{lane_id}"' in to_xml(lane_column(lane_id, "x"))


def test_bounced_cards_are_blurred():
    """Invariant 9."""
    assert "blurred" in to_xml(card(verdict(Lane.BOUNCED)))
    assert "blurred" not in to_xml(card(verdict(Lane.APPROVED)))


def test_a_penned_card_says_which_axis_was_unsure():
    """Otherwise the Pen reads as a shrug rather than a decision."""
    unsure = verdict(Lane.PEN)
    unsure.confidences["contempt"] = 0.2
    assert "contempt" in to_xml(card(unsure))


def test_an_errored_card_says_so_and_carries_no_fingerprint():
    failed = Verdict(comment=comment(), lane=Lane.PEN, error="429 rate limited")
    rendered = to_xml(card(failed))
    assert "could not judge" in rendered
    assert "fingerprint" not in rendered


def test_counters_carry_errors_separately_from_the_pen():
    """A Pen padded with failures is indistinguishable from one full of hard
    cases, so the two numbers never merge."""
    pipeline = FakePipeline()
    pipeline.counters = Counters()
    pipeline.counters.record(Verdict(comment=comment(), lane=Lane.PEN, error="boom"))
    html = build_frame(pipeline, [])
    assert 'data-errors="1"' in html
    assert 'data-pen="1"' in html


def test_approved_is_sampled_per_frame_but_the_pen_never_is():
    """Sampling Approved is a rendering choice. Sampling the Pen would mean a
    comment nobody can click, and the Pen is the product."""
    many = [verdict(Lane.APPROVED, i) for i in range(config.CARDS_PER_FRAME + 20)]
    penned = [verdict(Lane.PEN, i) for i in range(config.CARDS_PER_FRAME + 20)]
    assert len(sample(many, Lane.APPROVED)) == config.CARDS_PER_FRAME
    assert len(sample(penned, Lane.PEN)) == len(penned)


def test_an_empty_lane_is_omitted_rather_than_sent_empty():
    html = build_frame(FakePipeline(), [verdict(Lane.PEN)])
    assert 'id="lane-pen"' in html
    assert 'id="lane-approved"' not in html


def test_a_decision_leaves_the_pen_and_lands_in_the_chosen_lane():
    moved = verdict(Lane.APPROVED)
    html = build_frame(FakePipeline(), [], [(moved.comment.id, moved)])
    assert 'hx-swap-oob="delete"' in html
    assert 'id="c-t1-c000"' in html  # the penned card, removed
    assert 'id="c-t1-c000-decided"' in html  # its replacement, different id
    assert 'id="lane-approved"' in html
    assert "you decided" in html


def test_a_decided_card_has_no_buttons_to_click_twice():
    moved = verdict(Lane.PEN)
    html = build_frame(FakePipeline(), [], [(moved.comment.id, moved)])
    assert html.count("/decide?") == 0


def test_a_decision_survives_the_per_frame_sample():
    """The bug that made Allow silently do nothing: the decided card was
    appended behind ~19 streamed verdicts and truncated away by the cap, while
    Bounce worked because its lane is almost always empty."""
    streamed = [verdict(Lane.APPROVED, i) for i in range(config.CARDS_PER_FRAME + 20)]
    moved = verdict(Lane.APPROVED, 999)
    html = build_frame(FakePipeline(), streamed, [(moved.comment.id, moved)])
    assert 'id="c-t1-c999-decided"' in html
    assert "you decided" in html


def test_pen_cards_carry_buttons_and_other_lanes_do_not():
    penned = build_frame(FakePipeline(), [verdict(Lane.PEN)])
    approved = build_frame(FakePipeline(), [verdict(Lane.APPROVED)])
    assert "/decide?" in penned
    assert "/decide?" not in approved


def test_card_ids_are_selector_safe():
    """Comment ids contain a colon, which is legal in an HTML id but breaks
    `querySelector` — and htmx has to find this element to delete it."""
    from web.render import card_id

    assert ":" not in card_id("hn41002195:c2268")


def test_drain_is_what_the_tick_calls_not_the_worker(monkeypatch):
    """Invariant 1 in structural form: `Pipeline.drain` empties the queue in one
    go, so a frame is per tick rather than per classification."""
    assert hasattr(Pipeline, "drain")
    assert "get_nowait" in Pipeline.drain.__code__.co_names
