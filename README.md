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
uv run --env-file .env python -m web.app          # run the demo (localhost:5001)
uv run --env-file .env python -m calibrate.sweep  # threshold sweep against Jigsaw
uv run python -m feed.reddit_fetch URL            # dev-time: fetch a thread to data/threads/
uv run pytest                                     # tests (no network, no key)
uv run ruff check --fix .                         # lint
```

Only the two commands that call Jev need the key. The fetch script talks to
Reddit, and the tests run against recorded fixtures by design.

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

The judge half works and is measured. No UI yet.

- `judge/` — client with token bucket, the four-axis rubric, the lane policy
- `calibrate/` — labelled-sample fetch and the threshold/gate curve
- `experiments/` — the three runs behind `FINDINGS.md`

Headline results: batching costs nothing up to B=30 (so 300 items/sec is
comfortable), latency p50 is 169 ms, cost is ~$0.038 per 1,000 comments, and
the confidence signal is genuinely calibrated — agreement rises monotonically
with it. The open problem is Pen volume, and the next step is step 1 of
`TECHNICAL_SPEC.md` §10: get a real Reddit thread on disk, because the
calibration curve is currently being measured on a dataset with no thread
context and `on_topic` is paying for it.
