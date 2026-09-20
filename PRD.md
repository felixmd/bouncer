# The Bouncer — Product Requirements

**Status:** draft for a weekend build, revised 2026-09-19 after the first round of measurement
**Working name:** The Bouncer (alt: Velvet Rope). Not "ToxicGate" — the `-gate` suffix reads as scandal, not doorway.

> The product logic below survived contact with the API essentially intact — the
> two-axis model in §5 is right, and calibrated confidence does behave the way
> the demo needs. Two things changed: §5.2 gained a hard constraint on how
> rubrics must be written, and §6's numeric targets need re-reading against
> `FINDINGS.md`.

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

### 5.2.1 How the rubric levels must be written

Measured, and it is the highest-leverage decision in the build. See
`FINDINGS.md` §3.

Jev's confidence on a Score *is* the concentration of its probability across the
levels. If two levels could both describe the same comment, probability splits
between them, confidence falls, and the comment goes to the Pen — not because
the model is uncertain about the comment, but because the rubric is ambiguous
about the question. **A badly written rubric looks exactly like a hard corpus.**

Three rules, from the docs and confirmed here:

1. **Describe situations, not degrees.** "The comment calls a person a name,
   mocks them, or says what kind of person they are" beats "openly critical,
   but not abusive." The first names a thing that either happened or did not.
2. **One situation per level.** The first version of the contempt axis had a
   level reading "treats disagreement as legitimate, *or* does not engage with
   opposing views at all" — two unrelated situations, and contempt was the
   least confident axis in the first run.
3. **One thing per question.** An axis asking about target *and* intensity *and*
   manner at once splits probability three ways.

Rewriting the rubric to these rules raised hostility recall from 0.43 to 0.57.

This also sharpens the live-rubric-editing feature in §4.2: what a viewer is
editing is not a vibe, it is the thing that determines whether the model can
answer confidently at all. That is a better story than "type a sentence and the
model changes its mind," and it is true.

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
2. Throughput shown is ≥ 200 items/sec sustained. — **clears comfortably.** 20 req/s × B=15 = 300/sec, and batching costs no accuracy up to B=30.
3. Spend counter stays visibly trivial (cents) against a five-figure volume counter. — **clears as written, but read §6.2.** $0.034 per 1,000 comments. The same rate is **$0.41 per minute** of continuous streaming, which is the number that actually governs running the thing.
4. The Pen contains genuinely ambiguous comments — a viewer who clicks in should hesitate before choosing Allow or Bounce. If the Pen is full of obvious cases, the threshold is wrong.
5. Editing the rubric visibly re-sorts the backlog in under three seconds.
6. At least one person asks to try their own comment.

### 6.1 The Pen-size target needs stating honestly

The framing in §2 — "we auto-handle 94% and escalate 6%" — is a claim about a
number nobody has measured on this workload, and the first measurement did not
support it. On Civil Comments the auto-handled share at a defensible floor is
**22%**, not 94%.

Two things about that, in order of importance.

**It is measured on the wrong data, and that is fixable.** Civil Comments has no
thread structure, so the `on_topic` axis is being asked what a comment is
replying to with nothing in the state to reply to. Its confidence there is 0.50
against 0.86 on Reddit data with real context. `on_topic` is frequently the axis
that pens a comment, so the number should improve once the curve is measured on
a fetched thread. How much is unknown.

**The demo should not hard-code a percentage it cannot hit.** The honest version
is stronger anyway: put the auto-handled share on screen as a live number next
to the threshold slider, and let a viewer move the threshold and watch the
trade. "Here is the curve, pick your operating point" is a better trust-and-
safety conversation than "we do 94%", and it turns criterion 4 from an assertion
into something the room can check.

If the number after step 2 of the build order is still low, that is the finding,
and the Pen being large is only embarrassing if the demo claimed otherwise.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Wall goes all green — Reddit's popular threads are friendly | Sort by `controversial`; pick structurally contentious subs; pre-bake and eyeball the pile before showing it |
| Accuracy objection | The Pen, plus the calibration curve tab |
| "A small BERT classifier would do this" | Live rubric editing — no retrain, no labelling run |
| Offensive content on a screen in front of colleagues | Bounced lane blurred by default, click to reveal |
| Publishing real users' comments under a "toxic" label | Hash usernames at fetch time; never store or display real handles |
| Reddit unreachable at showtime | Pre-baked JSON; Reddit is a dev-time dependency only |
| Throughput counter disappointing on the day | ~~Measure fan-out latency early~~ — **retired.** p50 169ms from this laptop, 300 items/sec at B=15 |
| Pen so large there is no Approved lane | **Live and unresolved.** `min()` over four axes penned 99% of traffic; the `decisive` gate fixes the arithmetic, but the auto-handled share is still only 22% on context-free data. Re-measure on a real thread before the demo |
| Rubric ambiguity mistaken for model uncertainty | A level that overlaps another splits the probability and lowers confidence, which looks identical to a hard comment. Write levels as disjoint situations (§5.2.1) and watch per-axis confidence, not just the lane mix |
| Same comment gets a different verdict on a re-run | Batching flips 6–7% of lane decisions against a near-deterministic baseline. Harmless while the stream is one-way; becomes visible the moment live rubric editing re-sorts a backlog the audience has already read |

## 8. Out of scope for v1

- Any second model (including for summarisation — thread context comes from Reddit's own fields)
- Live Reddit fetching at runtime
- Persistence, accounts, multi-user state
- Twitch / other feeds (the feed adapter should be pluggable, but only Reddit ships)
- Mobile layout
