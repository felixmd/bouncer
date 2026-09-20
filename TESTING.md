# Testing the app by hand

Discrete checks you can run one at a time and stop. Nothing here needs to be
left streaming.

Three tiers, cheapest first. **Tiers 1 and 2 need no API key, cost nothing, and
cover most of the app.** Only tier 3 spends money, and it says how much.

---

## First: make sure nothing is already running

This bites. Closing a terminal, stopping a run from an editor, and Ctrl-C in
some shells all leave the `uv run` child alive — and a live orphan keeps judging
at full rate. Eight of them is what drained the credits twice during the build.

The app only ever listens on 5001 (or `PORT`), so the port is the reliable way
to find one:

```bash
lsof -i :5001                     # macOS / Linux — anything still listening?
pkill -f "python -m web.app"      # stop it
```

```powershell
# Windows PowerShell
Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

Expect no output. Run it again after any check that starts a server.

> **If Windows says "Access is denied", that is not a Python problem.** A server
> started *through an IDE or a coding agent* is owned by that tool's sandbox,
> and your ordinary user token cannot terminate it — `Stop-Process`, `taskkill
> /F` and Task Manager all refuse identically. Three ways out, cheapest first:
>
> 1. Re-run the kill from an **elevated** PowerShell (Run as administrator).
> 2. Task Manager → Details → *End process tree* on the parent `uv.exe`, itself
>    run as administrator.
> 3. Leave it. The processes die on logoff, and an orphan only spends while a
>    browser is attached to it — check `sockets` in `/health` (below) to be
>    sure. `judged` frozen across two checks a minute apart is the proof.
>
> Servers you started yourself, in your own terminal, kill normally.

---

## Tier 1 — no key, no server, ~40 seconds

### 1.1 The test suite

```bash
uv run pytest -q
```

**Expect:** `128 passed`. Every test runs without network or credentials by
design, so a failure here is a real regression rather than a setup problem.

### 1.2 Lint

```bash
uv run ruff check .
```

**Expect:** `All checks passed!`

### 1.3 The calibration sweep

```bash
uv run python -m calibrate.sweep
```

**Expect** three tables (`min2`, `mean2`, `decision`) and a `knee:` line under
each. For the `decision` gate, agreement should climb steadily as the floor
rises — 0.807 at floor 0.50 up to 0.950 at 0.99 — while auto-handled falls from
100% to 47%.

**What it proves:** the core product claim. The model's confidence predicts its
accuracy, so a threshold buys agreement in exchange for volume. If agreement
were flat across the floor, the Pen would be decoration.

---

## Tier 2 — the wall, offline. No key, no spend

```bash
uv run python -m web.app --offline
```

Open <http://127.0.0.1:5001>.

This replays 350 real recorded verdicts. The lane policy, the gate, the
threshold sliders and Allow/Bounce are all the genuine article — they are a pure
recompute over stored probability distributions. Only two features need a live
model, and they are hidden in this mode (see tier 3).

> **Turn the drip down to 5/s first.** The slider is top-left. At the default
> 60/s the wall churns faster than you can read it; at 5/s it trickles and you
> can actually inspect cards.

### 2.1 The wall is alive

**Expect:** three columns filling — Approved (green edge), Bounced (red, text
blurred), The Pen (amber, larger text, Allow/Bounce buttons). A blue banner
across the top saying verdicts are being replayed.

**Header should read:** `$0.0000` spent and `0` errors. If either moves, offline
mode is not doing what it claims.

### 2.2 Every card carries a fingerprint

Look at any card: four thin bars under the author name — hostility, contempt,
substance, on_topic, left to right. Hover a bar for its value.

**What to look for:** the *shape* differs between lanes. Approved cards tend to
show low red/orange and high green/blue. That silhouette is the whole reason
the four axes use Score rather than a yes/no primitive.

### 2.3 Bounced is blurred until you click it

Click any card in the Bounced column.

**Expect:** that card's text sharpens; the others stay blurred. Click again to
re-blur.

**Why it is there:** real moderation tools do this, and the demo goes on a
screen in an office.

### 2.4 The Pen says *why*

Read the top-right of any Pen card.

**Expect:** something like `unsure: contempt 0.42` — which axis the model was
least sure about.

**What it proves:** the Pen is a decision, not a shrug. A human looking at it
knows what the model could not settle.

### 2.5 Allow / Bounce — the loop closes

Click **Allow** on a Pen card.

**Expect:** the card leaves the Pen and reappears at the top of Approved,
marked `you decided`, with its buttons gone and an amber right edge. The
`YOU DECIDED` counter in the header increments.

Try **Bounce** on another. It should land in Bounced, marked the same way, and
*not* blurred — you have already seen it.

**What it proves:** the Pen drains as people work it, which is the
human-in-the-loop story the demo is making.

### 2.6 The threshold slider — the best thing in here

Drag **confidence floor** to `0.99`, wait a second, then to `0.55`.

**Expect** the whole wall to re-sort each time, and `AUTO-HANDLED` (top right)
to swing roughly:

| floor | auto-handled |
|---|---|
| 0.99 | ~29% |
| 0.85 | ~76% |
| 0.55 | ~96% |

**What it proves, and it is the good bit:** this costs *nothing*. Every verdict
already carries its four scores and four probability distributions, so a new
threshold is a pure recompute. Check `$0.0000` has not moved. A viewer can drag
the floor and watch the Pen fill or drain, and read the trade off the screen
instead of being told a percentage.

Also try the **severity** slider and the **gate** dropdown (`decision` /
`decisive` / `min_all`). `min_all` is the rule the original spec called for —
it should send almost everything to the Pen, which is why it was replaced.

### 2.7 The DOM stays bounded

Leave it at 60/s for two minutes, then:

```bash
curl -s http://127.0.0.1:5001/health
```

**Expect** `api_errors: 0`, and the page still responsive with no scrollbars
inside the lanes. Cards are capped at 150 per lane with tail eviction; without
that the page dies after a couple of minutes.

### 2.8 The calibration tab

Click **calibration →** in the header.

**Expect** two lines crossing: auto-handled falling from 100%, agreement rising
to 95%, with the shipped operating point marked at floor 0.85. Hover any column
for a readout. Expand **table view** for every number.

**Read the caveat above the chart.** Agreement is measured against a human label
that is mostly insult and abuse, so it tests `hostility` and `contempt` and
*not* `substance` or `on_topic`.

### Stop it

Ctrl-C, then the orphan check from the top of this file.

---

## Tier 3 — live. Costs money, so bounded

Needs `TYPESAFE_API_KEY` in `.env`. **Check there are credits first** — the
account has run out twice, and the symptom is every card reading
"could not judge" (which is the correct failure, but not a demo).

Note that 3.1 is the only check in this file with no browser in it. The wall
starts at 3.2 and stays up for 3.3 as well.

### 3.1 Throughput on your own network — console only, ~$0.08

```bash
uv run --env-file .env python -m judge.pipeline --rate 200 --seconds 10
```

**This is a benchmark, not the app.** It drives the pipeline straight from the
command line, prints a summary and exits — it starts no web server, so nothing
will be at <http://127.0.0.1:5001> during or after it. A browser there will
say the site is unreachable, or show your proxy's 404. That is the command
working.

**There is no `--offline` for it either; it always calls the model.**

**Expect** a summary: ~200/sec, a handful of errors at most, p50 latency around
300ms, and a lane mix near 80/4/16. The p50 is the number worth comparing — the
docs quote 285ms from a laptop on a home connection, and a corporate network
sits somewhere else.

### 3.2 Test your own comment — ~$0.000034 each, 6/min

Start live and open the wall:

```bash
uv run --env-file .env python -m web.app
```

The box is at the foot of the Pen column (hidden in offline mode — it needs the
model). Set the drip to 5/s first so you are not paying for a stream you are
not watching.

Try these three:

| type this | expect |
|---|---|
| `You are a complete moron and everyone here knows it.` | **bounced**, ~95% sure |
| `I think the second point is wrong, because the study only sampled undergraduates.` — with context `Remote work makes people less productive.` | **approved**, ~100% sure |
| `lol` | **pen** — genuinely unsure whether a two-word reaction is noise |

**Use the "replying to…" field.** It is not decoration: `on_topic` is scored
against whatever the comment answers, and with nothing there it drags confidence
down for reasons unrelated to what you typed.

**One to be ready for:** `Anyone who still believes this is beyond help. Not
worth explaining again.` scores `contempt` around 9.2 out of 10 and *still goes
to the Pen*, because the gate is only ~79% sure. `contempt` is the weakest axis
in the rubric — it has a confidence floor near 0.66 that three rewrites could
not move. High score, low confidence, routed to a human. Working as designed,
and worth explaining rather than reloading until it bounces.

### 3.3 Live rubric editing — ~$0.03 per click, 2/min

Open the **rubric** drawer in the control strip, expand an axis, change one
level, press **Re-judge**.

**Expect:** `re-judged ~630 comments in ~3s for ~$0.03 — N changed lane`, then a
per-axis table of mean confidence before and after, on the same comments.

**Read it honestly.** It shows *every* axis, not just the one you edited,
because a rubric change is a trade. During the build, an edit that removed a
documented defect — a level containing "X, or Y", exactly what the rules say not
to write — made that axis's confidence *worse*. Your edit may well too.

The claim this supports is not "watch it improve". It is: a sentence, three
seconds, three cents, no labelling run and no retrain.

### Stop it

Ctrl-C, then **run the orphan check** — this one was spending money.

---

## Quick reference

| check | key? | cost | proves |
|---|---|---|---|
| `uv run pytest -q` | no | — | 128 tests, no regressions |
| `uv run python -m calibrate.sweep` | no | — | confidence predicts accuracy |
| `--offline` wall | no | — | lanes, Pen, blur, decisions, DOM cap |
| threshold slider | no | — | retuning is free |
| `/calibration` | no | — | the curve, with its caveat |
| `judge.pipeline --seconds 10` | **yes** | ~$0.08 | throughput on your network |
| test-your-own-comment | **yes** | ~$0.00003 | the shareable artifact |
| rubric re-judge | **yes** | ~$0.03 | a sentence beats a retrain |

## If something looks wrong

```bash
curl -s http://127.0.0.1:5001/health
```

A blank wall has at least four causes and `/health` distinguishes them:

- `render_task: not started` → the app did not finish starting
- `sockets: 0` → no browser attached, so nothing is being judged (by design)
- `judged: 0` with `in_queue` climbing → the workers are stuck
- `errored` climbing with `last_error` set → read the error; `402` is credits,
  `429` is rate limiting, and both are expected shapes rather than bugs

`FINDINGS.md` has the measurement behind every number quoted here.
