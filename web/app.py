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
    Div,
    Header,
    I,
    Script,
    Span,
    Style,
    Title,
    fast_app,
)

import config
from feed.replay import ThreadStore
from judge.pipeline import Pipeline
from web.render import render_loop

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

.lanes { display:grid; grid-template-columns:1fr 1fr 1.3fr; gap:1px;
  background:var(--edge); height:calc(100vh - 3.5rem); }
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

.fingerprint { display:flex; gap:3px; margin-bottom:.35rem; }
.bar { flex:1; height:4px; background:#0a0c11; border-radius:2px; overflow:hidden; }
.bar-fill { height:100%; }
.bar-hostility { background:#f85149; } .bar-contempt { background:#db6d28; }
.bar-substance { background:#3fb950; } .bar-on_topic { background:#58a6ff; }

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
""".replace("__MAX__", str(config.MAX_VISIBLE_CARDS))


def stat(key: str, label: str, cls: str = "") -> Div:
    return Div(B("0", id=f"n-{key}"), I(label), cls=f"stat {cls}".strip())


def lane(lane_id: str, title: str, note: str = "") -> Div:
    heading = H2(title, I(f" · {note}") if note else "")
    return Div(heading, Div(id=lane_id, cls="stack"), cls="lane")


def page(store: ThreadStore) -> tuple:
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
                stat("spend", "spent"),
                # Errors sit beside the Pen and are never folded into it. A
                # failed request pens fifteen comments, and a Pen padded with
                # failures looks identical to one full of hard cases.
                stat("errors", "errors", cls="err"),
                cls="stats",
            ),
        ),
        Div(
            lane("lane-approved", "Approved"),
            lane("lane-bounced", "Bounced", "click to reveal"),
            lane("lane-pen", "The Pen", "a human decides"),
            cls="lanes",
        ),
        # Replaced out-of-band every tick; the odometer reads its dataset.
        Div(id="tick"),
    )


def build():
    store = ThreadStore.load()
    pipeline = Pipeline(store)
    tasks: dict[str, asyncio.Task] = {}

    async def on_startup() -> None:
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

    async def on_connect(ws):
        sockets.add(ws)

    async def on_disconnect(ws):
        sockets.discard(ws)

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
        return page(store)

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
