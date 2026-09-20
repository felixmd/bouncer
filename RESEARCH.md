# Jev for comment moderation — a capability report

**What this is.** A findings report from building *The Bouncer*, a working
comment-moderation system on TypeSafe's `jev-1.13.0`, over roughly two days.
The object was never the demo. It was to find out how far a non-generative
decision model carries a real, messy, production-shaped problem, and to write
down what it cannot do as carefully as what it can.

**Status of the numbers.** Everything quoted here was measured on this project
against `jev-1.13.0`, from a laptop, between 2026-09-19 and 2026-09-20.
Section references like *(§13)* point at [`FINDINGS.md`](FINDINGS.md), which
holds the raw experiment write-ups. Anything not measured is labelled as such,
and §12 lists what is still open. One analysis in §5 below is new to this
report and **supersedes a claim made elsewhere in the repository**; it is
flagged where it appears.

---

## 1. Summary, and the recommendation

Jev did the job. A three-lane moderation wall runs end to end at 212–225
comments/sec with an error rate under 1%, judging real Hacker News and
r/changemyview threads against four plain-English rubrics, with no training
data, no labelling run and no fine-tuning. Every experiment in this report cost
well under a dollar in total; the account still ran out of credits twice, for
reasons that turn out to be the most transferable cost lesson here (§8).

The interesting results are the qualifications.

**What is genuinely good:**

- **Zero-shot discrimination of AUC 0.864** against human toxicity labels, from
  four sentences per axis written by hand. No labels were used to build it.
- **The confidence number is real.** Higher confidence does mean higher
  accuracy — the lift over a trivial baseline is positive at every operating
  point, and the model is systematically least sure about exactly the comments
  nearest the decision boundary (§14), which is what correct calibration looks
  like.
- **Policy is prose, and changing it is instant.** A rubric edit re-judged 776
  comments in 3.2 seconds for 3.2 cents (§23). There is no analogue of this in
  a trained-classifier workflow.
- **Retuning thresholds is free** — every verdict carries its full per-level
  probability distribution, so changing the operating point is a pure recompute
  with zero model calls (§21).
- **The failure mode is benign.** When the account ran out of credits mid-run,
  4,050 unjudgeable comments routed to the human queue and the system stayed up
  (§18).

**What is not:**

- **The calibration curve overstates the case, and this report corrects it
  (§5).** Agreement on auto-handled traffic rises 0.807 → 0.950 as the
  confidence floor rises — but the majority-class baseline on the same subsets
  rises 0.700 → 0.943 alongside it. The real lift collapses from +0.107 to
  +0.007. Balanced accuracy *peaks* at floor 0.65 and declines thereafter.
- **At the shipped operating point the system automates the easy half.**
  Specificity 0.989, recall 0.294. It auto-approves benign comments very
  reliably and hands a human 71% of the actual violations.
- **One of the four axes has a hard floor.** `contempt` sits near 0.66
  confidence and three separate rubric rewrites could not move it (§11, §23).
- **Output is a number, never a reason.** A moderator sees "unsure: contempt
  0.42" and nothing else. For a moderation product that is a real gap.
- **No baseline was run.** Not against a generative LLM, not against a trained
  classifier. Every comparison in §9 is therefore structural, not empirical,
  and that is the single biggest weakness in this report.

### Recommendation

**Adopt Jev for confidence-gated triage where labels do not exist and policy
changes often. Do not adopt it as a general-purpose accuracy play, and do not
adopt it at high volume without a cost model.**

Concretely:

| verdict | scope |
|---|---|
| ✅ **Use it** | A new policy with no labelled data, where the product is "automate the clear cases at high precision and route the rest to people", at volumes below roughly 10M items/month |
| ✅ **Use it** | Policy that changes faster than a retraining cycle — new community rules, evolving abuse patterns, per-customer moderation standards |
| ⚠️ **Measure first** | Anywhere a trained classifier already exists and performs. Jev's 0.864 AUC is excellent for zero labels and is probably not competitive with a fine-tuned model on a stable task |
| ❌ **Do not use it** | Where moderators or users need a stated reason, where recall matters more than precision, or where per-item cost at scale is the binding constraint |

The rest of this document is the evidence.

---

## 2. What Jev is, as observed

Jev is a non-generative decision model. You send it a **state** (free text) and
a set of **questions**, and it returns structured answers with calibrated
probabilities. It does not generate prose, cannot be asked to explain itself,
and has no knowledge beyond what the state contains.

