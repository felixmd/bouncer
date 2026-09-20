"""The Bouncer — FastHTML shell, WebSocket, and the wall.

    uv run --env-file .env python -m web.app        # localhost:5001

Server-rendered, HTMX over one WebSocket, CSS transitions for motion. No React
and no build step — spec §1. The escape hatch, if motion ever needs more than
CSS at high card counts, is a single `<canvas>` fed by this same socket rather
than a frontend rewrite.
"""

import asyncio
import os

import uvicorn
from fasthtml.common import (
    H1,
    H2,
    B,
    Button,
    Div,
    Form,
    Header,
    I,
    Input,
    Label,
    Option,
    Script,
    Select,
    Span,
    Style,
    Textarea,
    Title,
    fast_app,
)

import config
from feed.replay import ThreadStore
from judge.lanes import Gate, Lane, Policy
from judge.pipeline import Pipeline
from web.limits import RateLimiter
from web.render import render_loop, resort_frame, test_result

CSS = """
:root {
  --bg:#0f1115; --panel:#161922; --edge:#232838; --ink:#e7e9ee; --dim:#8b93a7;
  --approved:#3fb950; --bounced:#f85149; --pen:#d29922;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45
  ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; overflow:hidden; }
header { display:flex; align-items:baseline; gap:1.5rem; padding:.7rem 1.2rem;
  border-bottom:1px solid var(--edge); }
h1 { font-size:1.05rem; margin:0; letter-spacing:.02em; font-weight:600; }
h1 span { color:var(--dim); font-weight:400; }
.stats { display:flex; gap:1.7rem; margin-left:auto; }
.stat { text-align:right; }
.stat b { display:block; font-size:1.3rem; font-variant-numeric:tabular-nums;
  line-height:1.1; }
.stat i { font-style:normal; color:var(--dim); font-size:.66rem;
  text-transform:uppercase; letter-spacing:.09em; }
.stat.pen b { color:var(--pen); }
.stat.err b { color:var(--bounced); }
.stat.decided-stat b { color:#7fd18a; }

.controls { display:flex; align-items:center; gap:1.4rem; padding:.4rem 1.2rem;
  border-bottom:1px solid var(--edge); background:#12151c; }
.ctl { display:flex; align-items:center; gap:.45rem; }
.ctl label { color:var(--dim); font-size:.66rem; text-transform:uppercase;
  letter-spacing:.09em; }
.ctl input[type=range] { width:7rem; accent-color:var(--pen); }
.ctl select { font:inherit; font-size:.72rem; background:#20242e; color:var(--ink);
  border:1px solid var(--edge); border-radius:4px; padding:.15rem .3rem; }
.ctl .val { font-size:.72rem; font-variant-numeric:tabular-nums; color:var(--ink);
  min-width:2.6rem; }
.ctl.auto { margin-left:auto; gap:.5rem; }
.auto-label { color:var(--dim); font-size:.66rem; text-transform:uppercase;
  letter-spacing:.09em; }
.auto-value { font-size:1.05rem; font-weight:600; color:var(--approved);
  font-variant-numeric:tabular-nums; }
#tuner { margin:0; }

.lanes { display:grid; grid-template-columns:1fr 1fr 1.3fr; gap:1px;
  background:var(--edge); height:calc(100vh - 5.6rem); }
/* min-width:0 matters: grid items default to min-width:auto, so one long
   unbroken comment stretches its column and squeezes the others. */
.lane { background:var(--bg); display:flex; flex-direction:column;
  min-height:0; min-width:0; }
.lane h2 { margin:0; padding:.55rem .9rem; font-size:.7rem; font-weight:600;
  text-transform:uppercase; letter-spacing:.1em; color:var(--dim);
  border-bottom:1px solid var(--edge); white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.lane h2 em { font-style:normal; color:#5b6274; letter-spacing:.04em;
  text-transform:none; }
.stack { overflow:hidden; padding:.6rem; display:flex;
  flex-direction:column-reverse; gap:.5rem; flex:1; }

.card { background:var(--panel); border:1px solid var(--edge);
  border-left:3px solid var(--edge); border-radius:5px; padding:.5rem .65rem;
  animation:in .28s ease-out; }
@keyframes in { from { opacity:0; transform:translateY(-8px); } }
.card.approved { border-left-color:var(--approved); }
.card.bounced  { border-left-color:var(--bounced); }
.card.pen { border-left-color:var(--pen); background:#1e1a11;
  border-color:#413418; box-shadow:0 0 0 1px rgba(210,153,34,.18); }
.card-head { display:flex; gap:.5rem; align-items:baseline; margin-bottom:.32rem; }
.who { color:var(--dim); font-size:.71rem; }
.conf { margin-left:auto; color:var(--dim); font-size:.67rem;
  font-variant-numeric:tabular-nums; }
.why { margin-left:auto; color:var(--pen); font-size:.67rem; }
.body { font-size:.8rem; overflow-wrap:anywhere; white-space:pre-wrap; }
.card.pen .body { font-size:.87rem; }

/* The Pen is the only lane anyone is expected to act on, so it is the only one
   with controls. */
.decide { display:flex; gap:.4rem; margin-top:.5rem; }
.decide button { flex:1; cursor:pointer; font:inherit; font-size:.72rem;
  font-weight:600; letter-spacing:.04em; padding:.3rem 0; border-radius:4px;
  background:#20242e; color:var(--ink); border:1px solid var(--edge);
  transition:background .12s, border-color .12s; }
.decide button:hover { background:#2a3140; }
.decide .allow:hover  { border-color:var(--approved); color:var(--approved); }
.decide .bounce:hover { border-color:var(--bounced); color:var(--bounced); }
.decided { margin-left:auto; color:#7fd18a; font-size:.67rem; font-weight:600; }
/* A card a human resolved keeps a hint of amber, so the Pen's work stays
   visible after it leaves the Pen. */
.card.was-penned { border-right:2px solid var(--pen); }

.fingerprint { display:flex; gap:3px; margin-bottom:.35rem; }
.bar { flex:1; height:4px; background:#0a0c11; border-radius:2px; overflow:hidden; }
.bar-fill { height:100%; }
.bar-hostility { background:#f85149; } .bar-contempt { background:#db6d28; }
.bar-substance { background:#3fb950; } .bar-on_topic { background:#58a6ff; }

/* Test your own comment: PRD 4.2's shareable artifact, at the foot of the Pen
   column because that is where a viewer is already looking. */
.tester { border-top:1px solid var(--edge); padding:.6rem .7rem; background:#12151c;
  display:flex; flex-direction:column; gap:.45rem; flex:0 0 auto; }
.test-fields { display:flex; flex-direction:column; gap:.35rem; }
.tester textarea, .tester input[type=text] { font:inherit; font-size:.8rem;
  background:#0d1016; color:var(--ink); border:1px solid var(--edge);
  border-radius:4px; padding:.4rem .5rem; resize:none; width:100%; }
.tester input[type=text] { font-size:.72rem; }
.tester textarea::placeholder, .tester input::placeholder { color:#5b6274; }
.test-actions { display:flex; align-items:center; gap:.6rem; }
.tester .judge { cursor:pointer; font:inherit; font-size:.74rem; font-weight:600;
  letter-spacing:.04em; padding:.32rem .8rem; border-radius:4px;
  background:var(--pen); color:#1a1408; border:0; }
.tester .judge:hover { filter:brightness(1.1); }
.test-note { color:var(--dim); font-size:.68rem; opacity:0; transition:opacity .15s; }
.test-note.htmx-request { opacity:1; }
.test-note.htmx-request::after { content:"judging…"; }
.test-result:empty { display:none; }
.test-result { border:1px solid var(--edge); border-radius:5px; padding:.5rem .6rem;
  background:var(--panel); display:flex; flex-direction:column; gap:.4rem; }
.verdict-lane { font-size:.8rem; font-weight:700; text-transform:uppercase;
  letter-spacing:.1em; }
.verdict-lane.approved { color:var(--approved); }
.verdict-lane.bounced  { color:var(--bounced); }
.verdict-lane.pen      { color:var(--pen); }
.axis-chips { display:flex; flex-wrap:wrap; gap:.3rem; }
.axis-chip { font-size:.66rem; color:var(--dim); background:#0d1016;
  border:1px solid var(--edge); border-radius:3px; padding:.05rem .3rem;
  font-variant-numeric:tabular-nums; }
.verdict-why { font-size:.72rem; color:var(--dim); }

/* Invariant 9: blurred by default, click to reveal. Real moderation tools do
   this, so it reads as authentic rather than squeamish. */
.card.blurred .body { filter:blur(5px); cursor:pointer; user-select:none;
  transition:filter .18s; }
.card.blurred.shown .body { filter:none; }
"""

