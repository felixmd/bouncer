# The Bouncer — Technical Specification

Companion to `PRD.md`. Read that first for the product logic, especially §5 (the two-axis model).

> **Verify before building.** The Jev API details below come from launch-window documentation and community sources. You have API access — check the real docs at `docs.typesafe.ai` and confirm the SDK surface, rate limits, and response shape before building on any number in this file. Where something is unverified it is marked **[verify]**.

---

## 1. Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Official Jev SDK ships `AsyncTypeSafeClient`; async fan-out is the hard part and it is native |
| Web | FastHTML (Starlette + HTMX) | Server-rendered, WebSockets first-class, one process to deploy |
| Transport | WebSocket | Server-push at a fixed tick |
| Animation | CSS transitions | No React needed; canvas is the escape hatch if motion gets heavy |
| Package manager | `uv` | Fast, lockfile, no venv ceremony |
| Runtime | Local laptop | Demo runs from the presenter's machine; Cloudflare Tunnel for sharing |

Dependencies: `python-fasthtml`, `typesafe-sdk`, `httpx`, `uvicorn`. Dev only: `pytest`, `ruff`.

## 2. Architecture

```
replay reader ──> in_queue ──> batcher ──> N async workers ──> out_queue ──> render loop ──> WS frame
  (JSON on disk)              (groups of B,   (semaphore +        (results)     (fixed 12Hz)   (batched
                               same thread)    token bucket)                                    OOB swap)
```

**The invariant that makes this work: never push one WebSocket message per classification.** Workers run flat out and write to `out_queue`; a separate loop drains that queue on a fixed tick and emits one batched frame. Coupling them means hundreds of DOM swaps per second and a dead page inside two minutes.

### 2.1 Components

| Module | Responsibility |
|---|---|
| `feed/replay.py` | Reads pre-baked JSON, emits comments at a configurable drip rate |
| `feed/reddit_fetch.py` | **Dev-time only.** Fetches a thread and writes JSON. Never called at runtime |
| `judge/batcher.py` | Groups comments from the same thread into request-sized batches |
| `judge/client.py` | Jev calls: token bucket, semaphore, retry, response parsing |
| `judge/rubric.py` | Builds the question set from the current (editable) rubric text |
| `judge/lanes.py` | Pure function: scores + confidence → lane |
| `web/app.py` | FastHTML routes, WebSocket handler |
| `web/render.py` | The 12Hz tick loop and frame construction |
| `calibrate/` | Jigsaw mode: threshold sweep and curve |

## 3. Jev integration

### 3.1 Rate limits and the batching math

Published limits: **1,200 requests/min (20/sec)** and **250,000 tokens/sec**. **[verify]**

One request per comment caps you at 20 items/sec — a slow scroll, not a demo. Batching is mandatory:

```
throughput = 20 req/s × B items per request
B = 15  →  300 items/sec
B = 20  →  400 items/sec
```

Token budget is not the binding constraint. With ~300 tokens of shared thread context and ~140 tokens per comment (text plus its four questions):

```
request  = 300 + (15 × 140)  ≈ 2,400 tokens
per sec  = 20 × 2,400        ≈ 48,000 tokens/sec   (19% of the 250K ceiling)
```

Context limit is ~64K for state plus all questions **[verify]** — a 2,400-token request is nowhere near it. **You are request-limited, not token-limited.** Push B up until accuracy degrades, not until tokens run out.

Concurrency needed to sustain 20 req/s at 200ms latency is only ~4 in flight; at 500ms, ~10. A semaphore of 32 is ample. **Rate-limit explicitly with a token bucket** — do not rely on the semaphore to hold you under 20/s, because when latency drops you will blow through it and start collecting 429s.

### 3.2 The experiment to run before writing any UI

Jev evaluates every question in isolation against the same state. In a batched request the state is 15 comments and the question is effectively "is comment #7 hostile?" — which requires the model to locate #7 first. **This will degrade with batch size and nobody knows where the knee is.**

Measure it on day one:

1. Take ~300 labelled Jigsaw comments.
2. Score each at B = 1, 3, 5, 8, 12, 15, 20, 30.
3. Plot agreement-with-B=1 and mean confidence against B.
4. Pick the largest B before either falls off.

