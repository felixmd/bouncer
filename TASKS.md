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

The judge half works and is measured. There is no UI and no Reddit data.

Throughput, cost, latency and batch size are settled and comfortable — see
`FINDINGS.md` §1 and §6. The risk profile has moved off the model integration
entirely. What is unproven is **whether the Pen holds interesting comments on
real data**, which is a content question, and everything in Phase 1 exists to
answer it.

Two open problems carried from the experiments:

- **Pen volume.** 73% on the smoke corpus, 78% on Jigsaw. The `decisive` gate
  fixed the arithmetic; the level is still wrong and cannot be tuned honestly
  until the curve is measured on data with thread context (`FINDINGS.md` §5).
- **v2's `hostility` levels are worse than v1's** (−0.083 confidence, paired)
  and nothing has been done about it (`FINDINGS.md` §3, "Open").

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
- [ ] **1.2 Fetch 3–5 threads and eyeball the pile**
  - r/AmItheAsshole, r/unpopularopinion, r/relationship_advice, r/politics, r/news
  - PRD §7's first risk is that the wall goes all green; this is the check
  - *Done when:* at least one thread has a visible spread of hostile / contemptuous / vacuous / ambiguous, judged by reading it
- [ ] **1.3 Commit one small thread as a test fixture**
  - tests must run with no network and no key — 20–30 comments is enough
  - *Done when:* `uv run pytest` exercises the real replay path from a committed file

## Phase 2 — close the open measurements

Cheap, and each one changes a number that the UI will display. Do these before
building the thing that displays them.

- [ ] **2.1 Per-axis rubric** — the explicit open item in `FINDINGS.md` §3
  - v1's `hostility` levels beat v2's by 0.083 confidence; v2's `on_topic` beats
    v1's by 0.165. Nothing tests the obvious combination.
  - *Done when:* a paired run over the same corpus says which per-axis mix wins, per axis, and `judge/rubric.py` holds that mix
- [ ] **2.2 Re-run `calibrate/sweep.py` against a fetched Reddit thread**
  - the curve is currently measuring Civil Comments' missing fields
  - *Done when:* `CONFIDENCE_FLOOR` is set from a curve built on data that has thread context, and the old value is recorded as superseded
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

First point where the shape of the running system exists. Verify against a
printed console stream before any HTML.

- [ ] **3.1 `feed/replay.py`** — read `data/threads/*.json`, emit at a configurable drip rate, loop forever
  - Reddit is never called here — invariant 5
- [ ] **3.2 `judge/batcher.py`** — group into `BATCH_SIZE` batches **from the same thread**
  - shared state only amortises within a thread
- [ ] **3.3 Wire the pipeline** — `replay → in_queue → batcher → workers → out_queue`
  - *Done when:* a console stream prints lane + fingerprint at the target rate with no 429s
- [ ] **3.4 Backlog cache** — keep the last ~2,000 comments' raw text in memory
  - live rubric editing must never touch disk — spec §6
- [ ] **3.5 Sustained-throughput check** — is 300/sec real over five minutes, not just in a burst?
  - *Done when:* a number measured from the demo machine, on the demo network

## Phase 4 — the web shell

- [ ] **4.1** FastHTML app, routes, static shell
- [ ] **4.2 WebSocket + fixed 12Hz render loop** — invariant 1
  - workers write to `out_queue`; a *separate* loop drains and emits one batched frame
  - *Done when:* one frame per tick under load, verified by counting frames, not by it looking fine
- [ ] **4.3** Three lanes, card component, four-bar fingerprint
- [ ] **4.4 DOM cap at ~150 cards with tail eviction** — invariant 7
  - *Done when:* the page is still responsive after ten minutes of streaming
- [ ] **4.5 Bounced lane blurred by default, click to reveal** — invariant 9
- [ ] **4.6** Counters: push raw numbers once per tick, JS odometer interpolates — spec §8.3
- [ ] **4.7** Spend meter from real `usage.input_tokens`, not an estimate

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