JS = """
const MAX = __MAX__;

// Listeners go on `document`, not `document.body`: this script is in the head
// and runs before the body exists. Events bubble to document either way.

// Invariant 7. Without eviction the page dies after a couple of minutes.
// column-reverse puts new cards at the visual top, so the tail to drop is the
// *first* child in document order.
function trim() {
  for (const stack of document.querySelectorAll('.stack')) {
    while (stack.childElementCount > MAX) stack.removeChild(stack.firstElementChild);
  }
}
document.addEventListener('htmx:oobAfterSwap', trim);

// Spec 8.3: the wire carries raw numbers twelve times a second and the digits
// are eased toward them at frame rate, so they read as an odometer rather than
// a stutter. Counter HTML is never streamed at frame rate.
const shown = {};
function odometer() {
  const tick = document.getElementById('tick');
  if (tick) for (const key in tick.dataset) {
    // 'auto' is a percentage rendered below, not a count to ease.
    if (key === 'auto') continue;
    const el = document.getElementById('n-' + key);
    if (!el) continue;
    const target = parseFloat(tick.dataset[key]);
    if (isNaN(target)) continue;
    if (key === 'spend') { el.textContent = '$' + target.toFixed(4); continue; }
    const now = shown[key] ?? 0;
    shown[key] = Math.abs(target - now) < 0.5 ? target : now + (target - now) * 0.16;
    el.textContent = Math.round(shown[key]).toLocaleString();
  }
  requestAnimationFrame(odometer);
}
requestAnimationFrame(odometer);

document.addEventListener('click', e => {
  const card = e.target.closest('.card.blurred');
  if (card) card.classList.toggle('shown');
});

// Slider readouts update on drag; the POST only fires on change (release), so
// dragging does not spam the server with re-sorts.
document.addEventListener('input', e => {
  const el = e.target;
  if (el.type !== 'range') return;
  const out = document.getElementById('v-' + el.id);
  if (!out) return;
  out.textContent = el.id === 'drip' ? Math.round(el.value) + '/s'
    : parseFloat(el.value).toFixed(el.id === 'floor' ? 2 : 1);
});

// The auto-handled share is a percentage, not a count, so it sits outside the
// odometer loop's integer easing.
setInterval(() => {
  const tick = document.getElementById('tick');
  const el = document.getElementById('n-auto');
  if (tick && el && tick.dataset.auto) el.textContent = tick.dataset.auto + '%';
}, 250);
""".replace("__MAX__", str(config.MAX_VISIBLE_CARDS))


