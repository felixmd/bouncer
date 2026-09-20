# The Bouncer — Technical Specification

Companion to `PRD.md`. Read that first for the product logic, especially §5 (the two-axis model).

> **`FINDINGS.md` supersedes parts of this document.** The experiments in §3.2
> and §7 have now been run. Three assumptions here were wrong: the batch-size
> knee does not exist, the request shape in §3.3 is the wrong one, and the
> confidence gate in §4 pens 99% of traffic. Sections below are annotated where
> measurement overtook them. The unannotated parts still stand.

> **Verify before building.** The Jev API details below came from launch-window documentation and community sources. Most are now confirmed against `docs.typesafe.ai` and against live calls; remaining unverified items are marked **[verify]**.

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

### 3.2 The batch-size experiment — **run, see `FINDINGS.md` §1**

Done. 300 labelled Civil Comments at B = 1, 3, 5, 8, 12, 15, 20, 30, via
`experiments/batch_sweep.py`.

**There is no knee.** Agreement with the human label is flat across the whole
range (0.74–0.79) and B=30 scored highest. Every one of the 120 questions in a
B=30 request came back answered. The prior guess of 8–15 was pessimistic.

One thing the original design of this experiment would have missed: measuring
drift against B=1 only tells you something if you know the noise floor. Running
B=1 twice gives a mean score delta of **0.081/10** and lane agreement of
**0.993** — the model is nearly deterministic. Against that, batching moves
scores by 0.54–0.71 and flips **6–7% of lane decisions**. Real, roughly 8×
noise, and lateral rather than degrading. Acceptable for a demo; not acceptable
if a comment has to get the same verdict twice.

**Any future sweep must include the noise floor as a control.**

`BATCH_SIZE = 15`, for headroom under the 64K ceiling rather than for accuracy.

### 3.3 Request shape — **superseded, see `FINDINGS.md` §2**

The shape below — comments in the state, questions addressing them by id — was
measured against the alternative and lost on every metric. It walks into two
documented `jev-1.13` failure modes at once: the model "cannot reliably count
items, with error growing with the size of the thing being counted", and
"accuracy falls as the state grows with content unrelated to the decision". At
B=15, the other fourteen comments *are* that unrelated content.

**Use `quoted` addressing instead.** The comment travels inside its own
question; the state holds only thread context. Nothing has to be located,
nothing irrelevant is present, and the batch size is unchanged — still 15
comments per request, so throughput does not move. It costs ~45% more tokens,
which we have, and buys higher confidence, higher recall and higher agreement.

```python
state = {
    "thread_title": thread.title,
    "thread_body": thread.selftext[:1200],      # empty for link posts
    "replying_to": c.parent_snippet,
}

questions = {}
for c in batch:
    for axis in rubric.axes:
        questions[f"{c.id}_{axis.key}"] = Score(
            instructions=f'The comment is:\n"""\n{c.body}\n"""\n{axis.question}',
            criteria=axis.levels,
        )
```

The batch still shares one `state`, so thread context still amortises across B.

<details><summary>The original index-addressed shape, kept for the record</summary>

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

</details>

Notes:

- Include **one level** of parent context per comment. "You're an idiot" as a top-level comment and as a reply to a specific claim are different objects, and `on_topic` for a deep reply means relevant to its subthread, not to the article. Two levels is diminishing returns and breaks context amortisation.
- **Batch by thread.** All comments in a request share one `state`, so the thread context cost amortises to near zero across B comments. Batching randomly across threads would force every context into every request.
- **Never summarise the thread.** Reddit's own title and selftext are sufficient, and generating a summary would mean bolting a second model onto a Jev demo.
- Score levels are plain-English rubric strings, editable at runtime (see §6).
- **Write levels as situations, not degrees**, and make sure each level names exactly one situation. This is the single highest-leverage thing in the whole pipeline — see `FINDINGS.md` §3, and §5.2 of the PRD.

### 3.4 Client

Import path confirmed: the distribution is `typesafe-sdk`, the module is
`typesafe_sdk` (not `typesafe`). The keyword is `retry`, not `retry_policy`, and
the key is read from `TYPESAFE_API_KEY` by the SDK — never pass `api_key=` in
source.

```python
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

client = AsyncTypeSafeClient(
    model=config.JEV_MODEL,
    retry=RetryPolicy(max_retries=3),
    timeout=30,
)
```

