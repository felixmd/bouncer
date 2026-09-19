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

## Commands

```bash
uv run python -m web.app                   # run the demo (localhost:5001)
uv run python -m feed.reddit_fetch URL     # dev-time: fetch a thread to data/threads/
uv run python -m calibrate.sweep           # threshold sweep against Jigsaw
uv run pytest                              # tests (no network, no API key needed)
uv run ruff check --fix .                  # lint
```

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

Scaffold only. Build order is in `TECHNICAL_SPEC.md` §10 — note that steps 1–3
(fetch script, the batch-size-vs-accuracy experiment, the judge pipeline) come
before any UI work, and `BATCH_SIZE` in `config.py` is deliberately unset until
that experiment has run.
