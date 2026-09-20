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

**Everything in the PRD is built.** The wall, all three interaction hooks, the
tunables, and the calibration curve. What remains is demo readiness (Phase 8),
the offline replay mode (4b.2), and one judgement call nobody has made yet
(2.3 — reading the Pen cold to confirm it is genuinely hard).


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
- [ ] **2.3 Pen-quality audit** — PRD success criterion 4, and **the last thing measurement cannot settle**
  - read 30 penned comments cold and try to call them. If they are obvious, the threshold is wrong; if they are genuinely hard, the demo works
  - now free and repeatable: `uv run python -m web.app --offline` needs no key and no credits
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
- [x] **4b.2 Offline replay** — `uv run python -m web.app --offline`. `FINDINGS.md` §25
  - **6,492 judged, 0 errors, $0.0000, no key in the environment** — the offline launch config does not even pass `--env-file`
  - fakes at the *client boundary*, so the batcher, rubric, lane policy, gate and render loop are the real ones. Threshold and gate controls still work offline (a pure recompute over stored probabilities — 29% auto-handled at floor 0.99, 96% at 0.55), and so does Allow/Bounce
  - **every score on screen is a real verdict.** 350 of them, baked from `thread_probe` by `feed/bake.py` and committed. Reusing answers for unscored comments would have given full coverage and was rejected — a fabricated fingerprint in front of a room is a lie, and the cost is only that offline mode streams the 350 it has
  - rubric editing and test-your-own-comment ask the model something new, so they refuse and name the flag to drop rather than return stale numbers
  - the bug worth remembering: question keys are `{comment_id}_{axis}` and `on_topic` contains an underscore, so splitting on the last one matched nothing and every comment errored

## Phase 5 — the Pen

- [x] **5.1** Pen lane visually heavier — amber, larger body text, wider column
- [x] **5.2 Allow / Bounce, working, anyone can click.** The card leaves the Pen, lands in the chosen lane marked "you decided", loses its buttons, and a counter tracks what the room resolved
  - decisions travel the WebSocket frame, not the POST response, so all DOM mutation is on one channel serialised on the tick
  - the bug worth remembering: the resolved card was being truncated away by the per-frame sample. Allow failed and Bounce worked, and the tell was that it tracked lane *volume*, not lane identity — `FINDINGS.md` §20
- [x] **5.3 Penned cards say which axis was unsure**, which is the difference between a Pen that reads as a decision and one that reads as a shrug

## Phase 5b — cost control

The credits ran out twice. `FINDINGS.md` §19 has the numbers: **$0.034 per
1,000 comments but $0.41 per minute** of continuous streaming, because 93% of
every request is rubric and comment duplication and we run at roughly half the
API's maximum possible token rate.

- [x] **5b.1 Judge only while someone is watching.** `Replay` waits on a demand event that `web/app.py` clears when no socket is connected. `preview_stop` does not kill the `uv run` child, so eight orphaned servers had been judging at ~225/s for nobody — this is what drained the account, and it also caused the 429 storm that looked like a rate-limit mystery in §16
- [x] **5b.2 Ambient drip rate is 60/s** (~$7.25/hour), with a slider to 300 for the moment someone asks for the headline number. Measured: 20/s → 19 items/sec at 2 req/s, 200/s → 195 at 13 req/s, a 6.5× spend difference on one control. The render cap only shows ~96 cards/sec anyway
- [ ] **5b.3 Index-address `hostility` and `contempt`.** §2's `quoted` win was entirely on `on_topic` and `substance`; both intrinsic axes were *ns*, so they can read the comment from shared state instead of quoting it. **~7%, not the 13% first estimated** — index addressing still puts the body in state once, so it is four copies to three. Smallest of the three levers, untested

## Phase 6 — the three interaction hooks

- [x] **6.1 Test your own comment** — same gate, same rubric, one request, the same fingerprint. `FINDINGS.md` §22
  - [x] per-IP sliding window plus a hard total cap for the process run — spec §9. A held request does not consume the total budget, or a retry loop would exhaust the cap without anyone getting an answer
  - [x] an optional "replying to…" field, which is **not decoration**: `on_topic` scored against nothing is a documented cause of low confidence (§9), so without it every test comment would pen for a reason unrelated to what was typed
  - **Worth being ready to explain:** a textbook contempt comment scores `contempt` 9.2/10 and still pens, because the gate is only 79% sure and `contempt` is the axis §11 found has a floor around 0.66. The weakest axis is the one a viewer is most likely to probe on purpose
- [x] **6.2 Live rubric editing** — `FINDINGS.md` §23. **630 comments re-judged in 2.9s for $0.0305, 43 changed lane.** Inside spec §6's three-second budget
  - one axis at a time, because §11 found the winning levels differ per axis
  - the report shows **every axis, before and after, on the same comments** — showing only the edited one would hide the cost, and showing only "43 moved" would imply motion is progress
  - **the test edit made things worse, and that is the finding.** Removing an "X, or Y" level — exactly what the rubric rules say to do — dropped `contempt` confidence by 0.017. Third independent confirmation that axis has a floor rewording cannot move
  - PRD §4.2's claim survives intact: a sentence, three seconds, three cents, no retraining pipeline. What does *not* survive is any suggestion the edit will be an improvement, and the UI says so above the fields
- [x] **6.3 Tunables surfaced** — drip rate, confidence floor, severity, gate
  - **retuning is free**: every verdict carries its scores and per-level probabilities, so a new threshold is a pure recompute over the backlog with no model calls. Verified live — dragging the floor from 0.99 to 0.55 re-sorted the whole wall for zero extra API requests (`FINDINGS.md` §21)
  - the expensive cousin is 6.2: rubric *wording* changes the question and does need re-judging
- [x] **6.4 Auto-handled share is live** beside the controls — PRD §6.1
  - 31% at floor 0.99, 75% at 0.85, 96% at 0.55. The honest version turned out to be cheaper to run than the hard-coded claim would have been

## Phase 6 — done

All three PRD §4.2 interaction hooks work: the Pen with Allow/Bounce, live
rubric editing, and test-your-own-comment. Plus the tunables and the live
auto-handled share.

## Phase 7 — calibration tab

- [x] **7.1 The curve** — `/calibration`, `FINDINGS.md` §24
  - **agreement rises monotonically 0.807 → 0.950 as the floor rises 0.50 → 0.99**, while auto-handled falls 100% → 47%. The lines cross, and the crossing point is the trade. That is PRD §2's answer to the accuracy objection, on a chart
  - entirely offline: renders `calibrate/curve.json` and calls nothing. The objection gets answered from evidence already gathered, not a live run whose numbers move while someone watches
  - `calibrate/sweep.py --write` regenerates it; the JSON is committed so the tab works on a fresh clone with no key
  - gate selector (min2 / mean2 / decision), the shipped operating point marked on the chart, hover crosshair, and a table-view twin so no value is reachable only by hover
  - the caveat is **printed above the chart**: agreement is against a label that is mostly insult and abuse, so it tests `hostility` and `contempt` and not `substance` or `on_topic`
  - colours chosen by running the validator, not by eye. The first pair looked clearly distinct and scored ΔE 2.7 under deuteranopia

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