Three primitives: **Choice** (pick one of ≤255 options), **Score** (place the
item on a 2–10 level rubric), and **Noul** (a 0–1 probability). We used Score
for all four axes. Noul was rejected early on documented advice and our own
observation that Nouls pile up at the extremes, while Scores spread into a
distribution you can threshold.

Four properties dominated every design decision we made:

**1. Questions fan out in parallel against one shared state.** The fourth
question costs tokens but almost no wall-clock time. We confirmed 120 questions
in a single request all returning. This is the architectural gift — it is why
four axes cost roughly what one axis costs in latency, and it is why a rubric
contest can put every variant of every axis in one request and get perfect
pairing for free (§11).

**2. Every answer carries confidence, and you get the full distribution.**
`ScoreAnswer` exposes `.score` (a probability-weighted mean), `.confidence`
(concentration of probability across levels) and `.probabilities` (the per-level
dict). That third field is the one that matters, and it took us until §13 to
realise it. Having the distribution rather than a scalar means the entire
operating point — threshold, gate, floor — is recomputable after the fact
without calling the model again.

**3. Confidence is not a category and must never be one.** It is returned
alongside the answer, not chosen as part of it. Putting "not sure" into a Choice
would destroy exactly the signal you are buying.

**4. It is literal.** It answers the question you wrote, not the one you meant.
This is the source of most of our self-inflicted wounds.

### The documented jagged edges, and which ones bit

TypeSafe publishes a jaggedness page for the model. Four items on it were
directly relevant, and three of them cost us real time:

| documented weakness | did it bite? |
|---|---|
| "Cannot reliably count items" | **Yes, pre-emptively.** It is why we address comments by quoting them into their own question rather than by index (§2) |
| "Accuracy falls as the state grows with content unrelated to the decision" | **Yes, measurably.** Clearing 14 irrelevant comments out of the state was worth +0.140 confidence on `on_topic` (§2) |
| "Score levels are weak in numerical calibration" | **No.** We tested it and it did not bite at our threshold — thresholding the mean at 6.0 versus `P(level≥3)>0.5` disagreed on 5 of 300 comments (§13) |
| Literal interpretation | **Yes, repeatedly.** §9 is an entire section about a question with two competing referents in it |

The third row is worth dwelling on: a documented warning turned out not to
apply to our case, and we only knew because we measured it. Taking the
jaggedness page as gospel would have cost us a design change for no benefit.

---

## 3. What we built, and why it is a fair test

A live wall. Pre-baked comments stream toward a gate; each is scored on four
axes in one batched request; a pure Python function assigns a lane from the
scores *plus* the confidence; results render over one WebSocket at a fixed 12Hz
tick.

**The four axes**, each a Score with five levels:

| axis | question | needs thread context |
|---|---|---|
| `hostility` | is this attacking a person? | no |
| `contempt` | how is a differing view treated? | no |
| `substance` | does it add anything? | yes |
| `on_topic` | does it engage with what it replies to? | yes |

**Three lanes**, from two orthogonal axes — severity and certainty:

|  | confident | low confidence |
|---|---|---|
| over the line | Bounced | **the Pen** |
| fine | Approved | **the Pen** |

**The corpus**: 7,456 real comments across four threads — three Hacker News
(CrowdStrike outage, a DMCA takedown, an AI-comments policy row) and 866
r/changemyview comments from ConvoKit, specifically the subset curated for
conversations that turned hostile. Usernames hashed at fetch time, mentions
scrubbed.

**Why this is a fair test rather than a friendly one.** Three reasons. The data
is real and was not filtered for spectacle — we explicitly refused to mix in
harsher labelled data to make the red lane look busier (§15). Two of the four
axes require thread context, which is the hard case rather than the easy one.
And the accuracy evaluation uses a third-party labelled corpus (300 Civil
Comments, 30% toxic) that the rubric was never tuned against.

**Where it is not a fair test.** The accuracy corpus has no thread structure, so
it can only score two of our four axes, and it scores them against a human label
("toxicity") that does not exactly match what our rubric asks. Some of the recall
shortfall in §5 is definition mismatch rather than model error, and we did not
separate the two.

---

## 4. Method, and the mistakes in it

Worth reading before the results, because two of our early conclusions were
wrong and the reasons generalise.