`client.system_one(state, questions)` returns `.model`, `.usage` and
`.scores[name]`, each a `ScoreAnswer` with `.score` (probability-weighted mean
of the levels, continuous) and `.confidence` (0–1, the concentration of the
probability distribution).

Pin the model version rather than `jev-latest` — the SDK's own default *is*
`jev-latest`, so an unset `model` silently inherits the moving target. Currently
`jev-1.13.0`. Log the `model` field from the response, not from config.

Handle 429 (rate limit, honour `retry_after`) and 529 (overloaded) with backoff. On persistent failure, mark the comment `errored` and route it to the Pen — degrading into "a human looks at it" is the right failure mode for this product and costs nothing to implement.

## 4. Lane classification

Pure function, no model involvement. This is deliberate — the model returns scores and confidence; the *policy* is yours.

Confidence gate is checked **first**. A low-confidence "clearly fine" still goes to the Pen — that is the entire point of the mechanism, and short-circuiting it to save Pen volume would gut the demo. That has not changed.

**What changed is which confidences the floor applies to — see `FINDINGS.md` §4.**

`min()` across all four axes pens **99–100% of traffic** at any floor that is
worth having. That is arithmetic, not caution: four axes each averaging 0.6–0.7
confidence, take the worst every time, nothing survives. The demo has no
Approved lane.

The fix is not a lower floor — the Score docs warn specifically that if there is
nowhere for uncertain cases to go you will lower the threshold and rebuild the
thing you were escaping. The fix is to stop letting an axis veto a decision it
took no part in.

```python
SEVERITY_THRESHOLD = 6.0      # on the normalised 0-10 scale
CONFIDENCE_FLOOR   = 0.85     # from the curve in §7, not from taste
CONFIDENCE_GATE    = "decisive"

def decisive_confidence(scores, confidences) -> float:
    # Both severity axes always: an Approved verdict claims neither crossed the
    # line, so both have to be trusted. The quality axes only when the
    # low-quality rule is what fired.
    relevant = [confidences["hostility"], confidences["contempt"]]
    if scores["substance"] <= 3.0 and scores["on_topic"] <= 3.0:
        relevant += [confidences["substance"], confidences["on_topic"]]
    return min(relevant)

def lane(scores, confidences) -> Lane:
    if decisive_confidence(scores, confidences) < CONFIDENCE_FLOOR:
        return Lane.PEN
    over = (
        scores["hostility"] >= SEVERITY_THRESHOLD
        or scores["contempt"] >= SEVERITY_THRESHOLD
        or (scores["substance"] <= 3.0 and scores["on_topic"] <= 3.0)
    )
    return Lane.BOUNCED if over else Lane.APPROVED
```

`min_all` remains available as the conservative gate and both are exposed in the
UI — the difference between them is itself worth showing.

All constants live in config and are exposed in the UI.

### 4.1 The scoring scale

`Score.criteria` is one description per level **starting at zero**, so N levels
give raw scores in `0..N-1`. A "0–10 score" would need 11 levels against a
documented maximum of 10. We use 5 levels and normalise onto 0–10 so the
thresholds above keep the scale this document talks about.

Note also that `jev-1.13`'s score levels are documented as "weak in numerical
calibration" — you cannot read the fractional part as a magnitude. Thresholds
should sit near level boundaries, which 6.0/10 (level 2.4 of 0–4) roughly does.

## 5. Data pipeline

### 5.1 Fetch script (dev-time only)

`reddit_fetch.py <thread_url> --out data/threads/<slug>.json`