def stat(key: str, label: str, cls: str = "") -> Div:
    return Div(B("0", id=f"n-{key}"), I(label), cls=f"stat {cls}".strip())


def lane(lane_id: str, title: str, note: str = "", foot=None) -> Div:
    heading = H2(title, I(f" · {note}") if note else "")
    parts = [heading, Div(id=lane_id, cls="stack")]
    if foot is not None:
        parts.append(foot)
    return Div(*parts, cls="lane")


def tester() -> Form:
    """Type a comment, get the same fingerprint card.

    PRD §4.2's shareable artifact. It lives at the foot of the Pen column
    because that is where a viewer is already looking, and because what they
    want to know after reading the Pen is "what would it say about mine".

    The context field is not decoration. `on_topic` is scored against whatever
    the comment is replying to, and a typed comment has no parent — scoring it
    against nothing is a documented cause of low confidence (FINDINGS §9), so
    leaving this blank would pen most test comments for a reason that has
    nothing to do with what was typed.
    """
    return Form(
        Div(
            Textarea(
                placeholder="Try your own comment…",
                name="comment", id="test-comment", rows="2",
            ),
            Input(
                type="text", name="context", id="test-context",
                placeholder="replying to… (optional — gives on_topic something to judge against)",
            ),
            cls="test-fields",
        ),
        Div(
            Button("Judge it", type="submit", cls="judge"),
            Span(id="test-note", cls="test-note"),
            cls="test-actions",
        ),
        Div(id="test-result", cls="test-result"),
        hx_post="/test",
        hx_target="#test-result",
        hx_swap="outerHTML",
        hx_indicator="#test-note",
        id="tester",
        cls="tester",
    )