**We compare arms pairwise, not by aggregate rates.** All arms score the same
corpus in the same order, so per-comment comparison is always available. We
reported a rubric rewrite as a win for about an hour on the strength of lane
agreement moving 0.793 → 0.800 — which is two comments out of 300 (§8). Rates
that close are noise wearing a direction.

**Every accuracy sweep carries a noise-floor control.** Two identical runs of
the same configuration differ by 0.081/10 in mean score and agree on 99.3% of
lanes. The model is very nearly deterministic, which means a drift that looks
small may not be noise at all. Without that control the batch-size experiment
would have been unreadable.

**Variants go inside one request.** Because questions are evaluated
independently against a shared state, `hostility__v1` and `hostility__v3` are
just two questions about the same comment. This gives perfect pairing and
eliminates the arm-order confound that our earlier sequential design carried.
Any future rubric contest should be run this way; there is no reason not to.

**Two diagnoses we got wrong, both instructive:**

- We predicted `on_topic` confidence would *recover* once the data had real
  thread structure, since Civil Comments starved it of a referent. It went the
  other way — 0.500 → 0.354. The cause was not the data but the question: the
  levels named "the thread's subject" while the question supplied a parent
  comment. Two referents, one question. Adding context to an ambiguous question
  made it worse (§9).
- We predicted that switching the confidence gate to measure decision
  probability would buy accuracy. It bought none — the two gates rank comments
  at correlation 0.845 and agree with human labels within ±0.03 at matched
  volume. What it bought was interpretability (§13).

Both were reasonable hypotheses, and in both cases the write-up now leads with
the correction. That pattern — predict, measure, be wrong in public — is most
of what this report is made of.

---

## 5. Calibration: the central claim, corrected

This is the most important section, and it revises the project's headline.

### What was claimed

Sweeping the confidence floor and measuring agreement with human labels on the
comments that clear it produces a clean monotonic curve:

| confidence floor | auto-handled | agreement on those |
|---|---|---|
| 0.50 | 100% | 0.807 |
| 0.65 | 89% | 0.850 |
| 0.75 | 80% | 0.866 |
| **0.85** *(shipped)* | **70%** | **0.876** |
| 0.95 | 57% | 0.924 |
| 0.99 | 47% | 0.950 |

Read straight, that says: *the model's confidence predicts its accuracy, so a
threshold buys agreement in exchange for volume.* That is what
[`FINDINGS.md`](FINDINGS.md) §24 concluded, it is what the app's calibration tab
renders, and it is what the README presented as the proof.

### What is wrong with it

Raising the floor does not just remove comments the model is unsure about. It
removes **toxic** comments. The auto-handled subset goes from 30% toxic at floor
0.50 to 5.7% toxic at 0.99 — and a subset that is 94% one class is trivially
easy. Agreement rises whether or not the model got any better.

The correct comparison is against what "approve everything" would score **on
that same subset**. That is an implementable strategy, not an oracle, because
the benign class is the majority at every floor. Run
`uv run python -m calibrate.baseline`:

| floor | auto | toxic in subset | Jev agreement | always-approve | **lift** | balanced acc | F1 | MCC |
|---|---|---|---|---|---|---|---|---|
| 0.50 | 100% | 30.0% | 0.807 | 0.700 | **+0.107** | 0.729 | 0.623 | 0.511 |
| 0.60 | 93% | 27.6% | 0.835 | 0.724 | **+0.111** | 0.737 | 0.635 | 0.558 |
| **0.65** | 89% | 25.8% | 0.850 | 0.742 | **+0.109** | **0.748** | **0.649** | **0.580** |
| 0.75 | 80% | 20.5% | 0.866 | 0.795 | +0.071 | 0.711 | 0.579 | 0.539 |
| **0.85** *(shipped)* | 70% | 16.3% | 0.876 | 0.837 | **+0.038** | 0.641 | 0.435 | 0.448 |
| 0.95 | 57% | 9.3% | 0.924 | 0.907 | +0.017 | 0.594 | 0.316 | 0.416 |
| 0.99 | 47% | 5.7% | 0.950 | 0.943 | **+0.007** | 0.562 | 0.222 | 0.345 |

Three readings, in order of importance:

1. **The lift over a trivial baseline collapses as the floor rises**, from
   +0.107 to +0.007. At the strictest setting, where the agreement number looks
   most impressive at 0.950, Jev is beating "approve everything" by seven
   thousandths.
