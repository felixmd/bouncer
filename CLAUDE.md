# CLAUDE.md

## Project

**The Bouncer** — a live comment-moderation demo for TypeSafe's Jev model. Pre-baked Reddit comments stream toward a gate, Jev scores each on four axes, and they land in one of three lanes: Approved, Bounced, or **the Pen** (low confidence → a human decides).

The Pen is the point of the demo. Throughput and cost are secondary. When a design decision trades off Pen legibility against anything else, Pen wins.

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

   Refined by measurement: the gate applies the floor to the axes that **carried this comment's verdict** (`decisive`), not to `min()` of all four. `min()` of four axes pens 99% of traffic — that is arithmetic, not caution, and it left the demo with no Approved lane. Dropping the floor to compensate is the one move to avoid; the docs are explicit that it rebuilds the thing you were escaping.

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

**You are request-limited, not token-limited.** 20 req/s means batching is mandatory — one comment per request caps throughput at 20 items/sec. Use a token bucket to hold under 20/s explicitly; do not rely on the semaphore, because when latency drops you will blow through and collect 429s.

Use **Score**, not Noul, for the four axes. Nouls pile up at the extremes; Scores spread into a usable distribution. Confirmed — scores hit the rails only 25% of the time.

### Documented jagged edges that bite this project

From `docs.typesafe.ai/model-jaggedness/jev-1.13`:

- **"Cannot reliably count items, with error growing with the size of the thing being counted."** This is why we do not address comments by index inside a batch.
- **"Accuracy falls as the state grows with content unrelated to the decision."** At B=15, the other 14 comments are unrelated content for any one question. Hence `ADDRESSING = "quoted"`.
- **Score levels are "weak in numerical calibration."** Do not read the fractional part of a score as a magnitude. Put thresholds near level boundaries.
- **Literal interpretation** — it answers the question you wrote, not the one you meant.

## Rubric rules (highest leverage thing in the build)

Confidence on a Score *is* the concentration of probability across levels. Overlapping levels split the probability and look identical to a hard comment. So:

1. **Describe situations, not degrees.**
2. **One situation per level.** No level containing "X, or Y".
3. **One thing per question.**

Rewriting the rubric to these rules moved hostility recall from 0.43 to 0.57. A rubric problem masquerades as a model problem — when the Pen floods, check per-axis confidence before touching the floor.

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
