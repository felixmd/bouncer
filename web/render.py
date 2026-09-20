"""Frame construction and the fixed-tick render loop.

**One loop, broadcasting to every client.** Not one loop per connection: the
pipeline has a single `out_queue`, so two draining loops would hand each client
a disjoint half of the stream.

The tick is the whole architecture (invariant 1). Workers write verdicts and
stop; this drains on a 12Hz timer and emits one frame. Coupling them would mean
a WebSocket message per classification — 225 a second — and a dead page inside
two minutes.
"""

import asyncio
from collections.abc import Awaitable, Callable

from fasthtml.common import Button, Div, Span, to_xml

import config
from judge.lanes import Lane
from judge.pipeline import Pipeline, Verdict
from judge.rubric import DEFAULT_RUBRIC

LANE_IDS = {Lane.APPROVED: "lane-approved", Lane.BOUNCED: "lane-bounced", Lane.PEN: "lane-pen"}


def fingerprint(verdict: Verdict) -> Div:
    """Four bars. The shape is the point — a viewer reads the silhouette, not
    the numbers, which is why Score beat Noul (PRD §5.2)."""
    bars = []
    for axis in DEFAULT_RUBRIC.keys:
        value = verdict.scores.get(axis, 0.0)
        pct = max(2.0, value / config.SCORE_SCALE_MAX * 100)
        bars.append(
            Div(
                Div(cls=f"bar-fill bar-{axis}", style=f"width:{pct:.0f}%"),
                cls="bar", title=f"{axis} {value:.1f}",
            )
        )
    return Div(*bars, cls="fingerprint")


def card_id(comment_id: str) -> str:
    """A DOM id from a comment id. Colons are legal in HTML ids but break
    `querySelector`, and htmx needs to find this element to replace it."""
    return "c-" + comment_id.replace(":", "-")


def decision_buttons(comment_id: str) -> Div:
    """Allow / Bounce. Anyone can click — PRD §4.2, no accounts, no auth.

    A plain HTTP post rather than the WebSocket: this is user-initiated and
    wants a direct response, and the socket is the one-way firehose.
    """
    return Div(
        Button(
            "Allow",
            cls="allow",
            hx_post=f"/decide?id={comment_id}&lane=approved",
            hx_swap="none",
        ),
        Button(
            "Bounce",
            cls="bounce",
            hx_post=f"/decide?id={comment_id}&lane=bounced",
            hx_swap="none",
        ),
        cls="decide",
    )


def card(verdict: Verdict, decided_by_human: bool = False) -> Div:
    """One comment. Penned cards carry more, because that is where attention
    should go and a bare score tells a human nothing useful."""
    comment = verdict.comment
    body = comment.body if len(comment.body) < 320 else comment.body[:317] + "..."

    header = [Span(comment.author_hash, cls="who")]
    if decided_by_human:
        header.append(Span("you decided", cls="decided"))
    elif verdict.error:
        header.append(Span("could not judge", cls="why"))
    elif verdict.lane is Lane.PEN and verdict.least_sure_axis:
        # Which axis was unsure is the difference between a Pen that reads as a
        # decision and one that reads as a shrug.
        axis = verdict.least_sure_axis
        header.append(
            Span(f"unsure: {axis} {verdict.confidences[axis]:.2f}", cls="why")
        )
    else:
        header.append(Span(f"{verdict.gate_confidence:.2f}", cls="conf"))

    parts = [Div(*header, cls="card-head")]
    if not verdict.error:
        parts.append(fingerprint(verdict))
    parts.append(Div(body, cls="body"))

    classes = f"card {verdict.lane.value}"
    if verdict.lane is Lane.PEN and not decided_by_human:
        parts.append(decision_buttons(comment.id))
    if decided_by_human:
        classes += " was-penned"
    elif verdict.lane is Lane.BOUNCED:
        # Invariant 9. Real moderation tools do this, so it reads as authentic
        # rather than squeamish — and this goes on a screen in an office.
        classes += " blurred"
    # A resolved card gets a *different* id from the one being deleted. The
    # /decide response both deletes the penned card and appends its replacement,
    # and reusing the id made the two OOB swaps race — the appended card
    # sometimes vanished with the delete.
    dom_id = card_id(comment.id) + ("-decided" if decided_by_human else "")
    return Div(
        *parts,
        id=dom_id,
        cls=classes,
        title="click to reveal" if "blurred" in classes else None,
    )