2. **On base-rate-immune metrics the system gets worse above floor 0.65.**
   Balanced accuracy peaks at 0.748 and falls to 0.562; F1 peaks at 0.649 and
   falls to 0.222; MCC peaks at 0.580 and falls to 0.345. The confidence gate is
   buying class balance, not skill.
3. **The signal is nonetheless real.** Lift is positive at every floor, MCC at
   the low end is 0.511, and balanced accuracy does genuinely improve from 0.729
   to 0.748 between floors 0.50 and 0.65. Confidence carries information. It
   just carries perhaps a third of what the agreement curve implies.

### The honest version of the claim

> Confidence-gating buys **precision on automated actions**. Above a floor of
> roughly 0.65 it stops buying accuracy and starts buying class balance.

That is a narrower claim and it is still a good one — see §6 for why it is
arguably the right product shape anyway. But "agreement rises from 0.807 to
0.950 as the floor rises" should not be presented as evidence of calibration
without the baseline column beside it.

**This supersedes FINDINGS §24 and the proof section of the README, both of
which quote the uncorrected curve.** The measurement is reproducible from
`calibrate/baseline.py`.

### The threshold-free number

The cleanest single measure of what Jev can do on this task, immune to both base
rate and operating point, is the ranking AUC over all 300 labelled comments:

| ranking signal | AUC |
|---|---|
| max P(level≥3) over `hostility` and `contempt` | **0.864** |
| `hostility` alone | 0.842 |
| `contempt` alone | 0.834 |
| max raw score | 0.848 |

**0.864 AUC, zero-shot, from eight sentences of hand-written rubric and no
training data at all.** That is the number to quote, in both directions: it is
genuinely strong for a system that saw no labels, and it is the number a trained
classifier should be expected to beat.

---

## 6. What the system actually automates

The operating point matters more than the aggregate, and it is not what the
lane percentages suggest.

At the shipped floor of 0.85, on labelled data:

| | |
|---|---|
| specificity (TNR) | **0.989** |
| recall (TPR) | **0.294** |
| precision | 0.833 |
| auto-handled share | 70% |

The system is **extremely good at confidently approving benign comments** and
routes most actual violations to a human. At floor 0.90 and above, precision on
automated bounces reaches 1.000 while recall falls to 0.226 and below.

Whether that is a flaw depends entirely on the product.

**It is the correct shape for a moderation triage queue.** Wrongly bouncing a
legitimate comment is a visible, appealable harm; wrongly escalating a hostile
comment costs a moderator thirty seconds. A system that auto-approves at 99%
specificity, auto-bounces at 83–100% precision, and escalates everything near
the line is defensible and is what a trust-and-safety team would design if you
gave them the choice.

**It is the wrong shape if the pitch is "we automate your moderation".** At the
shipped setting, 71% of the toxicity in the stream reaches a human. The
automation is doing the easy work. Any claim about headcount reduction has to be
computed on that basis, and the honest framing is *"we remove the 70% of your
queue that is obviously fine"*, not *"we catch the bad stuff"*.

**Recall is partly a choice, not a ceiling.** Sweeping the severity threshold
instead of the confidence floor:

| decision rule | precision | recall | F1 |
|---|---|---|---|
| P(over) > 0.2 | 0.681 | 0.711 | **0.696** |
| P(over) > 0.3 | 0.714 | 0.667 | 0.690 |
| P(over) > 0.5 *(shipped)* | 0.750 | 0.533 | 0.623 |
| P(over) > 0.7 | 0.787 | 0.411 | 0.540 |

F1 peaks at a much more permissive threshold than the one we ship. The shipped
point is precision-leaning by choice, and a product that needed recall could
move along this curve at no cost — the probabilities are already stored (§21).

---

## 7. Where Jev struggled

### The rubric is the model, and that cuts both ways

This is the single largest finding in the project.

Confidence on a Score *is* the concentration of probability across levels. If
two levels could both describe the same comment, probability splits between
them, confidence falls, and the comment escalates — **not because the model is
uncertain about the comment, but because the rubric is ambiguous about the
question.** A badly written rubric is indistinguishable from a hard corpus from
the outside.

The measured consequences:

- Rewriting `hostility`'s levels moved its confidence by **0.114** — more than
  any pipeline change we made (§11).