Prior guess is 8–15. If it turns out to be 3, the throughput story changes and it is much better to know that on Saturday morning than Sunday night.

### 3.3 Request shape

State is the thread context once, then the comment block. Questions are keyed per comment.

```python
state = {
    "thread_title": thread.title,
    "thread_body": thread.selftext[:1200],      # empty for link posts
    "comments": [
        {"id": "c00", "replying_to": c.parent_snippet, "text": c.body}
        for c in batch
    ],
}

questions = {}
for c in batch:
    questions[f"{c.id}_hostility"] = Score(
        f"For comment {c.id}: is it attacking a person?",
        rubric.hostility_levels,
    )
    questions[f"{c.id}_contempt"]  = Score(...)
    questions[f"{c.id}_substance"] = Score(...)
    questions[f"{c.id}_on_topic"]  = Score(...)
```

Notes:

- Include **one level** of parent context per comment. "You're an idiot" as a top-level comment and as a reply to a specific claim are different objects, and `on_topic` for a deep reply means relevant to its subthread, not to the article. Two levels is diminishing returns and breaks context amortisation.
- **Batch by thread.** All comments in a request share one `state`, so the thread context cost amortises to near zero across B comments. Batching randomly across threads would force every context into every request.
- **Never summarise the thread.** Reddit's own title and selftext are sufficient, and generating a summary would mean bolting a second model onto a Jev demo.
- Score levels are plain-English rubric strings, editable at runtime (see §6).

### 3.4 Client

```python
from typesafe import AsyncTypeSafeClient, RetryPolicy   # [verify import path]

client = AsyncTypeSafeClient(
    api_key=os.environ["TYPESAFE_API_KEY"],
    retry_policy=RetryPolicy(max_retries=3),
    timeout=30,
)
```

Pin the model version (`jev-1.13.0` style) rather than `jev-latest`. `latest` moves, and it will move under your tuned thresholds. Log the `model` field from the response, not from config.

Handle 429 (rate limit, honour `retry_after`) and 529 (overloaded) with backoff. On persistent failure, mark the comment `errored` and route it to the Pen — degrading into "a human looks at it" is the right failure mode for this product and costs nothing to implement.

## 4. Lane classification

Pure function, no model involvement. This is deliberate — the model returns scores and confidence; the *policy* is yours.

```python
SEVERITY_THRESHOLD = 6.0      # on a 0-10 Score
CONFIDENCE_FLOOR   = 0.80     # tune against Jigsaw, see §7

def lane(scores: dict[str, float], confidences: dict[str, float]) -> Lane:
    if min(confidences.values()) < CONFIDENCE_FLOOR:
        return Lane.PEN
    over = (
        scores["hostility"] >= SEVERITY_THRESHOLD
        or scores["contempt"] >= SEVERITY_THRESHOLD
        or (scores["substance"] <= 3.0 and scores["on_topic"] <= 3.0)
    )
    return Lane.BOUNCED if over else Lane.APPROVED
```

Confidence gate is checked **first**. A low-confidence "clearly fine" still goes to the Pen — that is the entire point of the mechanism, and short-circuiting it to save Pen volume would gut the demo.

Both constants live in config and are exposed in the UI.

## 5. Data pipeline

### 5.1 Fetch script (dev-time only)

`reddit_fetch.py <thread_url> --out data/threads/<slug>.json`

- Use `sort=controversial` on the comments endpoint. Popularity and contentiousness are nearly uncorrelated on Reddit — the top post on r/all is usually a photo with a friendly comment section.
- Filter on the per-comment `controversiality` field where useful.
- Good source subs: r/AmItheAsshole, r/unpopularopinion, r/relationship_advice, r/politics, r/news.
- **Set a descriptive User-Agent.** Reddit blocks `python-requests/2.x` regardless of origin IP.
- **Hash usernames at fetch time.** Never store or display real handles — the app labels comments "toxic," and that should not be attached to a real person. Hash to a stable display name so thread structure stays legible.
- Unauthenticated `.json` endpoints are fine at this volume (a script run a few dozen times, from a laptop). OAuth is over-engineering here.

