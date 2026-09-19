# The Bouncer — Product Requirements

**Status:** draft for a weekend build
**Working name:** The Bouncer (alt: Velvet Rope). Not "ToxicGate" — the `-gate` suffix reads as scandal, not doorway.

---

## 1. What it is

A live comment-moderation demo. Reddit comments stream toward a gate; Jev judges each one in flight; they land in one of three lanes — Approved, Bounced, or **the Pen**, a holding area for comments Jev judged with low confidence, where a human decides.

One screen, always moving, watchable from across a room.

## 2. Why this demo and not another

The demo exists to make three properties of Jev visceral to someone who has not read the docs:

| Property | How the UI shows it |
|---|---|
| Throughput | A counter ticking through hundreds of items per second while cards flow |
| Cost per decision | A spend counter that stays absurdly low next to the volume counter |
| **Calibrated confidence** | The Pen — the model visibly routing its own uncertainty to a human |

The Pen is the differentiator. Throughput and cost are impressive but arguable; a fast small LLM gets close. Confidence-gated escalation is the thing Jev's architecture gives you that a generative model does not, and it is also a real product shape that a trust-and-safety buyer recognises immediately.

### The accuracy objection, and the answer

Jev is not always more accurate than a small LLM. On at least one public benchmark (phishing classification) it is materially worse than Claude Haiku. Someone in the room will raise this.

The Pen is the answer, and the demo should be built so that the answer is already on screen. The claim is not "this model is right." The claim is **"this model knows when it isn't, and escalates."** Design every decision below to reinforce that framing rather than fight it.

## 3. Audience

Technical-adjacent viewers who are not necessarily engineers — the kind of person who would nod at "we auto-handle 94% and escalate 6%" but would not read a benchmark table. Must be legible in about eight seconds with no narration.

## 4. Core experience

### 4.1 Ambient state (default)

App loads with a pre-baked Reddit thread already streaming. No click required. Comments flow left to right, get their four-axis fingerprint stamped at the gate, and drop into lanes. Counters climb. This runs indefinitely and loops.

### 4.2 The three interaction hooks

**The Pen (highest priority).** Penned comments accumulate in the amber lane with Allow / Bounce buttons. Anyone can click. Penned cards are visually heavier than the others — this is where attention should go.

**Live rubric editing.** A text box holds the plain-English definition of each axis. Editing it re-judges the entire already-processed backlog in a couple of seconds. This is the answer to "why not just train a classifier" — a trained model needs a labelling run and a retrain; this needs a sentence.

**Test your own comment.** A single input feeding the same gate, returning the same fingerprint card. This is the shareable artifact — people screenshot their own comment getting bounced.

### 4.3 Secondary screen: the calibration curve

A second tab running the Jigsaw / Civil Comments labelled dataset, plotting Jev's returned confidence against whether it matched the human label. This is what turns "we have a nice amber lane" into "here is the curve; at this threshold we auto-handle 94% and agree with the human label 97% of the time on what we keep."

Keep this dataset entirely separate from the Reddit path. Reddit gives spectacle and thread context; Jigsaw gives labels. One dataset cannot serve both jobs without compromising each.

## 5. The classification model

### 5.1 Two axes, not one list

This is the most important piece of product logic and the easiest thing to get wrong.

Content classification and confidence are **orthogonal**. Confidence is never an option the model picks — Jev returns it alongside every answer. Putting "not sure" into a Choice corrupts exactly the thing being demonstrated.

|  | Confident | Low confidence |
|---|---|---|
| **Over the line** | Bounced (red) | **The Pen** (amber) |
| **Fine** | Approved (green) | **The Pen** (amber) |

Amber does not mean "mildly toxic." It means "Jev doesn't know, so a human decides."

### 5.2 Four axes, scored not bucketed

Per comment, ask four **Score** questions in a single call (Jev fans questions out in parallel, so the marginal cost of the fourth question is close to zero):

| Axis | Question | Needs thread context? |
|---|---|---|
| `hostility` | Is this attacking a person? | No |
| `contempt` | Does it dismiss anyone who disagrees as not worth answering? | No |
| `substance` | Does it add anything to the discussion? | Yes |
| `on_topic` | Does it engage with what it is replying to? | Yes |

**Score, not Noul.** Nouls pile up at the extremes; Scores spread across a distribution, which both looks better as a bar fingerprint and gives a usable threshold surface.

**Contempt, not bias.** On Reddit, one-sided personal opinion is the default state, not a violation. A "biased" bucket would swallow 80% of comments and carry no information. Contempt isolates the actionable half — having a strong opinion is fine; treating the other side as beneath response is the signal.

**Not mutually exclusive.** A comment can be hostile *and* unhelpful. "Healthy" is the absence of the others, not a peer category. Lanes are computed in Python from the four scores plus confidence, never chosen by the model.

### 5.3 Explicit non-goals

Jev is a non-generative decision model trained on synthetic data for calibrated judgments. It is not a knowledge store. **Never ask it a question whose answer requires a fact not present in the state you passed it.**

Out of scope, permanently:

- Truth or fact-checking of any kind
- Citation-needed detection (does not work on Reddit — most comments are personal opinion, and verifying a source needs external knowledge)
- Misinformation or scam detection (requires currency: "is this project a known rug?")
- Anything involving arithmetic, dates, or counting — compute those in Python and pass the result as prose

Every question in the demo must be intrinsic to the text in the state.

## 6. Success criteria

The demo works if:

1. It is legible in eight seconds with no explanation.
2. Throughput shown is ≥ 200 items/sec sustained.
3. Spend counter stays visibly trivial (cents) against a five-figure volume counter.
4. The Pen contains genuinely ambiguous comments — a viewer who clicks in should hesitate before choosing Allow or Bounce. If the Pen is full of obvious cases, the threshold is wrong.
5. Editing the rubric visibly re-sorts the backlog in under three seconds.
6. At least one person asks to try their own comment.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Wall goes all green — Reddit's popular threads are friendly | Sort by `controversial`; pick structurally contentious subs; pre-bake and eyeball the pile before showing it |
| Accuracy objection | The Pen, plus the calibration curve tab |
| "A small BERT classifier would do this" | Live rubric editing — no retrain, no labelling run |
| Offensive content on a screen in front of colleagues | Bounced lane blurred by default, click to reveal |
| Publishing real users' comments under a "toxic" label | Hash usernames at fetch time; never store or display real handles |
| Reddit unreachable at showtime | Pre-baked JSON; Reddit is a dev-time dependency only |
| Throughput counter disappointing on the day | Measure fan-out latency from the actual demo machine and network early |

## 8. Out of scope for v1

- Any second model (including for summarisation — thread context comes from Reddit's own fields)
- Live Reddit fetching at runtime
- Persistence, accounts, multi-user state
- Twitch / other feeds (the feed adapter should be pluggable, but only Reddit ships)
- Mobile layout