- Rubric quality *gates* whether anything else you do matters. Under a poor
  `on_topic` rubric, cleaning the state was worth +0.004 (ns). Under a good one,
  the same change was worth +0.140 (§2). Fix wording before architecture.
- The documented rule "describe situations, not degrees" **over-generalises**.
  Our winning `hostility` levels are a pure degree ladder and they beat two
  careful situational rewrites by 0.114 and 0.099. What actually predicts
  confidence is whether the levels form *one unambiguous ordering*; situational
  phrasing usually produces that, which is why the rule works most of the time
  (§11).

The upside is that you can fix your model by editing prose. The downside is
that **your model's quality now depends on writing you cannot validate without
running it**, and the failure is silent — it shows up as a busy escalation
queue, not an error.

### One axis has a floor that rubric work cannot move

`contempt` sits at 0.640–0.682 confidence across three genuinely different
phrasings (§11). Two of the three were written specifically to fix it. A fourth
attempt during live editing — removing a documented "X, or Y" defect, exactly
the change the project's own rules prescribe — made it **worse** by 0.017
(§23).

That is three independent confirmations. The most likely reading is that
contempt is simply a harder judgement than the others and ~0.66 is close to what
this model can do on it. It is also the axis most visible to anyone probing the
system: a textbook contempt comment scores 9.2/10 and still escalates, because
the gate is only 79% sure which side of the line it is on (§22).

**Practical implication: budget for one axis in four being stubborn, and check
per-axis confidence before touching the global threshold.** When the escalation
queue floods, the instinct is to lower the floor; the documented advice and our
own data both say the cause is usually one badly-behaved axis.

### Context helps only if the question is unambiguous

Adding real thread context to an ambiguous `on_topic` question *lowered* its
confidence from 0.500 to 0.354, with several comments returning confidence 0.00
while still producing a plausible score (§9). Rewriting every level to name a
single referent recovered it to 0.450 — still the weakest axis by a distance.

The literalism cuts deep here. Two referents in one question is not a subtle
flaw the model works around; it is a failure it reports as uncertainty.

### Batching is not free, and it is not deterministic

There is no batch-size accuracy knee — performance is flat from B=1 to B=30, and
B=30 was the best run (§1). But against a noise floor of 0.081/10, batching
moves scores by 0.54–0.71 and **flips 6–7% of lane decisions**. The effect is
lateral, not degrading — agreement with human labels is unchanged — but it is
real, roughly 8× noise.

For a streaming wall that is irrelevant. For anything that must give the same
item the same verdict twice — an appeals process, an audit, a regulator —
it is disqualifying without additional work.

### No explanations, ever

A penned card says `unsure: contempt 0.42`. That is the entire explanation
available. Jev cannot say *why*, because it does not generate.

For this demo it is fine. For a deployed moderation system it is a material
gap: moderators build trust through rationale, appeals processes need stated
reasons, and several regulatory regimes require them. The workaround —
putting a generative model next to it — costs you the latency, the cost profile
and the calibration that made Jev attractive.

---

## 8. Engineering and cost

### Throughput

Measured on the presenting laptop, 45 seconds live at drip 250/s:

```
9,552 judged in 45.0s = 212.3/sec    approved 81%  bounced 3%  pen 15%
639 requests, 6 errors (0.9%)        p50 275ms, p95 1,766ms
875 tokens/comment                   185,797 tokens/sec — 74% of ceiling
```

Three non-obvious things, each found the hard way:

- **You are token-limited, not request-limited — and both bind at once.** A
  request is ~12,000 tokens, five times what we estimated, because every
  question restates the whole rubric. The 20 req/s and 250K tokens/sec ceilings
  both bind near 300 items/sec, so **raising the batch size buys nothing** (§16).
- **More concurrency is actively harmful.** 24 workers gained 9% throughput and
  multiplied rate-limit errors fifteenfold. That matters more than it sounds: a
  failed request escalates fifteen comments by design, so the Pen went from 14%
  to 22% where the extra 8% was *failures*. **Always count errors separately
  from the escalation queue** — a queue padded with failures looks identical to
  a queue full of hard cases and is worthless (§16).
- **Token bucket capacity matters as much as its rate.** Capacity 20 at rate 20
  averaged 16 req/s and still collected 41 rate-limit errors, because a capacity
  equal to the per-second rate lets a whole second of requests leave in one
  instant. Rate 18, capacity 4 (§16).

### Cost, in both directions