### 5.2 Stored format

```json
{
  "thread": {
    "id": "1abc23",
    "title": "...",
    "selftext": "...",
    "subreddit": "AmItheAsshole",
    "fetched_at": "2026-09-19T10:00:00Z"
  },
  "comments": [
    {
      "id": "c00",
      "author_hash": "amber-finch",
      "body": "...",
      "parent_snippet": "first 200 chars of parent, or null",
      "depth": 2,
      "score": 41,
      "controversiality": 1
    }
  ]
}
```

### 5.3 Replay

Runtime reads only from `data/threads/*.json`. Reddit is never called while the app is running.

This is what actually solves the datacenter-IP problem — the data ships with the app, so it does not matter where it runs, now or later. It also means identical re-runs (important when demoing twice), a pile you have eyeballed in advance, and full control of pacing.

Drip rate is a UI slider. A real thread arrives as one bulk fetch, so the stream timing is synthetic regardless — own it rather than pretending otherwise.

## 6. Live rubric editing

The rubric is the plain-English Score level descriptions, held in server state and editable from the UI.

On edit: rebuild the question set, re-run the retained backlog (keep the last ~2,000 comments in memory), push a full re-sort. Must complete in under three seconds — at 300 items/sec, 2,000 comments is ~7 seconds, so either cap the re-judged window at ~800 or accept the longer sweep and animate it as a feature.

Cache raw comment text in memory so re-judging never touches disk.

## 7. Calibration mode

Separate route, separate dataset (Jigsaw / Civil Comments, which ships human labels).

1. Score N comments at the chosen batch size.
2. For each candidate `CONFIDENCE_FLOOR` from 0.5 to 0.99, compute: % auto-handled, and agreement-with-human-label on the auto-handled subset.
3. Plot both curves against the threshold.
4. Pick the knee.

Set `CONFIDENCE_FLOOR` from this plot, not by picking 0.8 because it sounds round. The plot is also worth exposing as the second tab — it is the difference between "we have an amber lane" and "here is the curve."

## 8. Frontend

### 8.1 Render loop

Fixed 12Hz tick. Each tick: drain `out_queue`, build one HTML fragment set, emit one WS frame using `hx-swap-oob`.

### 8.2 DOM budget

Hard cap of ~150 visible cards with eviction from the tail. Without this the page dies after a couple of minutes.

### 8.3 Counters

Push raw numbers once per tick and let a small JS odometer interpolate between ticks. Do not stream counter HTML at frame rate.

### 8.4 Motion

CSS `transform` + `transition` only. Accept CSS-grade motion — it is enough for "card slides into a lane." If you later want fluid motion at high card counts, the escape hatch is a single `<canvas>` fed by the same WebSocket, not a frontend rewrite.

### 8.5 Content safety

Bounced lane is **blurred by default, click to reveal**. Real moderation tools do this, so it reads as authentic rather than squeamish — and this is going on a screen in front of colleagues.

## 9. Operations

**Latency is location-dependent.** Jev's 70–500ms is measured from US West Coast. Throughput is a function of round-trip time, so the demo machine's location and network directly scale the headline counter. Measure a fan-out from the actual presenting machine early. If it is disappointing, that is the one real argument for running on a small box in us-west.

**Disable sleep before presenting.**

**If exposing via Cloudflare Tunnel or ngrok:** per-IP rate limit on `/test` and a hard cap on total requests. The API key is on the laptop and an open text box pointed at a paid API is the kind of thing that gets found.

## 10. Build order

1. `reddit_fetch.py` → one good thread on disk
2. Batch-size-vs-accuracy experiment (§3.2) — **before any UI**
3. `judge/` pipeline, verified against a printed console stream
4. FastHTML shell + WebSocket + 12Hz render loop
5. The Pen, with working Allow/Bounce
6. Counters and the spend meter
7. Test-your-own-comment
8. Live rubric editing
9. Calibration tab (cut first if time runs out)

Steps 1–3 are the weekend's real risk. Steps 4–8 are Claude Code's happy path.