def controls(pipeline: Pipeline) -> Div:
    """The tunables, surfaced — a convention, and here also the cost dial.

    Moving any of the three policy controls re-sorts the retained backlog with
    **no model calls**: the scores and per-level probabilities are already
    stored, so a threshold change is a pure recompute. That is what makes this
    strip worth having rather than a settings page — a viewer drags the floor
    and the whole wall re-sorts for nothing.
    """
    policy = pipeline.policy
    return Div(
        Div(
            Label("drip", For="drip"),
            Input(
                type="range", id="drip", name="rate", min="5",
                max=str(config.DRIP_RATE_MAX), step="5",
                value=str(int(pipeline.replay.rate)),
                hx_post="/tune", hx_trigger="change", hx_swap="none",
                hx_include="closest form",
            ),
            Span(f"{int(pipeline.replay.rate)}/s", id="v-drip", cls="val"),
            cls="ctl",
        ),
        Div(
            Label("confidence floor", For="floor"),
            Input(
                type="range", id="floor", name="floor", min="0.5", max="0.99",
                step="0.01", value=f"{policy.floor:.2f}",
                hx_post="/tune", hx_trigger="change", hx_swap="none",
                hx_include="closest form",
            ),
            Span(f"{policy.floor:.2f}", id="v-floor", cls="val"),
            cls="ctl",
        ),
        Div(
            Label("severity", For="severity"),
            Input(
                type="range", id="severity", name="severity", min="2", max="10",
                step="0.5", value=f"{policy.severity:.1f}",
                hx_post="/tune", hx_trigger="change", hx_swap="none",
                hx_include="closest form",
            ),
            Span(f"{policy.severity:.1f}", id="v-severity", cls="val"),
            cls="ctl",
        ),
        Div(
            Label("gate", For="gate"),
            Select(
                *(
                    Option(g.value, value=g.value, selected=(g is policy.gate))
                    for g in Gate
                ),
                id="gate", name="gate",
                hx_post="/tune", hx_trigger="change", hx_swap="none",
                hx_include="closest form",
            ),
            cls="ctl",
        ),
        # PRD §6.1: the auto-handled share as a live number beside the control,
        # rather than a hard-coded "we handle 94%" claim the room cannot check.
        Div(
            Span("auto-handled", cls="auto-label"),
            Span("—", id="n-auto", cls="auto-value"),
            cls="ctl auto",
        ),
        id="controls",
        cls="controls",
    )


def page(store: ThreadStore, pipeline: Pipeline) -> tuple:
    """Returned as a tuple, not a full `Html`.

    Returning `Html(...)` would bypass FastHTML's page assembly and the `hdrs`
    — stylesheet, script, htmx, the ws extension — would never reach the head.
    The body's `hx-ext`/`ws-connect` attributes come from `bodykw` instead.
    """
    return (
        Title("The Bouncer"),
        Header(
            H1("The Bouncer ", Span(f"· {store.total_comments:,} comments on the wire")),
            Div(
                stat("rate", "per sec"),
                stat("judged", "judged"),
                stat("pen", "in the pen", cls="pen"),
                stat("decided", "you decided", cls="decided-stat"),
                stat("spend", "spent"),
                # Errors sit beside the Pen and are never folded into it. A
                # failed request pens fifteen comments, and a Pen padded with
                # failures looks identical to one full of hard cases.
                stat("errors", "errors", cls="err"),
                cls="stats",
            ),
        ),
        Form(controls(pipeline), id="tuner"),
        Div(
            lane("lane-approved", "Approved"),
            lane("lane-bounced", "Bounced", "click to reveal"),
            lane("lane-pen", "The Pen", "a human decides", foot=tester()),
            cls="lanes",
        ),
        # Replaced out-of-band every tick; the odometer reads its dataset.
        Div(id="tick"),
    )