| | |
|---|---|
| per comment | $0.000025 |
| per 1,000 comments | **$0.034** |
| per 10,000 comments | $0.34 |
| **per minute of continuous streaming at 205/s** | **$0.41** |
| per hour | $24.80 |
| an 8-hour day | **$198** |

Both columns are true and they tell opposite stories. The unit price is
trivially cheap and is the right number for costing a moderation queue. The
per-minute price is what governs actually running the thing, and it drained the
account's credits twice before anyone multiplied it out (§19).

**93% of every request is overhead**, and it is structural rather than sloppy:

| component | tokens/comment | share |
|---|---|---|
| rubric levels, once **per question** | 346 | **58%** |
| comment body, once **per question** | 156 | 26% |
| parent snippet, on the two context axes | 54 | 9% |
| axis question text | 37 | 6% |
| *the comment itself* | *39* | *7%* |

Each Score question is answered independently against the shared state, so each
carries its own criteria. Four axes means four copies of the rubric and four
copies of the comment. **The fan-out that makes Jev fast is what makes it
token-fat.** At 205/s that is ~121K tokens/sec against a 250K ceiling — roughly
half the maximum this API can bill. No model is cheap when held flat out.

Three levers, in order of leverage:

1. **Throughput itself.** 20/s is $2.42/hour and still reads as a stream.
   Capability does not need to be sustained to be demonstrated.
2. **Shorter rubric levels** would cut the largest slice — but level wording is
   exactly what confidence is made of (§11), so this trades directly against
   escalation quality. Not worth it unless throughput is the binding constraint.
3. **Index-address the intrinsic axes.** `quoted` addressing's measured win was
   entirely on the two context axes; `hostility` and `contempt` could take the
   comment from the shared state instead. Worth ~7%. Untested.

### The failure modes are good

Two unplanned outages, both instructive:

- **Credits exhausted mid-run.** 4,050 unjudgeable comments went to the human
  queue, each labelled "could not judge", the app stayed up and responsive, and
  the error counter tracked them separately from genuine escalations (§18).
  Degrading into "a human looks at it" is the correct failure mode for this
  class of system and it fell out of the design for free.
- **Eight orphaned server processes** judging at full rate with nobody watching,
  at roughly $3/minute in aggregate. The fix was not an optimisation but a
  control: the pipeline now only judges while a browser is attached. The 429
  storm that had looked like a rate-limit mystery was partly these servers
  competing with each other; killing them took the error rate from 49% to 2%
  with no code change (§19).

The generalisable lesson is that **the cost risk in an always-on inference
product is not the unit price, it is the absence of a demand gate.**

---

## 9. Jev versus the alternatives

**Read this section with its limitation in front of you: we did not run either
baseline.** No generative LLM was tested on this corpus, and no trained
classifier was trained or evaluated. What follows is a structural comparison
informed by measured Jev numbers on one side and published characteristics on
the other. The single highest-value next experiment is to replace this section
with data (§12).

### Versus a generative LLM classifier (Haiku-class)

| dimension | Jev | generative LLM |
|---|---|---|
| **accuracy** | AUC 0.864 measured here | unmeasured. This project's own PRD records Jev as materially worse than Claude Haiku on at least one public benchmark (phishing classification) — second-hand, unverified by us, but reason enough to assume parity at best |
| **calibrated confidence** | **native, per-level probability distribution** | not native. Logprobs are a partial substitute; self-reported confidence is not calibrated |
| **retuning after the fact** | **free** — thresholds recompute from stored probabilities | requires re-inference unless you store and post-process logprobs |
| **explanations** | **none, ever** | **yes, and this is a real advantage for moderation** |
| **output cost** | **free** — output tokens are not billed | billed, and rationales are expensive |
| **latency** | p50 275ms for 4 axes × 15 comments batched | comparable per call; more output tokens means more time |
| **structural reliability** | typed answers, no parsing | JSON-schema violations, refusals, truncation are all live failure modes |
| **iteration speed** | edit prose, re-judge in seconds | same |
| **scope** | only what the rubric asks | can discover novel categories, follow up, handle edge cases you did not anticipate |

**The honest summary:** Jev's advantages over a generative LLM are calibration,
free retuning, free output, and structural reliability. Its disadvantage is that
it cannot explain itself, cannot go beyond the rubric, and may well be less
accurate. For a *triage* layer whose output is a routing decision, the trade
favours Jev. For anything a human reads as a justification, it does not.

