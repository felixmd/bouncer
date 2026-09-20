# Tasks

Working tracker. `PRD.md` is what we're building, `TECHNICAL_SPEC.md` is how,
`FINDINGS.md` is what measurement already changed, `CLAUDE.md` is the rule set.
This file is the order and the state.

Status: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped, with a reason

**Rule for this file:** a task is done when its *done-when* line is true, not
when the code exists. Several of these are measurements, and a measurement is
not done until someone has read the number.

---

## Where things stand

**The demo runs end to end.** 7,456 pre-baked comments on disk streaming through
the judge at **225 items/sec sustained** — 82% Approved, 4% Bounced, 14% Pen,
$0.036 per 1,000 comments, p50 285 ms — onto a live three-lane wall over one
WebSocket at a fixed 12Hz tick.

**Blocked: the TypeSafe account is out of credits** (`402`). The app degrades
correctly — every comment routes to the Pen labelled "could not judge" and the
page stays up — but a wall of "could not judge" is not a demo. See Phase 4b.

This is a capability measurement, so the overturned assumptions are part of the
output. `FINDINGS.md` now records that the batch-size knee does not exist (§1),
that the rubric mattered more than the model (§11), that the gate was measuring
the wrong quantity (§13), and that the throughput story in the spec was wrong in
three separate places (§16).

Throughput, cost, latency and batch size were never the risk — see `FINDINGS.md`
§1 and §6. Pen volume was, and three measured changes fixed it: per-axis rubric
levels (§11), the `decisive` gate (§4), and gating on probability mass one side
of the line rather than on scalar confidence (§13).

Two corpora on disk, both with three visible lanes:

| | Approved | Bounced | Pen |
|---|---|---|---|
| Hacker News | 77% | 3% | 19% |
| ChangeMyView | 70% | 4% | 27% |

**Bounced sits at 3–4% and further pipeline work will not move it.** `hostility`
across 200 ChangeMyView comments maxes at 7.4 — nothing in a corpus selected for
derailment reaches the rubric's top rung. That is what real forums look like,
and the borderline slice going to the Pen is the product working as designed.
`FINDINGS.md` §15 sets out the three ways to go; the recommendation is to accept
it. **Needs a human decision before the UI is built**, because it determines
what the Bounced lane is for.

---

## Phase 0 — done

- [x] **0.1** Environment: `uv`, Python 3.12 pinned, deps, private remote
- [x] **0.2** `config.py` as the single home for tunables
- [x] **0.3** `judge/client.py` — token bucket, semaphore, failure routes to Pen
- [x] **0.4** `judge/rubric.py` — four axes, editable levels, `quoted` addressing
- [x] **0.5** `judge/lanes.py` — pure function, `decisive` gate, 12 keyless tests
- [x] **0.6** `calibrate/fetch_jigsaw.py` — stratified Civil Comments sample
- [x] **0.7** `calibrate/sweep.py` — floor × gate curve, offline
- [x] **0.8** Batch-size experiment — no knee, `BATCH_SIZE = 15`
- [x] **0.9** Addressing experiment — `quoted` wins on the context-dependent axes
- [x] **0.10** Gate experiment — `min4` pens 99%, `decisive` adopted
- [x] **0.11** Pin `jev-1.13.0`; confirm SDK surface against the docs

---

## Phase 1 — Reddit data (blocks everything downstream)

Nothing after this phase can be tuned honestly without it. `on_topic` and
`substance` are being measured on a dataset that has no thread context, which is
a documented cause of low confidence and is currently dragging the whole
calibration curve down.

- [~] **1.1 `feed/reddit_fetch.py`** — written and unit-tested; **blocked on Reddit credentials**
  - [x] `sort=controversial`; keeps the per-comment `controversiality` field
  - [x] descriptive User-Agent — necessary, and no longer sufficient
  - [x] **usernames hashed at fetch time**, salted per thread, collision-free — invariant 8
  - [x] **`u/handle` mentions inside bodies scrubbed through the same map.** Found by a test: hashing the `author` field alone still leaks handles that people type at each other in the text
  - [x] one level of parent context as `parent_snippet`; a removed parent passes the grandparent rather than inventing context — spec §3.3
  - [x] stored format per spec §5.2; real comment ids deliberately not stored
  - [x] 19 keyless tests over the tree walk, hashing and URL forms
  - [ ] **BLOCKED:** Reddit 403s unauthenticated `.json` from this network — IP-level, not User-Agent. Spec §5.1's "OAuth is over-engineering" is now false and has been corrected. App-only OAuth is implemented and the token endpoint is reachable (401 not 403 with dummy credentials), so this needs a *script* app at `reddit.com/prefs/apps` and `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` in `.env`
  - *Done when:* one thread on disk, schema matches §5.2, no real handle anywhere in the file