def build():
    store = ThreadStore.load()
    pipeline = Pipeline(store)
    limiter = RateLimiter()
    tasks: dict[str, asyncio.Task] = {}

    async def on_startup() -> None:
        # Held until a browser connects, so an idle server costs nothing.
        pipeline.demand.clear()
        await pipeline.start()
        tasks["render"] = asyncio.create_task(render_loop(pipeline, broadcast))
        print(f"  {store.describe()}")
        print(f"  drip {pipeline.replay.rate}/s  B={config.BATCH_SIZE}  "
              f"gate={config.CONFIDENCE_GATE} floor={config.CONFIDENCE_FLOOR}")
        print(f"  http://127.0.0.1:{os.environ.get('PORT', 5001)}")

    async def on_shutdown() -> None:
        if task := tasks.get("render"):
            task.cancel()
        await pipeline.stop()

    app, rt = fast_app(
        exts="ws",
        pico=False,
        default_hdrs=True,
        hdrs=(Style(CSS), Script(JS)),
        # One socket for the whole page; every OOB target lives under the body.
        bodykw={"hx_ext": "ws", "ws_connect": "/ws"},
        on_startup=on_startup,
        on_shutdown=on_shutdown,
    )
    # Our own socket registry rather than FastHTML's `setup_ws`, which is
    # broken in 0.14.13: its connect handler does `conns[scope.client]`, but
    # `scope` resolves to the raw ASGI dict, which has no `.client`. Every
    # connection raised AttributeError and no frame ever reached a browser.
    #
    # One registry for all clients is the point — the pipeline has a single
    # `out_queue`, so two draining loops would hand each client half the stream.
    sockets: set = set()

    def set_demand() -> None:
        """Judge only while someone is watching.

        Not an optimisation — a cost control. Eight dev servers orphaned by
        restarts kept judging at ~225 items/sec with nobody watching and drained
        the account's credits. See FINDINGS §19.
        """
        if sockets:
            pipeline.demand.set()
        else:
            pipeline.demand.clear()

    async def on_connect(ws):
        sockets.add(ws)
        set_demand()

    async def on_disconnect(ws):
        sockets.discard(ws)
        set_demand()

    @app.ws("/ws", conn=on_connect, disconn=on_disconnect)
    async def socket():
        """Inbound messages are unused; the wall is one-way for now."""

    async def broadcast(html: str) -> None:
        for ws in list(sockets):
            try:
                await ws.send_text(html)
            except Exception:
                # A client vanished mid-frame. Drop it and keep the stream up.
                sockets.discard(ws)

    @rt("/")
    def home():
        return page(store, pipeline)

    @rt("/test")
    async def test(request, comment: str = "", context: str = ""):
        """Judge a comment someone typed. One request, the same gate.

        The only route a stranger can reach, so it is the only one with a rate
        limit — spec §9.
        """
        if not comment.strip():
            return Div(id="test-result", cls="test-result")

        client = request.client.host if request.client else "unknown"
        if refusal := limiter.check(client):
            return Div(
                Div("held", cls="verdict-lane pen"),
                Div(refusal, cls="verdict-why"),
                id="test-result", cls="test-result",
            )

        verdict = await pipeline.judge_one(comment[:1500], context[:400])
        return test_result(verdict)

    @rt("/tune")
    def tune(rate: float, floor: float, severity: float, gate: str):
        """Move a knob. **No model calls.**

        The drip rate takes effect on the next comment. The three policy values
        re-sort the retained backlog immediately, because every verdict already
        carries its scores and per-level probabilities — a new floor is a pure
        recompute. Editing *rubric wording* is the expensive one and lives
        elsewhere; thresholds are free.
        """
        pipeline.replay.rate = max(1.0, min(rate, config.DRIP_RATE_MAX))
        resorted = pipeline.retune(
            Policy(gate=Gate(gate), floor=floor, severity=severity)
        )
        # Re-render the lanes from scratch: a re-sort moves cards *between*
        # lanes, so appending is not enough.
        return resort_frame(pipeline, resorted)

    @rt("/decide")
    def decide(id: str, lane: str):
        """A human resolves one penned comment. Anyone can click — PRD §4.2.

        Returns nothing. The move goes out on the next 12Hz frame, so every DOM
        mutation travels one channel and nothing races. Answering here instead
        cost an afternoon — see `FINDINGS.md` §20.

        A comment that has aged out of the 2,000-verdict backlog returns `False`
        and simply does not move; the card will be evicted by the DOM cap
        shortly anyway.
        """
        target = Lane.APPROVED if lane == "approved" else Lane.BOUNCED
        pipeline.decide(id, target)
        return ""

    @rt("/health")
    def health():
        """Pipeline state without a browser. Useful when the wall is blank and
        the question is whether nothing is being judged or nothing is being
        sent."""
        counters, stats = pipeline.counters, pipeline.client.stats
        return {
            "judged": counters.judged,
            "errored": counters.errored,
            "lanes": {k.value: v for k, v in counters.lanes.items()},
            "per_second": round(counters.per_second, 1),
            "in_queue": pipeline.in_queue.qsize(),
            "out_queue": pipeline.out_queue.qsize(),
            "requests": stats.requests,
            "api_errors": stats.errors,
            "sockets": len(sockets),
            "render_task": "running" if tasks.get("render") else "not started",
            "last_error": next(
                (v.error for v in reversed(pipeline.backlog.recent(200)) if v.error), None
            ),
        }

    return app


if __name__ == "__main__":
    # Built here rather than at import time. `build()` constructs a
    # `JudgeClient`, which needs TYPESAFE_API_KEY, and tests that import this
    # module for its markup must run without one.
    #
    # 5001 is the documented demo port; PORT overrides it so a stray process
    # holding 5001 does not block a run.
    uvicorn.run(
        build(),
        host="127.0.0.1",
        port=int(os.environ.get("PORT", 5001)),
        log_level="warning",
    )