The free-retuning property deserves emphasis because it is easy to undersell:
having the full per-level distribution stored means the entire policy — which
threshold, which gate, which floor — is a decision you can revisit on historical
data at zero cost. With an LLM you would have to either re-run inference or have
had the foresight to persist logprobs.

### Versus a fine-tuned ML classifier (BERT/Detoxify-class)

| dimension | Jev | fine-tuned classifier |
|---|---|---|
| **accuracy on a stable task** | AUC 0.864 zero-shot | **higher — expect substantially so on a task with good labels.** Not measured here |
| **labels required** | **none** | thousands to tens of thousands |
| **time to first working system** | **hours** | weeks, dominated by labelling |
| **changing the policy** | **edit a sentence, 3 seconds, 3 cents** | re-label, retrain, re-validate, redeploy |
| **calibration** | **native** | poor by default; needs explicit calibration work |
| **per-item cost at scale** | $0.034/1,000, forever | approaching zero on owned hardware |
| **infrastructure** | an API key | training pipeline, model registry, serving, monitoring, an ML team |
| **explanations** | none | none (attention maps are not explanations) |
| **drift** | rubric is inspectable prose | silent; needs monitoring |

**This is the more interesting comparison, and the answer is about economics
rather than accuracy.**

A trained classifier will very likely beat 0.864 AUC on a task where you have
labels. But getting those labels is the whole problem: it costs weeks, it has
to be redone when the policy changes, and for a new or bespoke policy — a new
community, a new customer's standards, a newly emerging abuse pattern — the
labels do not exist and cannot be bought.

Jev's proposition is that you get a *working, calibrated* system before you have
a single label, and that policy iteration costs a sentence instead of a
retraining cycle. In a domain where the definition of the thing you are
detecting is contested and moves — which is exactly true of moderation — that
can matter more than several points of AUC.

**The crossover is cost.** At $0.034/1,000:

| monthly volume | Jev cost/month |
|---|---|
| 1M comments | $34 |
| 10M | $340 |
| 100M | $3,400 |
| 1B | $34,000 |

A distilled BERT serving 1B items/month costs far less than $34,000 in compute.
So the sensible reading is **Jev for the cold start and the long tail; a
distilled model for the hot path once the policy stabilises and the volume
justifies it** — and Jev's escalation queue is a rather good source of the
labels you would need to train that distilled model. That is a coherent
adoption path rather than a competition.

### What none of the three do

Explain themselves in a way a user can appeal against. That remains an
unsolved problem for automated moderation and Jev does not change it.

---

## 10. Where this approach fits

**Strong fit:**

- **Cold-start policy enforcement.** No labels, need something working now.
- **Policy that moves.** Community rules, evolving abuse, per-tenant standards.
  A system whose policy is inspectable prose that non-engineers can read and
  edit is a genuine organisational advantage, not just a technical one.
- **Triage where precision on automated actions matters more than recall**, and
  where a human queue exists to absorb the rest.
- **Anywhere the escalation decision is the product.** The stored-probability
  property means you can tune the automation/escalation trade continuously
  against your actual queue capacity, retrospectively and for free.

**Poor fit:**

- **Maximum accuracy on a stable, well-labelled task.** Train a model.
- **Recall-critical detection** — CSAM, credible threats, coordinated harm.
  A recall of 0.294 at the shipped point is nowhere near adequate, and moving
  along the threshold curve to recall 0.711 costs precision down to 0.681.
- **Anything needing a stated reason.**
- **Anything needing reproducibility**, until the 6–7% batching wobble is
  addressed.
- **Very high volume on a fixed policy**, where per-item cost dominates.
- **Anything requiring knowledge.** Jev is not a knowledge store: no
  fact-checking, no arithmetic, no counting, no dates. Compute those elsewhere
  and pass the result as prose.

---

## 11. Practitioner's notes

Things we would tell the next team, in priority order:

1. **Write the rubric first and measure per-axis confidence before anything
   else.** It is the highest-leverage variable by a wide margin, and a rubric
   problem is indistinguishable from a model problem from the outside.
2. **Make levels one unambiguous ordering.** Not "situations not degrees" —
   that is a heuristic for the real rule and it over-generalises.
3. **One referent per question.** Two things being compared in one question is
   the most expensive mistake available, and it presents as low confidence
   rather than as an error.