- [x] **1.1b `feed/hn_fetch.py`** — Hacker News, no credentials, unblocked Phase 1
  - Algolia mirror returns a whole nested thread in one request
  - earns its place rather than just being available: PRD §3's audience is "technical-adjacent", and condescension is HN's native register, which exercises `contempt` — the axis the PRD calls the interesting one
  - weakness: `hostility` fires rarely, so the Bounced lane is thin (1–2% on the probe)
- [x] **1.1c `feed/store.py`** — anonymisation and schema shaping shared by all fetchers, so a new source cannot forget invariant 8
- [x] **1.1d `feed/convokit_fetch.py`** — ChangeMyView, Wikipedia Talk and Winning Arguments
  - [x] 866 comments from 120 conversations, 765 with parent context, no credentials
  - [x] topic filter dropped 94 conversations; blunt by design, `--no-topic-filter` to see them
  - [x] conversations bundled into one file. `TECHNICAL_SPEC.md` §3.3 said batch by thread so the shared state amortises — but under `quoted` addressing the state is *just the title*, so there is nothing left to amortise and bundling keeps batches full
  - [x] found and fixed the rule/gate boundary mismatch — `FINDINGS.md` §14, Bounced roughly doubled on both corpora
  - **Outcome:** ChangeMyView 70/4/27, Hacker News 77/3/19. Three visible lanes on both
