# Running the demo

A pre-flight list and the two or three things that will actually go wrong.

---

## Decide which mode first

```bash
uv run python -m web.app --offline            # recordings: no key, no spend, no network
uv run --env-file .env python -m web.app      # live: the model, ~$0.41/min at 205/s
```

**Default to `--offline`.** It replays 350 real verdicts, and the lane policy,
the gate, the threshold sliders and Allow/Bounce are all the real ones — they
are a pure recompute over stored probabilities. The wall is indistinguishable
except that the spend counter reads `$0.0000`.

Go live when someone needs to watch the model actually decide — the two features
that need it are rubric editing and test-your-own-comment, and both say so in
offline mode rather than returning stale numbers.

If the wall is going on a screen for a whole day, **offline is not a fallback,
it is the correct choice**: live costs about $200 for eight hours (`FINDINGS.md`
§19).

## Pre-flight

**1. Stop the machine sleeping.** Currently it sleeps after 10 minutes on mains
and 4 on battery, which will interrupt a demo. Not changed for you — it is a
system-wide setting:

```bash
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

Put them back afterwards with a sensible number of minutes in place of `0`.

**2. Check it comes up.**

```bash
curl -s http://127.0.0.1:5001/health
```

`render_task: running`, `sockets: 1` once a browser is attached, and
`api_errors: 0`. If the wall is blank, `/health` tells you whether nothing is
being judged or nothing is being sent — that distinction has four causes and
guessing between them is slow.

**3. If going live, check the credits.** The account has run out twice. A wall
where every card reads "could not judge" is not a demo of anything; the app
degrades correctly but there is nothing to show.

**4. Kill orphans.** Closing a terminal or stopping a run from an editor does
not always kill the `uv run` child, and an orphaned live server keeps judging at
full rate for nobody. This is what drained the credits both times.

```bash
lsof -i :5001 && pkill -f "python -m web.app"      # macOS / Linux
```

```powershell
# Windows
Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

The app only judges while a browser is connected, so an orphan is now harmless —
but check anyway, and clear it before the room arrives rather than during.

If Windows answers **Access is denied**, the server belongs to an IDE or agent
sandbox and needs an elevated PowerShell; `TESTING.md` has the fallbacks. Do
that the night before, not five minutes out.

## Exposing it to a room

```bash
uv run python -m web.app --offline --public
```

`--public` clamps the drip ceiling to 60/s. Use it with `--offline` if you
possibly can: then the page is free to hit and there is nothing to abuse.

If you must expose the live app, know what is reachable:

| route | per click | limited |
|---|---|---|
| `/tune` | free, **but sets the burn rate** | drip clamped to 60/s under `--public` |
| `/rubric` | ~$0.027 (a 800-comment re-judge) | 2/min per IP, 30 total |
| `/test` | ~$0.000034 | 6/min per IP, 400 total |
| `/decide`, `/calibration` | free | — |

Spec §9 warned about `/test`, which turned out to be the cheapest thing on the
list. The real exposure is the drip slider: free to move, and it decides how
fast money leaves.

## What to say when someone pushes back

**"Is it accurate?"** Open `/calibration`. Agreement with the human label rises
monotonically from 0.807 to 0.950 as the floor rises, while the auto-handled
share falls from 100% to 47%. The lines cross; the crossing point is the trade.
The claim is not "this model is right", it is "it knows when it isn't" — and
that is a curve, not an assertion.

**"Why not train a classifier?"** Open the rubric drawer, edit one level, press
re-judge. 630 comments re-scored in under three seconds for three cents. Then
read the report honestly: it shows every axis before and after, and the edit may
well have made things *worse*. That is what tuning looks like, and no retraining
pipeline gives you the answer in three seconds either way.

**"The red lane is empty."** It is 3–4%, and that is a property of real
discussion data rather than a bug. Unambiguous abuse is rare even in a corpus
selected for conversations that derail; `hostility` tops out at 7.4 out of 10
across 200 ChangeMyView comments. Most of what a moderator actually faces is
borderline, and borderline is what the Pen is for.

**"That comment is obviously contemptuous and it went to the Pen."** Correct,
and worth being ready for. `contempt` is the weakest axis in the rubric — it has
a confidence floor around 0.66 that three separate rewrites could not move — so
a textbook example can score 9.2 out of 10 and still be gated. High score, low
confidence, routed to a human. The mechanism is working; the axis is the limit.

## Known rough edges

- **The stream has a visible seam** when it rolls over to the next thread.
  Batching is per-thread, so the reader emits one thread at a time.
- **Approved and Bounced are sampled** to 8 cards a frame. The counters carry
  the true totals; the wall is a visual. The Pen is never sampled.
- **Nothing persists.** Allow/Bounce is a visual state change and a counter;
  reload and the Pen refills. That is deliberate (PRD §8).
- **`on_topic` is the weakest axis on deep threads.** Its confidence is ~0.45
  against 0.75 for `substance`, and the Hacker News thread runs fourteen levels
  deep. Depth is untested as a variable.

## Measured, from this machine and network

45 seconds, live, drip 250/s:

```
9,552 judged in 45.0s = 212.3/sec      approved 81%  bounced 3%  pen 15%
639 requests, 6 errors (0.9%)          latency p50 275ms, p95 1,766ms
875 tokens/comment                     185,797 tokens/sec — 74% of the ceiling
$0.3512                                14.2 req/sec of the 20/sec budget
```

Clears the PRD's 200/sec target. The errors are 429s at three-quarters of the
published token ceiling, which is the expected shape (`FINDINGS.md` §16).
