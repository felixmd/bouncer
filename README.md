# The Bouncer

A live comment-moderation demo for TypeSafe's Jev model.

Pre-baked Reddit comments stream toward a gate. Jev scores each on four axes —
`hostility`, `contempt`, `substance`, `on_topic` — and they land in one of three
lanes: **Approved**, **Bounced**, or **the Pen**.

The Pen is the point. It holds the comments Jev judged with low confidence, where
a human decides. The claim the demo makes is not "this model is right" — it is
"this model knows when it isn't, and escalates."

- `PRD.md` — product logic, especially §5 (the two-axis model)
- `TECHNICAL_SPEC.md` — architecture, Jev integration, build order
- `FINDINGS.md` — **what measurement changed.** Supersedes parts of both above
- `TASKS.md` — the plan and current progress
- `CLAUDE.md` — the rule set, including the hard invariants

## Setup

Requires [uv](https://docs.astral.sh/uv/). The interpreter is pinned to Python
3.12 in `.python-version`; `uv sync` will fetch it if it isn't present.

```bash
uv sync
```

Set your API key — never commit it, never inline it, never put it in a fixture:

```bash
cp .env.example .env   # then fill in TYPESAFE_API_KEY
```

`.env` is gitignored. The SDK reads `TYPESAFE_API_KEY` from the environment and
sends it as a bearer token; `uv run --env-file .env` is what gets it there.

## Commands

```bash
uv run --env-file .env python -m web.app     # the demo, live (localhost:5001)
uv run python -m web.app --offline           # the demo, from recordings — no key, no spend
uv run python -m calibrate.sweep             # the calibration curve (offline)
uv run python -m feed.hn_fetch --search      # dev-time: find and fetch a thread
uv run pytest                                # tests (no network, no key)
uv run ruff check --fix .                    # lint
```

**`--offline` is the one to reach for.** It replays 350 recorded verdicts, so
there is no key, no spend and no network, and the lane policy, the gate and the
threshold controls are the real ones — they are a pure recompute over stored
probabilities. Only a rubric edit or a typed comment needs the model, and both
say so rather than return stale numbers.

Live mode costs **$0.41 a minute** at full drip rate. Read `FINDINGS.md` §19
before leaving it running.

## Layout

| Path | Responsibility |
|---|---|
| `config.py` | Every tunable constant. No magic numbers elsewhere |
| `feed/` | Replay reader; dev-time Reddit fetch |
| `judge/` | Batcher, Jev client, rubric, lane classification |
| `web/` | FastHTML routes, WebSocket, the 12Hz render loop |
| `calibrate/` | Jigsaw threshold sweep and calibration curve |
| `data/threads/` | Pre-baked thread JSON. Reddit is never called at runtime |

## Status

Working end to end. 7,456 pre-baked comments streaming through the judge onto a
three-lane wall, all three PRD §4.2 interaction hooks, and the calibration
curve.

| | |
|---|---|
| throughput | 225 items/sec sustained (target was 200) |
| lanes | ~79% Approved, ~3% Bounced, ~19% Pen |
| latency | p50 285 ms |
| cost | $0.034 per 1,000 comments — **and $0.41 per minute of streaming** |
| calibration | agreement 0.807 → 0.950 as the floor rises; monotonic |

**Read `FINDINGS.md`.** It is the deliverable as much as the app is, and it
records what measurement changed: the batch-size knee does not exist (§1), the
rubric mattered more than the model (§11), the gate was measuring the wrong
quantity (§13), the throughput story in the spec was wrong in three places
(§16), and cost per minute is the number that actually governs running this
(§19).

Two things a reader should know up front: `contempt` has a confidence floor
near 0.66 that three separate rewrites could not move, and a thin Bounced lane
is a property of real discussion data rather than a bug — both are written up
rather than tuned away.