- [x] **1.2 Fetch threads and eyeball the pile** — three HN threads, 6,590 comments
  - `hn-47340079` (HN's own AI-comment moderation policy — 1,562), `hn-24872911` (YouTube-dl DMCA — 1,392), `hn-41002195` (CrowdStrike — 3,636)
  - spread is real on `contempt` and `substance`; **thin on `hostility`**, which is the known HN weakness and the reason 1.1d still matters
- [ ] **1.3 Commit one small thread as a test fixture**
  - tests must run with no network and no key — 20–30 comments is enough
  - *Done when:* `uv run pytest` exercises the real replay path from a committed file

## Phase 2 — close the open measurements

Cheap, and each one changes a number that the UI will display. Do these before
building the thing that displays them.

- [x] **2.1 Per-axis rubric** — `FINDINGS.md` §11, via `experiments/axis_ab.py`
  - [x] `on_topic` → single referent, +0.147 paired
  - [x] `hostility` → the **degree ladder** wins by 0.114 over two situational rewrites, on both corpora, with no accuracy cost. `hostility` is now 0.813, the strongest axis in the rubric
  - [x] `substance` → situational phrasing wins by 0.045
  - [x] `contempt` → the one axis where confidence and accuracy disagreed. Took the 16:6 accuracy win over a marginal 0.028 confidence loss
  - [x] all variants scored **inside one request**, which gives perfect pairing and kills the arm-order confound in `FINDINGS.md` §7. Run every future rubric contest this way
  - **Outcome:** decisive confidence 0.538 → 0.599, auto-handled at floor 0.60 went 49% → 60%. But **16% at the current 0.85 floor, unchanged** — fixing `hostility` handed the bottleneck to `contempt`, whose three variants span only 0.034 and which looks close to its ceiling
- [x] **2.1b Gate on the decision, not on the level** — `FINDINGS.md` §13
  - [x] `Gate.DECISION` gates on probability mass one side of the line, on the axes that carried the verdict. **Demo went from 15/1/84 to 79/1/20.**
  - [x] measured honestly: it is **not more discriminative**. At matched volume the two gates track within ±0.03 with no consistent direction, correlation 0.845. Adopted for semantics and scale, not accuracy
  - [x] clean negative result on the second hypothesis: thresholding at a level boundary instead of the mean changes five comments out of 300. `SEVERITY_THRESHOLD` stays at 6.0
  - the docs' "weak in numerical calibration" warning is real but does not bite here — measure before building on a warning
- [x] **2.2 Measure confidence on data with real thread context** — `experiments/thread_probe.py`
  - the answer corrected `FINDINGS.md` §5 rather than confirming it: thread context did **not** rescue `on_topic`, it exposed a rubric ambiguity that Civil Comments had been hiding
  - `CONFIDENCE_FLOOR` deliberately **not** re-set yet — it should move after 2.1, not before, or we would be tuning the floor around a rubric we already know is fixable
- [ ] **2.3 Pen-quality audit** — PRD success criterion 4
  - read 30 penned comments cold and try to call them. If they are obvious, the
    threshold is wrong; if they are genuinely hard, the demo works.
  - this is the criterion that cannot be automated, and it is the one that matters
  - *Done when:* someone has actually read them and written down the hesitation rate
- [ ] **2.4 Characterise the 6–7% lane wobble** — `FINDINGS.md` §7
  - which comments flip under batching, and are they the near-threshold ones?
  - matters because live rubric editing re-sorts a backlog the audience has read
  - *Done when:* we know whether the flips cluster near thresholds (benign) or are scattered (not)
- [ ] **2.5 Interleave experiment arms** — only if a contrast starts carrying weight
  - arms currently run sequentially, so API drift is confounded with arm order
  - *Done when:* dropped with a reason, or the harness interleaves

## Phase 3 — streaming pipeline

- [x] **3.1 `feed/replay.py`** — `ThreadStore` + drip reader, loops forever, bounded queue for backpressure
  - fixed a starvation bug: at rate 0 the reader never yielded, because `Queue.put` returns without suspending while there is room. It hung the test suite; it would have hung the demo
- [x] **3.2 `judge/batcher.py`** — same-thread batches, flushed on size **or** a 0.4s timer
  - without the timer the demo freezes at low drip rates; age is tracked per thread so a quiet thread is not held hostage by a busy one
- [x] **3.3 Pipeline wired and verified on a console stream** — `judge/pipeline.py`
  - **225 items/sec sustained, 3 errors in 30s**, $0.036 per 1,000 comments
  - workers never touch the consumer; the console drains `out_queue` on the same 12Hz tick the browser will, so invariant 1's shape is exercised before any HTML exists
- [x] **3.4 Backlog** — last 2,000 verdicts in memory; raw text already in `ThreadStore`, so a rubric edit never touches disk
- [x] **3.5 Sustained-throughput check** — `FINDINGS.md` §16, and it overturned three spec assumptions
  - token-limited *and* request-limited, both binding near 300/s. **Raising B buys nothing**
  - 8 workers beats 24: same throughput, 15× fewer 429s, 3× better p95
  - bucket *capacity* matters as much as rate
  - **errors must be counted separately from the Pen.** Concurrency pressure turned a 14% Pen into 22%, the extra being failures rather than uncertainty — invisible unless counted

## Phase 4 — the web shell

- [x] **4.1** FastHTML app, routes, shell — plus `/health` (counters, queue depths, last error), because "the wall is blank" has four causes and guessing is slow
- [x] **4.2 WebSocket + fixed 12Hz render loop** — invariant 1
  - one broadcast loop for all clients: the pipeline has a single `out_queue`, so a loop per connection would hand each client a disjoint half of the stream
  - FastHTML's `setup_ws` is broken in 0.14.13 (`scope.client` on a dict); we keep our own registry — `FINDINGS.md` §17
- [x] **4.3** Three lanes, card component, four-bar fingerprint
- [x] **4.4 DOM cap with tail eviction** — invariant 7. Verified in-browser: the Pen holds at exactly 150 under a live stream
- [x] **4.5 Bounced blurred, click to reveal** — invariant 9. Verified: the clicked card unblurs, the rest stay blurred
- [x] **4.6** Counters as data attributes once per tick, eased by a JS odometer at frame rate — spec §8.3
- [x] **4.7** Spend meter from real `usage.input_tokens`
- **Card sampling:** Approved and Bounced show at most `CARDS_PER_FRAME` per tick (the counters carry the true totals). **The Pen is never sampled** — a penned card nobody can click is not a penned card

## Phase 4b — blocked on credits

- [ ] **4b.1 Top up the TypeSafe account.** Every request now returns `402 no available credits`. Nothing downstream can be measured or demoed until this is resolved
- [ ] **4b.2 Offline replay mode** — replay recorded verdicts from `experiments/out/probe-*.json` instead of calling the API
  - the same mitigation invariant 5 already applies to Reddit, applied to the model
  - it is what let the render layer be verified while the API was dead, so most of the work is proven
  - *Done when:* `uv run python -m web.app --offline` produces a full wall with no key and no credits

## Phase 5 — the Pen

The point of the demo. If time runs out, this is what must work.

- [ ] **5.1** Pen lane visually heavier than the other two — attention goes here
- [ ] **5.2** Allow / Bounce buttons, working, anyone can click
- [ ] **5.3 Show *why* a comment was penned** — which axis was decisive and its confidence
  - this is what makes the Pen legible rather than a shrug, and `decisive` gating
    means we already know exactly which axis it was
  - *Done when:* a viewer can tell at a glance which axis the model was unsure about

## Phase 6 — the three interaction hooks

- [ ] **6.1 Test your own comment** — same gate, same fingerprint card
  - per-IP rate limit and a hard total cap before this is ever exposed — spec §9
- [ ] **6.2 Live rubric editing** — edit levels, re-judge the backlog, re-sort
  - budget is under 3s; cap the re-judged window at ~800 if needed — spec §6
  - **be honest about what this shows.** The v1→v2 rewrite took careful work and
    came out a lateral move. A viewer editing a level mid-demo will plausibly
    make it worse, and "watch the confidence on that axis move" is a truer and
    more interesting story than "watch it get better"
- [ ] **6.3 Surface the tunables** — threshold, floor, gate, batch size, drip rate
  - config is already the single source; the UI reads from it, no second copy
- [ ] **6.4 Auto-handled share as a live number** next to the threshold control — PRD §6.1
  - replaces the hard-coded "94% / 6%" claim with something the room can check

## Phase 7 — calibration tab

- [ ] **7.1** Render the floor × gate curve from `calibrate/sweep.py` output
  - promoted from "cut first" — it is nearly free now and it is the direct answer
    to the accuracy objection in PRD §2

## Phase 8 — demo readiness

- [ ] **8.1** Disable sleep on the presenting machine
- [ ] **8.2** Measure latency and throughput from the demo machine and network
- [ ] **8.3** If exposing via tunnel: per-IP limit on `/test`, hard total cap
- [ ] **8.4** Two full dry runs, back to back — identical re-runs are the point of pre-baked data

---

## Standing constraints

Not tasks; they apply to every task above and a PR that breaks one is wrong even
if it works.

- Tests run with **no network and no API key**. Recorded fixtures only.
- `judge/lanes.py` stays pure — no I/O, no clock, no model calls.
- No magic numbers. Tunables live in `config.py` and are surfaced in the UI.
- Async throughout; the token bucket holds the rate, not the semaphore.
- **Compare experiment arms pairwise, never by aggregate rates.** A rate this
  close is one or two comments wide — `FINDINGS.md` §8.
- Any accuracy sweep includes a **noise-floor control**.
- Never ask Jev for a fact outside the state — invariant 4.

## Open questions for a human

- **Reddit credentials — blocking 1.1 and therefore all of Phase 1.** Someone
  needs to create a script app at <https://www.reddit.com/prefs/apps> and put
  the id and secret in `.env`. Two minutes, and nothing downstream moves without
  it. The alternative is running the fetch once from an unblocked network and
  committing the JSON.
- **1.2 / 2.3 are judgement calls, not code.** Someone has to read the pile and
  say whether the Pen is interesting. Worth deciding now who does that.
- **Does the demo ship with one thread or several?** Several is more convincing
  and costs nothing at fetch time, but batching is per-thread, so a thread
  switch is a visible seam in the stream.
- **What happens to a comment after Allow / Bounce is clicked?** Nothing
  persists (PRD §8), so it is a visual state change only. Fine, but it should be
  deliberate rather than discovered during the demo.