def lane_frame(
    lane: Lane,
    streamed: list[Verdict],
    decided: list[Verdict] | None = None,
) -> Div:
    """New cards for one lane, appended out-of-band.

    Decided cards are kept separate from streamed ones so that `sample` cannot
    reach them. It could, once: a resolved comment was appended to the
    Approved list behind ~19 streamed verdicts and then truncated away by the
    per-frame cap, so Allow silently did nothing while Bounce — whose lane is
    almost always empty — worked fine.
    """
    cards = [card(v) for v in sample(streamed, lane)]
    cards += [card(v, decided_by_human=True) for v in decided or []]
    return Div(*cards, id=LANE_IDS[lane], hx_swap_oob="beforeend")


def counter_frame(pipeline: Pipeline) -> Div:
    """Raw numbers once per tick, as data attributes.

    Spec §8.3: do not stream counter HTML at frame rate. The visible digits are
    eased toward these by a small odometer in the browser, so the numbers move
    smoothly at 60fps while the wire carries twelve tiny updates a second.
    """
    counters, stats = pipeline.counters, pipeline.client.stats
    return Div(
        id="tick",
        hx_swap_oob="true",
        data_judged=str(counters.judged),
        data_approved=str(counters.lanes.get(Lane.APPROVED, 0)),
        data_bounced=str(counters.lanes.get(Lane.BOUNCED, 0)),
        data_pen=str(counters.lanes.get(Lane.PEN, 0)),
        data_rate=f"{counters.per_second:.0f}",
        data_spend=f"{stats.cost_usd:.4f}",
        data_decided=str(counters.decided),
        # Errors are surfaced separately and never folded into the Pen count.
        # Under load a failed request pens fifteen comments, and a Pen padded
        # with failures is indistinguishable from one full of hard cases.
        data_errors=str(counters.errored),
    )


def sample(verdicts: list[Verdict], lane: Lane) -> list[Verdict]:
    """Cap cards per frame, except in the Pen.

    Every penned comment must reach the screen — it is the one lane a human is
    expected to act on. Approved and Bounced are a visual, and the counters
    carry the real totals.
    """
    if lane is Lane.PEN:
        return verdicts
    return verdicts[: config.CARDS_PER_FRAME]


def build_frame(
    pipeline: Pipeline,
    verdicts: list[Verdict],
    decisions: list[tuple[str, Verdict]] | None = None,
) -> str:
    """One tick's worth of DOM, as a single string.

    Human decisions ride in this frame rather than coming back on the `/decide`
    POST response, so that every DOM mutation travels one channel serialised on
    the tick. That is what invariant 1 asks for anyway.
    """
    by_lane: dict[Lane, list[Verdict]] = {lane: [] for lane in Lane}
    for verdict in verdicts:
        by_lane[verdict.lane].append(verdict)

    decided_by_lane: dict[Lane, list[Verdict]] = {lane: [] for lane in Lane}
    parts = []
    for comment_id, moved in decisions or []:
        # Out of the Pen. The replacement card carries a different id, so the
        # delete cannot swallow the thing that replaces it.
        parts.append(Div(id=card_id(comment_id), hx_swap_oob="delete"))
        decided_by_lane[moved.lane].append(moved)

    parts += [
        lane_frame(lane, by_lane[lane], decided_by_lane[lane])
        for lane in Lane
        if by_lane[lane] or decided_by_lane[lane]
    ]
    parts.append(counter_frame(pipeline))
    # `to_xml`, not `str`. `str()` on an FT object yields its children joined —
    # the frame went out as the literal text "lane-approvedlane-pentick" and the
    # ws extension silently found no elements to swap.
    return "".join(to_xml(part) for part in parts)


async def render_loop(pipeline: Pipeline, send: Callable[[str], Awaitable[None]]) -> None:
    """Drain on a fixed tick, emit one frame. Never one frame per verdict."""
    tick = 1.0 / config.RENDER_TICK_HZ
    while True:
        await asyncio.sleep(tick)
        verdicts = pipeline.drain()
        for verdict in verdicts:
            pipeline.counters.record(verdict)
        decisions = pipeline.drain_decisions()
        if not verdicts and not decisions:
            continue
        try:
            await send(build_frame(pipeline, verdicts, decisions))
        except (RuntimeError, ConnectionError):
            # A client vanished mid-broadcast. The stream is not about to stop
            # for one socket.
            continue
