# CLAUDE.md

## Project

**The Bouncer** — a live comment-moderation demo for TypeSafe's Jev model. Pre-baked Reddit comments stream toward a gate, Jev scores each on four axes, and they land in one of three lanes: Approved, Bounced, or **the Pen** (low confidence → a human decides).

The Pen is the point of the demo. Throughput and cost are secondary. When a design decision trades off Pen legibility against anything else, Pen wins.

**This is a capability measurement, not a sales demo.** The object is to find out how far this paradigm carries a real problem. A limitation found and characterised is a result, not a failure — if the POC does not work because of what the model can or cannot do, *that is the headline*, and it is worth more than a staged success. So: measure before claiming, report negative results as prominently as positive ones, and never tune the data or the thresholds to make a lane look busier than the model earned. `FINDINGS.md` is the deliverable as much as the app is.

See `PRD.md` for product logic, `TECHNICAL_SPEC.md` for architecture, and **`FINDINGS.md` for what measurement changed** — it supersedes parts of both. This file is the rule set.

## Commands

```bash
uv sync                                                 # install
uv run --env-file .env python -m web.app                # run the demo (localhost:5001)
uv run python -m feed.reddit_fetch URL                  # dev-time: fetch a thread
uv run python -m calibrate.fetch_jigsaw --n 300         # dev-time: labelled sample
uv run python -m calibrate.sweep                        # threshold + gate curve (offline)
uv run pytest                                           # tests
uv run ruff check --fix .                               # lint

# experiments — live API, see FINDINGS.md
uv run --env-file .env python -m experiments.smoke15      # 15 hand-written comments
uv run --env-file .env python -m experiments.batch_sweep  # B = 1..30
uv run --env-file .env python -m experiments.rubric_ab    # rubric x addressing, 2x2
```

`TYPESAFE_API_KEY` must be set. Never commit it, never inline it, never put it in a fixture.
It lives in `.env` (gitignored, copied from `.env.example`) and reaches the process via
`uv run --env-file .env`. The SDK reads the variable itself — never pass `api_key=` in code.

## Hard invariants

Do not violate these without asking. Each one exists because breaking it kills the demo in a specific way.

1. **Never push one WebSocket message per classification.** Workers write to `out_queue`; a separate loop drains it on a fixed 12Hz tick and emits one batched frame. Coupling them produces hundreds of DOM swaps per second and a dead page.

2. **Confidence is never a category.** Jev returns confidence alongside every answer. It must never appear as an option inside a Choice or a level inside a Score. Lanes are computed in Python from scores *plus* confidence as two separate axes.

3. **Check the confidence gate first** in `lanes.py`. A low-confidence "clearly fine" still goes to the Pen. Short-circuiting this to reduce Pen volume guts the demo.

   Refined by measurement, twice:

   - The floor applies to the axes that **carried this comment's verdict**, not to `min()` of all four. `min()` of four axes pens 99% of traffic — arithmetic, not caution.
   - It applies to **probability mass on one side of the line** (`decision`), not to `ScoreAnswer.confidence`. The scalar is concentration across the five levels, which is a different question: a comment spread across levels 0/1/2 is unsure of the level and certain of the decision, and penning it wastes the scarcest resource in the product.

   `decision` is **not more accurate** than the scalar gate — at matched auto-handled volume the two agree with human labels within ±0.03, correlation 0.845. Same curve. It is adopted because the number means the thing the lane turns on, so the floor reads in plain English. Do not report a volume change as an accuracy win.

   Dropping the floor to reduce Pen volume remains the one move to avoid; the docs are explicit that it rebuilds the thing you were escaping.

4. **Never ask Jev a question requiring a fact outside the state.** It is a non-generative decision model trained on synthetic data, not a knowledge store. No truth-checking, no fact-checking, no misinformation detection, no arithmetic, no dates, no counting. Compute those in Python and pass the result as prose.