- Use `sort=controversial` on the comments endpoint. Popularity and contentiousness are nearly uncorrelated on Reddit — the top post on r/all is usually a photo with a friendly comment section.
- Filter on the per-comment `controversiality` field where useful.
- Good source subs: r/AmItheAsshole, r/unpopularopinion, r/relationship_advice, r/politics, r/news.
- **Set a descriptive User-Agent.** Reddit blocks `python-requests/2.x` regardless of origin IP. Necessary, but no longer sufficient — see below.
- **Hash usernames at fetch time.** Never store or display real handles — the app labels comments "toxic," and that should not be attached to a real person. Hash to a stable display name so thread structure stays legible.
- **Scrub `u/handle` mentions inside comment bodies too.** Hashing the `author` field misses the handle people type at each other in the text, and that string lands on screen under the word "toxic" just the same. Route mentions through the same hash map so one person reads consistently.
- ~~Unauthenticated `.json` endpoints are fine at this volume. OAuth is over-engineering here.~~ — **false as of 2026-09-19.** Reddit serves 403 to `www.reddit.com/....json` from this network regardless of User-Agent (a browser UA gets the same 403), and `old.reddit.com` redirects to `/login/?reason=lor2`. It is an IP-level block.

  App-only OAuth does work — the token endpoint returns 401 rather than 403 for bad credentials, so it is reachable. Create a *script* app at `reddit.com/prefs/apps` and set `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`. `feed/reddit_fetch.py` uses them when present and falls back to the public endpoint when not.

  Worth noting what this validates: PRD §7 lists "Reddit unreachable at showtime" as a risk and pre-baked JSON as the mitigation. The risk arrived, during development rather than on stage, and the mitigation is exactly right.

**Hashing is not anonymisation.** Bodies are stored verbatim, so anyone holding a thread file can find the original by searching the text. What it buys is that the app never puts a real handle next to a toxicity label — which is the risk PRD §7 actually names. Real comment ids are deliberately not stored, since they are a direct lookup back to the author. Keep these files out of public repos.

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

## 7. Calibration mode — **partly run, see `FINDINGS.md` §4–5**

Separate route, separate dataset (Jigsaw / Civil Comments, which ships human labels).
`calibrate/fetch_jigsaw.py` pulls a stratified sample openly from the
HuggingFace datasets-server; `calibrate/sweep.py` produces the curve offline.

1. Score N comments at the chosen batch size.
2. For each candidate `CONFIDENCE_FLOOR` from 0.5 to 0.99, compute: % auto-handled, and agreement-with-human-label on the auto-handled subset.
3. **Sweep the gate as well as the floor** (`min4`, `mean4`, `severity`, `decisive`). The gate turned out to matter far more than the floor.
4. Plot the curves against the threshold.
5. Pick the knee.

Set `CONFIDENCE_FLOOR` from this plot, not by picking 0.8 because it sounds round. The plot is also worth exposing as the second tab — it is the difference between "we have an amber lane" and "here is the curve."

**The good news:** agreement on the auto-handled subset rises monotonically with
the floor and reaches 1.000 at the top. The confidence signal is real, which is
the entire product claim, and the second tab has something to show.

**The caveat that has to be fixed before the floor is final:** Civil Comments has
no thread structure, so `on_topic` — "what is it responding to?" — is being asked
with nothing in the state to respond to. Its mean confidence is 0.50 there
against 0.86 on the Reddit corpus. Since `on_topic` is frequently the binding
axis, the whole curve is depressed by a missing field rather than by the model.
**Re-run this against a fetched Reddit thread before setting the demo's floor.**

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

~~1. `reddit_fetch.py` → one good thread on disk~~ — still to do, now the blocker
~~2. Batch-size-vs-accuracy experiment (§3.2)~~ — **done**, no knee, `B=15`
~~3. `judge/` pipeline~~ — `client.py`, `rubric.py`, `lanes.py` exist and are exercised

Revised, in priority order:

1. **`feed/reddit_fetch.py` → one good thread on disk.** Now the critical path.
   Everything left to tune needs thread context, and the calibration curve is
   currently being measured on a dataset that does not have it.
2. **Re-run `calibrate/sweep.py` against that thread** and set
   `CONFIDENCE_FLOOR` for real. Expect the auto-handled share to improve
   materially over the 22% measured on Jigsaw.
3. `feed/replay.py` + `judge/batcher.py` — the streaming half of the pipeline
4. FastHTML shell + WebSocket + 12Hz render loop
5. The Pen, with working Allow/Bounce
6. Counters and the spend meter
7. Test-your-own-comment
8. Live rubric editing
9. Calibration tab — **promote this**. It is nearly free now that
   `calibrate/sweep.py` produces the curve, and it is the direct answer to the
   accuracy objection in PRD §2.

The risk profile has moved. The model integration is no longer the unknown —
throughput, cost, latency and batching are all measured and comfortable. What is
unproven is whether the Pen contains *interesting* comments on real Reddit data,
which is a content question, not an engineering one, and step 1 is what answers it.
