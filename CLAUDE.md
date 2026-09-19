# CLAUDE.md

## Project

**The Bouncer** — a live comment-moderation demo for TypeSafe's Jev model. Pre-baked Reddit comments stream toward a gate, Jev scores each on four axes, and they land in one of three lanes: Approved, Bounced, or **the Pen** (low confidence → a human decides).

The Pen is the point of the demo. Throughput and cost are secondary. When a design decision trades off Pen legibility against anything else, Pen wins.

See `PRD.md` for product logic and `TECHNICAL_SPEC.md` for architecture. This file is the rule set.

## Commands

```bash
uv sync                                    # install
uv run python -m web.app                   # run the demo (localhost:5001)
uv run python scripts/reddit_fetch.py URL  # dev-time: fetch a thread to data/threads/
uv run python -m calibrate.sweep           # threshold sweep against Jigsaw
uv run pytest                              # tests
uv run ruff check --fix .                  # lint
```

`TYPESAFE_API_KEY` must be set. Never commit it, never inline it, never put it in a fixture.

## Hard invariants

Do not violate these without asking. Each one exists because breaking it kills the demo in a specific way.

1. **Never push one WebSocket message per classification.** Workers write to `out_queue`; a separate loop drains it on a fixed 12Hz tick and emits one batched frame. Coupling them produces hundreds of DOM swaps per second and a dead page.

2. **Confidence is never a category.** Jev returns confidence alongside every answer. It must never appear as an option inside a Choice or a level inside a Score. Lanes are computed in Python from scores *plus* confidence as two separate axes.

3. **Check the confidence gate first** in `lanes.py`. A low-confidence "clearly fine" still goes to the Pen. Short-circuiting this to reduce Pen volume guts the demo.

4. **Never ask Jev a question requiring a fact outside the state.** It is a non-generative decision model trained on synthetic data, not a knowledge store. No truth-checking, no fact-checking, no misinformation detection, no arithmetic, no dates, no counting. Compute those in Python and pass the result as prose.

5. **Reddit is never called at runtime.** `reddit_fetch.py` is a dev-time script writing JSON to disk. The app reads only from `data/threads/*.json`.

6. **No second model.** Not for summarisation, not for anything. Thread context comes from Reddit's own title and selftext fields. Adding an LLM to a Jev demo defeats the demo.

7. **Cap the DOM at ~150 cards** with tail eviction.

8. **Hash usernames at fetch time.** Real handles never enter the data files or the UI.

9. **Bounced lane blurs by default.** Click to reveal.

10. **Pin the Jev model version.** Never `jev-latest` — it moves and will move under tuned thresholds. Log the `model` field from the response.

## Jev constraints

Verify all of these against `docs.typesafe.ai` before relying on them; they come from launch-window sources.

- Text only. No images, no streaming, no generation.
- Primitives: Choice (≤255 options), Score (2–10 rubric levels), Noul (0–1 probability).
- Questions fan out in parallel against one shared state — the 4th question costs tokens but almost no time.
- Rate limits: 1,200 req/min (20/sec) and 250K tokens/sec.
- Context ~64K for state + all questions.
- $0.042/M input tokens; output free.
- Latency 70–500ms from US West Coast.

**You are request-limited, not token-limited.** 20 req/s means batching is mandatory — one comment per request caps throughput at 20 items/sec. Use a token bucket to hold under 20/s explicitly; do not rely on the semaphore, because when latency drops you will blow through and collect 429s.

Use **Score**, not Noul, for the four axes. Nouls pile up at the extremes; Scores spread into a usable distribution.

## Before building any UI

Run the batch-size experiment in `TECHNICAL_SPEC.md` §3.2. Jev evaluates questions in isolation against a shared state, so in a batched request it must locate "comment #7" inside a 15-comment blob. Accuracy will degrade with batch size and nobody knows where the knee is. Prior guess 8–15. If it turns out to be 3, the whole throughput story changes.

Do not skip this and do not build around an assumed batch size.

## Conventions

- Async throughout. `AsyncTypeSafeClient`, `asyncio.Queue`, semaphore-bounded workers.
- `judge/lanes.py` stays a pure function — no I/O, no model calls, fully unit-testable.
- Tests must run without network and without an API key. Use recorded fixtures.
- Tunable constants (`SEVERITY_THRESHOLD`, `CONFIDENCE_FLOOR`, batch size, drip rate, tick rate) live in one config module and are surfaced in the UI. No magic numbers in logic.
- FastHTML + HTMX. CSS transitions for motion. No React, no build step. If motion needs more, the escape hatch is one `<canvas>` fed by the same WebSocket — not a frontend rewrite.
- On persistent Jev failure, route the comment to the Pen. Degrading into "a human looks at it" is the correct failure mode here.

## Naming

The app is **The Bouncer**. Not ToxicGate — the `-gate` suffix parses as scandal rather than doorway. `Velvet Rope` is an acceptable alternative if the metaphor is kept consistent (the Pen becomes "the line").