5. **Reddit is never called at runtime.** `reddit_fetch.py` is a dev-time script writing JSON to disk. The app reads only from `data/threads/*.json`.

6. **No second model.** Not for summarisation, not for anything. Thread context comes from Reddit's own title and selftext fields. Adding an LLM to a Jev demo defeats the demo.

7. **Cap the DOM at ~150 cards** with tail eviction.

8. **Hash usernames at fetch time.** Real handles never enter the data files or the UI.

9. **Bounced lane blurs by default.** Click to reveal.

10. **Pin the Jev model version.** Never `jev-latest` — it moves and will move under tuned thresholds. Log the `model` field from the response.

## Jev constraints

- Text only. No images, no streaming, no generation.
- Primitives: Choice (≤255 options), Score (2–10 rubric levels), Noul (0–1 probability).
- Questions fan out in parallel against one shared state — the 4th question costs tokens but almost no time. Confirmed: 120 questions in one request all come back.
- Rate limits: 1,200 req/min (20/sec) and 250K tokens/sec.
- Hard ceilings: **64K for state + all questions**, and **32K for state + the single longest question**.
- $0.042/M input tokens; output free. Measured ~$0.038 per 1,000 comments at B=15.
- Latency 70–500ms from US West Coast; measured **p50 169ms** from this laptop.
- Import is `typesafe_sdk`, not `typesafe`. Client kwarg is `retry=`, not `retry_policy=`.

~~**You are request-limited, not token-limited.**~~ **Measured: you are both, at once.** Batching is still mandatory — one comment per request caps you at 20 items/sec. But a request is ~12,000 tokens, not the 2,400 the spec estimated, because every question restates the whole rubric. At ~800 tokens/comment the 250K/sec ceiling and the 20 req/s ceiling both bind near 300 items/sec, so **raising B buys nothing.** See `FINDINGS.md` §16.

Three pipeline settings that are not obvious and were each found the hard way:

- **Use a token bucket, and keep its capacity small.** Capacity 20 at rate 20 averaged 16 req/s and still collected 41 rate-limit errors — a capacity equal to the per-second rate lets a whole second of requests leave in one instant. Now rate 18, capacity 4.
- **8 workers, not 32.** 24 workers gained 9% throughput and multiplied 429s fifteenfold.
- **Count errors separately from the Pen, always.** A failed request pens fifteen comments by design, so concurrency pressure quietly turned a 14% Pen into a 22% one where the extra was failures. A Pen full of errors is indistinguishable from a Pen full of hard cases and is worthless.

## Cost: cheap per decision, expensive per minute

$0.034 per 1,000 comments — and **$0.41 per minute** of streaming at 205/s, which is $24.80/hour. Both are true; the second is the one that governs running the thing, and it drained the account's credits twice before anyone computed it. See `FINDINGS.md` §19.

The unit price is not the problem: 93% of every request is overhead, because each of the four Score questions carries its own copy of the rubric *and* of the comment. At 205/s that is ~121K tokens/sec against a 250K ceiling — roughly half the maximum this API can bill.

- **Never leave the app running unattended.** `preview_stop` does not kill the `uv run` child; check for orphaned `python` processes after restarting. Eight of them judging for nobody is what burned the credits.
- **The pipeline only judges while a browser is connected** (`Replay.demand`). Keep it that way.
- **The drip rate is the cost dial**, and it is already in the UI. 20/s still reads as a stream and costs a tenth as much.
- Before optimising tokens, remember that rubric wording *is* confidence (see the rubric rules). Shortening levels trades directly against the Pen.

Use **Score**, not Noul, for the four axes. Nouls pile up at the extremes; Scores spread into a usable distribution. Confirmed — scores hit the rails only 25% of the time.

### Documented jagged edges that bite this project

From `docs.typesafe.ai/model-jaggedness/jev-1.13`:

- **"Cannot reliably count items, with error growing with the size of the thing being counted."** This is why we do not address comments by index inside a batch.
- **"Accuracy falls as the state grows with content unrelated to the decision."** At B=15, the other 14 comments are unrelated content for any one question. Hence `ADDRESSING = "quoted"`.
- **Score levels are "weak in numerical calibration."** Do not read the fractional part of a score as a magnitude. Put thresholds near level boundaries.
- **Literal interpretation** — it answers the question you wrote, not the one you meant.

## Rubric rules (highest leverage thing in the build)

Confidence on a Score *is* the concentration of probability across levels. Overlapping levels split the probability and look identical to a hard comment. So:

1. **Make the levels one unambiguous ordering.** This is the actual mechanism.
2. **One situation per level.** No level containing "X, or Y".
3. **One thing per question.**
4. *Describe situations, not degrees* — the docs' rule, and a good heuristic for (1), but **not reliable on its own.** The winning `hostility` levels are a degree ladder ("mildly pointed", "openly critical", "sustained abuse") and they beat two careful situational rewrites by 0.114 and 0.099 on both corpora. Both rewrites had smuggled in a rung that was a different dimension rather than a lower degree, and probability split between it and its neighbour.

Evidence: `experiments/axis_ab.py`, paired, n=300 each on Hacker News and Civil Comments. Per-axis winners differ — `hostility` v1, `substance` v2, `on_topic` v3 — so apply the rules **per axis, judged on that axis's confidence**, never as a wholesale version bump.

**Score every variant inside one request.** Questions are evaluated independently against a shared state, so all variants of all axes can go in together. That gives perfect pairing and removes the arm-order confound that sequential arms carry. There is no reason to run a rubric contest any other way.

Diminishing returns are real: `contempt`'s three variants span only 0.034 against `hostility`'s 0.114, and two of them were written specifically to fix it. When an axis stops responding to rewording, stop rewording it.

A rubric problem masquerades as a model problem — when the Pen floods, check per-axis confidence before touching the floor.

**Measure arms pairwise, never by aggregate rates.** All arms score the same corpus in the same order, so per-comment comparison is always available. A lane-agreement move of 0.793 → 0.800 is two comments out of 300; it once got reported as a win.

## Before building any UI

~~Run the batch-size experiment~~ — **done, see `FINDINGS.md` §1.** There is no knee: accuracy is flat from B=1 to B=30. `BATCH_SIZE = 15`, chosen for headroom under the 64K ceiling.

Two things carried forward from it:

- **Any accuracy sweep must include a noise floor control.** Two identical B=1 runs differ by 0.081/10 — the model is nearly deterministic, so drift that looks small is not necessarily noise. Without that control the batch sweep would have been unreadable.
- **Do not measure `on_topic` or `substance` on a dataset without thread context.** Civil Comments has none, which depresses `on_topic` confidence from 0.86 to 0.50 and drags the whole calibration curve down. That is the dataset, not the model.

## Conventions

- Async throughout. `AsyncTypeSafeClient`, `asyncio.Queue`, semaphore-bounded workers.
- `judge/lanes.py` stays a pure function — no I/O, no model calls, fully unit-testable.
- Tests must run without network and without an API key. Use recorded fixtures.
- Tunable constants (`SEVERITY_THRESHOLD`, `CONFIDENCE_FLOOR`, batch size, drip rate, tick rate) live in one config module and are surfaced in the UI. No magic numbers in logic.
- FastHTML + HTMX. CSS transitions for motion. No React, no build step. If motion needs more, the escape hatch is one `<canvas>` fed by the same WebSocket — not a frontend rewrite.
- On persistent Jev failure, route the comment to the Pen. Degrading into "a human looks at it" is the correct failure mode here.

## Naming

The app is **The Bouncer**. Not ToxicGate — the `-gate` suffix parses as scandal rather than doorway. `Velvet Rope` is an acceptable alternative if the metaphor is kept consistent (the Pen becomes "the line").