4. **Apply the confidence floor to the axes that carried the verdict**, never
   to `min()` over all of them. `min()` over four axes escalates 99% of traffic
   — that is arithmetic, not caution (§4).
5. **Gate on probability mass over the decision boundary, not on the scalar
   confidence.** They are different questions; the scalar asks "which level?"
   and the lane asks "which side?". This is not more accurate, but the number
   then means the thing you are deciding, so the threshold reads in plain
   English (§13).
6. **Make sure the gate and the rule refer to the same boundary.** Ours did not
   for a while — the rule thresholded a mean at 6.0, the gate asked about
   `P(level≥3)` which normalises to 7.5 — and nothing in the test suite caught
   it, because every test constructed probabilities consistent with its own
   scores (§14).
7. **Count errors separately from escalations, always.**
8. **Put a demand gate on inference from day one.** Nothing should call a paid
   API when nobody is waiting for the answer.
9. **Store the full probability distributions.** They make every future
   operating-point question free to answer.
10. **Batch, but know what it costs you.** 6–7% of decisions flip.

---

## 12. What is still unmeasured

Listed because a report that does not say what it does not know is not worth
much.

**Critical — blocks any adoption decision:**

- **No LLM baseline.** Same corpus, same rubric-as-prompt, a Haiku-class model.
  Without it, §9 is analysis rather than evidence.
- **No trained-classifier baseline.** Even an off-the-shelf toxicity model
  evaluated on the same 300 comments would anchor the 0.864 AUC.
- **Accuracy is measured on two axes of four.** `substance` and `on_topic`
  have no labelled evaluation at all, because the labelled corpus has no thread
  context. They are scored, shipped and unvalidated.

**Important:**

- **The escalation queue has never been read cold.** The product claim is that
  penned comments are genuinely ambiguous. Nobody has sat down with 30 of them
  and recorded a hesitation rate. This is the one thing measurement cannot
  settle and it remains open.
- **Adversarial robustness untested.** A comment containing instructions
  addressed to the model, deliberate obfuscation, or encoding tricks. A
  non-generative model should be structurally more resistant, but that is a
  hypothesis.
- **Thread depth as a variable.** `on_topic` confidence was 0.86 on 5-deep
  threads and 0.354–0.450 on 14-deep ones. Depth is confounded with corpus and
  has never been isolated.
- **Non-English content.** Entirely untested.
- **The 6–7% batching wobble** is characterised only in aggregate.

**Minor:**

- Index-addressing the two intrinsic axes for a ~7% token saving.
- Whether `contempt`'s 0.66 floor is an axis property or a rubric-design ceiling
  we have not yet escaped.

---

## 13. Conclusion

Jev is a genuinely different tool and the difference is not accuracy — it is
that **the policy is prose, the uncertainty is a calibrated number you get for
free, and both are cheap enough to iterate on in seconds.** That combination
does not exist in either of the obvious alternatives, and for a problem where
the definition of the thing being detected is contested and keeps moving, it is
worth real accuracy to have.

The measured capability is solid rather than spectacular: AUC 0.864 zero-shot,
0.989 specificity and 0.294 recall at a precision-leaning operating point, and a
confidence signal that carries genuine information but roughly a third of what
the uncorrected curve suggests. The system automates the easy 70% of a
moderation queue at high precision and hands a human everything near the line,
including most of the actual violations. Presented honestly, that is a useful
product. Presented as "we automate moderation", it is a claim the numbers do not
support.

The strongest single result in the project is not about the model at all. It is
that **the rubric was the constraint every time we thought the model was.**
Every apparent capability ceiling we hit — the flooded escalation queue, the
collapsing `on_topic` confidence, the empty Approved lane — turned out to be a
question we had written badly, and each one was fixed by rewriting a sentence
rather than by changing anything about the model or the pipeline. The one axis
where that stopped working, `contempt`, is the one place we are reasonably
confident we found the model's actual edge.

That is the shape of the paradigm: enormous leverage in the prose, and a real
ceiling behind it that you only reach by exhausting the prose first.

---

*Measured 2026-09-19 to 2026-09-20 against `jev-1.13.0`. Experiments in
`experiments/` and `calibrate/`; raw write-ups in [`FINDINGS.md`](FINDINGS.md);
the corrected calibration analysis in §5 is reproducible with
`uv run python -m calibrate.baseline`.*
