# Findings — what the experiments changed

Measured 2026-09-19 against `jev-1.13.0` from a laptop. Everything below is
reproducible from `experiments/` and `calibrate/`; the raw recordings land in
`experiments/out/`.

Read this before `TECHNICAL_SPEC.md`. Three of that document's load-bearing
assumptions turned out to be wrong, and one risk it did not name is now the
main one.

---

## 1. The batch-size knee does not exist

`TECHNICAL_SPEC.md` §3.2 called this the weekend's real risk and told us not to
build any UI until it was measured. It is measured. There is no knee.

300 labelled Civil Comments, scored at every batch size from 1 to 30:

| B | 1 | 3 | 5 | 8 | 12 | 15 | 20 | 30 |
|---|---|---|---|---|---|---|---|---|
| agreement with human label | 0.780 | 0.760 | 0.740 | 0.760 | 0.750 | 0.760 | 0.757 | **0.787** |
| mean confidence | 0.626 | 0.604 | 0.607 | 0.605 | 0.605 | 0.610 | 0.614 | 0.614 |
| tokens/comment | 900 | 710 | 671 | 650 | 638 | 633 | 628 | 624 |

B=30 was the *best* run. No questions went unanswered at any size — 120
questions in a single request all came back. This matches TypeSafe's own claim
that questions are evaluated independently and in parallel with no batching
effect beyond sampling noise.

**But it is not entirely free, and the spec had no way to see that.** A noise
floor — B=1 run twice, everything else identical — gives a mean score delta of
**0.081 / 10** and lane agreement of **0.993**. The model is very nearly
deterministic. Against that floor, batching moves scores by 0.54–0.71 and flips
**6–7% of lane decisions**. That is a real batch effect, roughly 8× noise. It is
*lateral* — it does not reduce agreement with human labels — but it is there.

For this demo, 6% wobble is fine. For anything where the same comment must get
the same verdict twice, it is not.

**Decision:** `BATCH_SIZE = 15`, chosen for headroom under the 64K
state-plus-questions ceiling rather than for accuracy. B=20 is fine too.

## 2. Addressing by index is the wrong request shape

§3.3 put the comments in the state and had each question say "for comment c07".
That exposes the design to two separately documented `jev-1.13` failure modes:
it "cannot reliably count items, with error growing with the size of the thing
being counted", and "accuracy falls as the state grows with content unrelated to
the decision."

At B=15, when the question is about comment 7, the other fourteen comments in
the state are exactly that unrelated content.

`quoted` puts the comment text in its own question and leaves the state holding
only the thread context. Nothing has to be located, and no other comment is
present to distract. It does **not** cost throughput — still 15 comments per
request. It costs about 45% more tokens, and we are request-limited.

Tested head to head at B=15. Paired per-comment deltas, v2 rubric, index →
quoted:

| axis | confidence delta | |
|---|---|---|
| `on_topic` | **+0.140 ± 0.012** | *** |
| `substance` | **+0.031 ± 0.011** | * |
| `hostility` | −0.012 ± 0.011 | ns |
| `contempt` | +0.015 ± 0.013 | ns |

**The gain is entirely on the two axes that need thread context**, which is
what the context-rot mechanism predicts: clearing fourteen irrelevant comments
out of the state helps the questions that have to look at the state, and does
nothing for the two axes that are intrinsic to the comment's own text. That is
a mechanism rather than a correlation, and it is the strongest result in this
document.

**The two factors interact**, which only the paired view shows. Under the v1
rubric, `quoted` does nothing for `on_topic` (+0.004 ± 0.015, ns); under v2 it
is worth +0.140. The reading: v1's `on_topic` levels are ambiguous enough that
the question cannot be answered confidently no matter how clean the state is, so
removing distractors buys nothing. Fix the levels and the state quality starts
to matter. **Rubric quality gates whether anything else you do to the request
shape has an effect** — which is an argument for fixing rubrics first.

`substance` is the one axis `quoted` helps regardless of rubric (+0.033 and
+0.031), so that part is robust.

Accuracy also moved in `quoted`'s favour but only weakly — pooled across both
rubrics, the arms disagree on 24 comments and `quoted` is right on 16 of them
(one-sided p ≈ 0.08). Directionally consistent in both rubrics, not
significant on its own.

**Decision:** `ADDRESSING = "quoted"` — on the confidence result, not the
accuracy one.

## 3. The rubric was the real constraint, not the model

The first rubric was a degree ladder — "mildly pointed", "openly critical",
"sustained abuse" — and its contempt level 0 read *"treats disagreement as
legitimate, **or** does not engage with opposing views at all"*: two situations
in one level.

The Score docs name three causes of low confidence — the levels overlap for this
state, the question measures more than one thing, the state doesn't say enough —
and give one instruction: **describe situations, not degrees**. The v1 rubric
committed two of the three sins.

Rewriting every level as a situation (v2, now in `judge/rubric.py`) is a
**trade, not an improvement**, and the first version of this section said
otherwise. Paired per-comment deltas, quoted addressing, v1 → v2:

| axis | confidence delta | |
|---|---|---|
| `on_topic` | **+0.165 ± 0.012** | *** |
| `hostility` | **−0.083 ± 0.016** | *** |
| `contempt` | −0.018 ± 0.021 | ns |
| `substance` | −0.020 ± 0.014 | ns |

Much better on `on_topic`, significantly *worse* on `hostility`. Mean confidence
rose (0.626 → 0.637) only because the first outweighs the second.

**Accuracy did not change.** Of the 23 comments where the two rubrics disagree,
v1 is right on 11 and v2 on 12. A coin flip. The headline this section used to
carry — recall 0.43 → 0.57 — is a real number but it describes v2 sitting at a
*more aggressive operating point*, not judging better: precision fell 0.85 →
0.74 in exchange. Discrimination is unchanged.

So the three rubric rules below are supported by the **confidence** result, on
the axis whose v1 levels were worst, and not by an accuracy result. That is
still the right kind of evidence — confidence on a Score *is* probability
concentration, which is exactly what level overlap destroys — but it is a
narrower claim than "the rewrite made it better".

**Open:** v2's `hostility` levels are worse than v1's and nothing has been done
about it. The obvious next move is a per-axis rubric rather than a wholesale
version bump — keep v1's hostility, take v2's on_topic. Nothing tests that yet.

## 4. `min()` over four axes is what emptied the demo, not the floor

This is the one that matters.

With `CONFIDENCE_FLOOR = 0.80` applied to `min()` across four axes, **99–100% of
comments go to the Pen**. Every configuration, every batch size, every rubric.
The demo has no Approved lane at all.

That is arithmetic, not caution. Four axes each averaging 0.6–0.7 confidence,
take the worst one every time, and nothing survives.

The tempting fix is a lower floor. The Score docs warn against exactly that:
*"build the low-confidence path before you build the automation. If there's
nowhere for uncertain cases to go, you'll be tempted to lower the threshold
instead — and then you've rebuilt the exact thing you were trying to escape."*

So the floor stays high and the *gate* changes. `decisive` applies the floor
only to the axes that carried this comment's verdict: both severity axes always,
plus the quality axes only when the low-quality rule is what fired. If hostility
is 9.4 at confidence 0.93, an unsure `on_topic` is not information about that
decision and should not veto it.

From `calibrate/sweep.py`, at a fixed 0.85 floor:

| gate | auto-handled | agreement on auto-handled |
|---|---|---|
| min4 | 0% | — |
| mean4 | 14% | 0.929 |
| severity | 23% | 0.928 |
| **decisive** | **22%** | **0.940** |

**Decision:** `CONFIDENCE_GATE = "decisive"`, `CONFIDENCE_FLOOR = 0.85`.

### The confidence signal itself is real

Worth stating plainly, because it is the product claim. Agreement on the
auto-handled subset rises monotonically with the floor, and at `min4 / 0.70` it
reaches **1.000** on the 4% of comments that clear it. Higher confidence really
does mean higher accuracy on this data. The Pen is not a fig leaf.

## 5. The numbers above are pessimistic, and the reason is fixable

Civil Comments has no thread structure. So `on_topic` — "what is it responding
to?" — was being asked with nothing in the state to respond to. That is the
third documented cause of low confidence, induced by the dataset.

`on_topic` mean confidence:

- **0.50** on Civil Comments (no thread context)
- **0.86** on the Reddit smoke corpus (real title, selftext and parent snippet)

`on_topic` is the binding axis in 5 of 15 comments. Re-run
`calibrate/sweep.py` against a fetched Reddit thread before setting the floor
for the demo, and expect the auto-handled share to be materially better than
the 22% above.

Keeping the two datasets separate remains right — Jigsaw has labels, Reddit has
context — but **the calibration curve has to be measured on data that has thread
context**, or it is measuring the dataset's missing fields.

## 6. Cost and latency are not problems

- p50 latency **169 ms** at B=1 from this laptop, well inside the published
  70–500 ms.
- The whole investigation — 1,816 requests, ~4M tokens — cost **$0.22**.
- At B=15 quoted, ~900 tokens/comment is **$0.038 per 1,000 comments**.
- Sustained throughput is rate-limited, not latency-limited: 20 req/s × B=15 =
  **300 items/sec**, which clears the PRD's 200/sec target.

## 7. What is still unmeasured

- **Everything above is Civil Comments.** Only 15 hand-written comments have
  been run with real thread context.
- **Recall against human labels is 0.57 at best.** We miss more than a third of
  what Jigsaw calls toxic. Some of that is a definition mismatch — Civil
  Comments' "toxic" is largely insult and obscenity, while three of our four
  axes measure Reddit-shaped things with no counterpart in the label — but not
  all of it.
- **The 6% lane wobble from batching** has not been characterised beyond the
  aggregate.
- **Live rubric editing has not been tested at all.** v2 took several minutes of
  careful writing and came out a lateral move — better on one axis, worse on
  another, accuracy unchanged. A viewer editing a level mid-demo will very
  plausibly make it worse, and the demo should be honest that this is what
  tuning actually looks like.

- **Arms ran one run each, sequentially.** There is no within-arm variance
  estimate; the noise-floor control is doing that work by proxy, and any API
  drift during the run is confounded with the arm order. Interleave if any of
  these contrasts start carrying real weight.

## 8. A methodological note worth keeping

The rubric result was reported as a win for about an hour because the aggregate
rates moved in the right direction — mean confidence up, recall up, lane
agreement up 0.793 → 0.800. Every one of those was true. The last one is two
comments out of 300.

Paired per-comment tests were available the whole time, because all four arms
scored the same corpus in the same order. **Compare arms pairwise, not by their
aggregate rates.** Rates this close are one or two comments wide, and the
direction of a rate says nothing about whether the arms actually disagree.

## 9. §5 was wrong: thread context did not rescue `on_topic`

Measured 2026-09-20 on a real Hacker News thread (1,562 comments, max depth 14),
via `experiments/thread_probe.py`.

§5 above predicted that `on_topic` confidence would recover once the data had
thread structure, on the basis that Civil Comments starved it of a referent.
**It went the other way.** With a thread title in the state and a real parent
snippet in the question:

| | Civil Comments (no thread) | Hacker News (real thread) |
|---|---|---|
| `on_topic` mean confidence | 0.500 | **0.354** |

Several comments came back at confidence **0.00** while still producing a
plausible score. The axis was not starved — it was *confused*.

The cause is in the rubric, not the data. The levels said "the thread's
subject" and "the specific question or claim the thread is about", while the
question supplied a *parent comment* to judge against. Two competing referents
in one question — the documented "measuring more than one thing" failure. On
Civil Comments there was no parent, so the ambiguity never surfaced; adding the
context exposed it. **Adding context to an ambiguous question made it worse.**

Rewriting every level to name one referent — the quoted text it is replying to,
with the thread title standing in for top-level comments — recovered most of it:

| `on_topic` mean confidence | |
|---|---|
| levels naming the thread, parent supplied | 0.354 |
| levels naming the quoted text | **0.450** |

Still the weakest axis by a distance, and still below the 0.86 seen on the
hand-written smoke corpus — where the threads are five comments deep, not
fourteen. Depth is the untested variable.

**The general lesson is the one worth keeping:** a confidence number that looks
like a data problem can be a wording problem, and the two are distinguishable
only by changing one and re-measuring. §5's diagnosis was reasonable and wrong.

## 10. The Pen problem is now `hostility` and `contempt`

Same run. With `decisive` gating the quality axes only enter the gate when the
low-quality rule fires, so `on_topic` is no longer what pens most comments:

| axis | mean confidence on a real thread |
|---|---|
| `substance` | 0.720 |
| `hostility` | 0.675 |
| `contempt` | 0.666 |
| `on_topic` | 0.450 |

`decisive` takes the minimum of `hostility` and `contempt` on every comment that
is not caught by the quality rule. Two axes at ~0.67 give a decisive confidence
of **0.538**, and at the 0.85 floor that leaves **16% auto-handled, 84% penned**.

So the remaining Pen volume traces directly to the two severity axes, and
`FINDINGS.md` §3 already flagged that v2's `hostility` levels are *worse* than
v1's by 0.083 and that nothing had been done about it. That open item is now the
highest-value work in the project — it is the only thing standing between the
current state and a demo with three visible lanes.

Note also what did **not** move: `hostility` −0.027 and `contempt` +0.000
against Civil Comments. These two axes are intrinsic to the comment's own text,
they get no thread context by design, and they behave identically across two
very different corpora. That is a good sign for the rubric being stable; it just
needs to be *sharper*.

## 11. Rubric levels, picked one axis at a time

`experiments/axis_ab.py`, 2026-09-20. 300 Hacker News comments for confidence
under real thread context, 300 labelled Civil Comments for accuracy on the two
intrinsic axes.

**All variants went in the same request.** Jev evaluates questions
independently against one shared state, so `c000__hostility__v1` and
`c000__hostility__v3` are just two questions about the same comment. That gives
perfect pairing and removes the arm-order confound §7 listed as an open
weakness. It is strictly better than the sequential-arms design used earlier and
any future rubric contest should be run this way.

Mean confidence, Hacker News:

| axis | v1 | v2 | v3 | winner |
|---|---|---|---|---|
| `hostility` | **0.824** | 0.710 | 0.725 | **v1**, by 0.114 *** |
| `contempt` | 0.682 | 0.648 | 0.654 | see below |
| `substance` | 0.658 | **0.703** | — | **v2**, by 0.045 *** |
| `on_topic` | — | 0.284 | **0.431** | **v3**, by 0.147 *** |

Accuracy against the human label was a wash on `hostility` (10:12 and 9:8 on
discordant comments — differently wrong, not better), so the confidence result
decides it uncontested.

### The "situations, not degrees" rule is real but it is not the mechanism

This is the part worth keeping. `hostility` v1 **is the degree ladder** —
"mildly pointed", "openly critical", "sustained abuse" — and it beat two
careful situational rewrites by 0.114 and 0.099, on both corpora.

So the rule as stated in the docs, and as this project had been applying it,
over-generalises. What actually predicts confidence is whether the levels form
**one unambiguous ordering**. Both situational rewrites smuggled in a rung that
was a different dimension rather than a lower degree — v2's "reports what a
person did or said, without judging them" is not less hostile than level 0, it
is *orthogonal* to it — and probability split between it and its neighbour.

Situational phrasing usually produces a cleaner ordering, which is why the rule
works most of the time and why it won `substance` and `on_topic` here. It is a
heuristic for the real thing, not the real thing.

### `contempt` is the one axis where confidence and accuracy disagreed

v1 is 0.028 more confident (about 2 sigma, marginal). v3 is meaningfully more
accurate: **16:6** on the comments where the two disagree. v3 was taken. 0.028
is noise next to hostility's 0.114, and being right is what the product is for.

### `contempt` also looks like it has a floor

All three variants land between 0.648 and 0.682 — a spread of 0.034 across
three genuinely different phrasings, against `hostility`'s spread of 0.114 and
`on_topic`'s 0.147. Two of the three were written specifically to fix it.

The most likely reading is that contempt is simply a harder judgement than the
others, and that ~0.66 is close to what this model can do on it. Further rubric
work on this axis looks like poor value.

### Net effect on the demo

Applying the per-axis mix and re-probing the same Hacker News thread:

| | before | after |
|---|---|---|
| `hostility` confidence | 0.675 | **0.813** |
| `contempt` confidence | 0.666 | 0.640 |
| decisive confidence | 0.538 | **0.599** |
| auto-handled at floor 0.70 | 35% | **41%** |
| auto-handled at floor 0.60 | 49% | **60%** |
| auto-handled at floor 0.85 | 16% | 16% |

The curve moved right, but **not at the current floor**, because `decisive`
takes `min(hostility, contempt)` and `contempt` is now the binding axis on
almost every comment. Fixing `hostility` handed the bottleneck to the axis that
appears to have a floor.

## 12. The gate is asking the wrong question — the next move

Rubric work has taken `hostility` as far as it goes and `contempt` looks close
to its ceiling, yet 84% of traffic still pens. That points at the gate rather
than the rubric, and there is a specific reason to think it is wrong.

`ScoreAnswer.confidence` measures **how concentrated the probability is across
the five levels**. The lane decision does not need that. It needs to know which
**side of the severity threshold** the comment falls on.

Those are very different questions. A comment whose probability is spread
evenly across levels 0, 1 and 2 has low confidence — the model genuinely does
not know which level — while every one of those levels is far below the
threshold. The *level* is uncertain and the *decision* is not. Under the current
gate that comment goes to the Pen, and a human is asked to adjudicate a
judgement the model was never actually unsure about.

`ScoreAnswer` carries `probabilities`, a per-level distribution, so this is
directly computable:

```python
p_over = sum(p for level, p in answer.probabilities.items() if level >= OVER_AT)
decision_confidence = max(p_over, 1 - p_over)
```

That is still calibrated confidence and still routes genuine uncertainty to a
human — it just measures uncertainty about the thing the lane actually turns on.
It should move Pen volume substantially without touching the floor, which is
what §4 said the fix must not be.

**Untested.** The probe records scores and confidences but not the
distributions, so measuring it needs one more run. This is the next task, and it
is more likely to matter than any further rubric work.

## 13. The gate, measured. §12's hypothesis was half right

`experiments/gate_ab.py`, 2026-09-20. 300 labelled Civil Comments and 300
Hacker News comments, capturing the full per-level distribution.

### The half that was wrong: it is not more discriminative

§12 implied the scalar gate was letting good decisions pen and that fixing it
would buy accuracy. It does not. Ranking comments by each gate and taking the
top slice, at **matched auto-handled volume**:

| auto-handled | scalar conf | decision conf | delta |
|---|---|---|---|
| 20% | 0.933 | 0.900 | −0.033 |
| 40% | 0.950 | 0.942 | −0.008 |
| 60% | 0.894 | 0.917 | +0.022 |
| 80% | 0.858 | 0.871 | +0.013 |
| 100% | 0.810 | 0.810 | 0.000 |

No consistent direction, and the two gates rank comments at a correlation of
**0.845**. It is the same curve. The 83%-auto-handled figure below was always
available from the scalar gate — at a floor near 0.30.

**Anyone reporting this as an accuracy win is reading a volume change.** It is
the same mistake §8 records, in a new place.

### The half that was right: it measures the thing the lane turns on

Scalar confidence has mean 0.612 on a real thread; decision confidence has mean
0.912. So the *same* floor means completely different things:

| floor | auto-handled, scalar | auto-handled, decision |
|---|---|---|
| 0.85 | 17% | **83%** |
| 0.90 | 11% | 76% |
| 0.95 | 2% | 58% |

That is not cosmetic, for three reasons:

1. **The number is interpretable.** 0.85 decision confidence means "at least 85%
   of the probability mass is on one side of the line". 0.30 scalar confidence
   means "probability is spread across levels", which is not what the lane asks
   and cannot be explained to anyone in the room.
2. **The Pen fills with the right comments.** Under the scalar gate a comment
   spread across levels 0, 1 and 2 pens, and a human is asked to adjudicate a
   decision the model was never unsure about. That is a waste of the scarcest
   resource in the product.
3. **PRD §6.1's threshold slider becomes meaningful.** Moving it visibly trades
   Pen volume against agreement, and both numbers read in plain English.

**Decision:** `CONFIDENCE_GATE = "decision"`, floor 0.85 — an operating point,
not a discovery.

### A clean negative result: the level boundary does not matter

The second hypothesis in §12 was that thresholding the *mean* at 6.0 — between
level 2 (5.0) and level 3 (7.5) — was exposed to the documented weakness that
`jev-1.13` score levels are "weak in numerical calibration", and that
`P(level >= 3) > 0.5` would be a better decision rule.

It makes no difference. Of 300 comments the two rules disagree on **five**,
3:2, with agreement 0.810 against 0.807. `SEVERITY_THRESHOLD` stays at 6.0.

The documented weakness is real; it just does not bite at this threshold, on
this rubric. Worth remembering before building anything else on a warning from
the docs without measuring whether it applies.

### What the demo looks like now

Same thread, same sample, end to end:

| | before | after |
|---|---|---|
| Approved | 15% | **79%** |
| Bounced | 1% | 1% |
| Pen | 84% | **20%** |

Three visible lanes, a Pen at a plausible 20%, and penned comments that are
genuinely hard to call — one of the four sampled is a string of morse code and
emoticons with substance 0.0, which is exactly the kind of thing a human should
decide on.

**Bounced is still 1%, and that is now the largest remaining gap.** It is the
known Hacker News weakness — `hostility` barely fires there — and it is what
`TASKS.md` 1.1d (ChangeMyView) exists to fix. A demo whose red lane never
lights up is only two-thirds of the story.

## 14. The rule and the gate were asking about different lines

Found while investigating why ChangeMyView — a corpus *curated for conversations
that derail into personal attacks* — still produced only a 2% Bounced lane.

Of 200 CMV comments, 25 (12%) were over the line on scores alone. **21 of those
25 were penned.** Mean decision confidence on them was 0.699 against 0.917 for
everything else. The model is systematically least sure about exactly the
comments near the threshold, which is correct calibration and not the bug.

The bug was that the two halves of the lane function disagreed about where the
threshold was:

- the **rule** asked `mean >= 6.0` on the normalised scale;
- the **gate** asked about `P(level >= 3)`, and level 3 normalises to **7.5**.

So a comment scoring 6.7 was over the line by the rule, while the gate reported
how unsure it was about a stricter line nobody was applying. Reading the score
distribution makes the mismatch obvious: `hostility` maxes at **7.4** across 200
comments and *nothing* reaches 7.5. The expected score is a shrunk estimator —
probability is always spread across levels — so thresholding the mean at a level
value is far stricter than asking whether the model thinks it is that level.

Aligning both halves on `P(level >= 3) > 0.5` is accuracy-neutral by §13 (the
two rules disagreed on five of 300 labelled comments) and roughly doubled the
Bounced lane on both corpora:

| | Approved | Bounced | Pen |
|---|---|---|---|
| Hacker News, before | 79% | 1% | 20% |
| Hacker News, after | 77% | **3%** | 19% |
| ChangeMyView, before | 72% | 2% | 26% |
| ChangeMyView, after | 70% | **4%** | 27% |

**Lesson worth keeping:** when a gate and the decision it guards are derived
from different quantities, check they refer to the same boundary. Nothing in the
tests caught this, because every test supplied probabilities consistent with its
scores by construction.

## 15. A thin Bounced lane is a property of the data, not the pipeline

Both corpora now sit at 3–4% Bounced, and further pipeline work will not move
that much. `hostility` across 200 ChangeMyView comments: median 1.2, p75 3.4,
p90 5.2, **max 7.4**. Nothing in a corpus selected for derailment reaches the
rubric's top rung — "sustained abuse of a person: degrading language, or
wishing harm on them".

That is what real discussion forums look like. Most comments are fine, a
meaningful slice is genuinely borderline, and unambiguous abuse is rare. The
borderline slice going to the Pen is the product working as designed — PRD §5.1
is explicit that amber means "Jev doesn't know, so a human decides", not "mildly
toxic".

So 70/4/27 is an honest picture, and it is arguably a *better* demo than a wall
of red: it puts the attention where the PRD says it belongs. But it is a product
decision rather than an engineering one, and there are three ways to go:

1. **Accept it.** Three visible lanes, Pen at a quarter of traffic, and a claim
   that survives contact with a trust-and-safety buyer who moderates real
   forums.
2. **Lower `SEVERITY_THRESHOLD` to the level-2 boundary.** Rubric level 2 is
   "openly critical of a person's character, motives or intelligence, but not
   abusive". Treating that as over the line is defensible for some products and
   would bounce roughly 12% — but it is a change to what the demo *claims*
   moderation means, not a tuning fix.
3. **Source harsher data.** Civil Comments has `severe_toxicity`, `threat` and
   `obscene` labels and plenty of unambiguous material. PRD §4.3 deliberately
   keeps that dataset out of the Reddit path, and mixing it in to make the red
   lane look busier would be staging the demo.

Option 1 is the recommendation. Option 3 should be refused on the PRD's own
terms.

**Decided 2026-09-20: accept it.** The reasoning governs everything after this
point. This project is a measurement of what the paradigm can do on a real
problem, not a sales demo. A characterised limitation is a result. If the honest
answer turns out to be "three lanes, and the red one is nearly empty because
unambiguous abuse is rare and the model is properly unsure about everything near
the line" — that is a more interesting finding than a busy red lane bought by
moving a threshold, and it is the kind of thing the room should be told rather
than shielded from.

## 16. Throughput: token-limited, not request-limited

The pipeline runs. 30 seconds against the live API, four threads, B=15:

```
judged 6,764 in 30.0s = 225/sec     approved 82%  bounced 4%  pen 14%
452 requests, 3 errors, 5.9M tokens, $0.2464
latency p50 285ms, p95 1,291ms
```

That clears the PRD's 200/sec target. Two things underneath it are the opposite
of what `TECHNICAL_SPEC.md` §3.1 predicted.

### More concurrency buys nothing and costs a great deal

The spec reasoned that ~10 concurrent requests would sustain 20 req/s and that
"a semaphore of 32 is ample". Ample was never the risk. Measured over 30
seconds at the same drip rate:

| workers | throughput | 429s | p95 latency | Pen |
|---|---|---|---|---|
| 8 | 225/s | **3** | **1,291 ms** | **14%** |
| 24 | 245/s | 44 | 3,542 ms | 22% |

Tripling concurrency gained 9% throughput and multiplied errors fifteenfold.

The Pen column is the part that matters. Every failed request pens fifteen
comments, by design — spec §3.4, a comment we cannot judge is a comment a human
looks at. Under concurrency pressure that turned the Pen from 14% to 22%, and
**the extra 8% was failures, not uncertainty**. A Pen full of errors looks
identical to a Pen full of hard cases from the outside and is worthless. It is
the one failure mode this demo cannot afford, and it is invisible unless you
count errors separately.

`PIPELINE_WORKERS = 8`.

### The bucket's capacity matters as much as its rate

Rate 20/s with capacity 20 averaged 16 req/s — comfortably under budget — and
still collected 41 rate-limit errors in 25 seconds. A capacity equal to the
per-second rate lets a whole second of requests leave in one instant, and the
server sees the burst, not the average. Now rate 18 with capacity 4.

That alone did not fix it, which is what pointed at concurrency.

### The real ceiling is tokens

Throughput plateaus near 240/s no matter what. At ~800 tokens per comment and
~12,000 per request, 20 req/s would be 238K tokens/sec against a published
ceiling of 250K. We measure 195K/sec sustained — 78% of it — and the bursts
reach it.

The spec estimated **2,400** tokens per request and concluded "you are
request-limited, not token-limited". It is 5× that, because each comment
carries four questions and **every question restates the whole rubric** —
roughly 1,400 tokens of level descriptions per comment, dwarfing the ~150-token
comment it is asking about.

Two consequences:

- **Raising B buys nothing.** Tokens scale with comments, not requests, so the
  token ceiling moves with the batch. Both ceilings bind at once, near 300/s.
- **The lever is shorter rubric levels**, not bigger batches — and that trades
  directly against confidence, which §11 showed is where the demo lives. Not
  worth spending unless throughput ever becomes the constraint, which at 225/s
  against a 200/s target it is not.

`quoted` addressing costs ~45% more tokens than index addressing (§2), so it is
also buying its confidence win partly out of this budget. At current volumes
that is affordable and clearly worth it.

## 17. The wall, and what building it cost

The UI works. Three lanes streaming over one WebSocket at a fixed 12Hz tick,
cards carrying the four-bar fingerprint, Bounced blurred until clicked, the Pen
heavier and labelled with the axis that was unsure. Verified in a browser:
eviction holds the Pen at exactly 150 cards under a live stream (invariant 7),
and clicking a Bounced card unblurs that card alone (invariant 9).

Nothing here changed the product thinking, but three of the four bugs cost real
time and none of them surfaced as an error — the page simply stayed blank.
Worth recording so the next person recognises the shape.

- **`setup_ws` is broken in `python-fasthtml` 0.14.13.** Its connect handler
  does `conns[scope.client]`, but `scope` resolves to the raw ASGI dict, which
  has no `.client`. Every connection raised `AttributeError`. Also note it is
  applied with `@patch`, so the module-level `setup_ws` name is `None` and only
  `app.setup_ws()` exists. We keep our own socket registry instead, which is
  about ten lines and is what a single broadcast loop wants anyway.
- **`str()` on an FT object does not render HTML.** It yields the children
  joined, so a frame went out as the literal text `lane-approvedlane-pentick`.
  The htmx ws extension found no elements carrying `hx-swap-oob` and silently
  did nothing. Use `to_xml`.
- **Returning a full `Html(...)` bypasses FastHTML's page assembly**, so `hdrs`
  — stylesheet, htmx, the ws extension — never reach the head. Return a tuple
  and put body attributes in `bodykw`.
- CSS: grid items default to `min-width:auto`, so one long comment stretched
  its column and squeezed the other two.

There is now a `/health` route returning pipeline counters, queue depths and
the last error. It exists because "the wall is blank" has at least four causes
and guessing between them is slow.

## 18. The account ran out of credits

Mid-way through verifying the UI, every request started returning:

```
402 Your organization has no available TypeSafe API credits.
```

Two things worth taking from it.

**The failure mode held exactly as designed.** 4,050 unjudgeable comments went
to the Pen, each labelled "could not judge", the app stayed up and responsive,
and the error counter tracked them separately from genuine Pen volume — which
is precisely the distinction §16 argued for. Spec §3.4's "degrade into a human
looks at it" is not just a nice idea; it is why a total API outage produced a
working page rather than a stack trace.

**But a demo cannot run on it.** A wall where every card reads "could not
judge" is not a demo of anything. If the account is going to be near its limit
at showtime, the honest mitigation is the same one used for Reddit: a recorded
run replayed from disk. `experiments/out/probe-*.json` already contains real
verdicts, and the render path was verified against exactly that data while the
API was dead. That is a small piece of work and it is now the difference
between a demo and a blank room.

Total spend across every experiment in this document: well under a dollar. The
credits did not run out because the experiments are expensive — see §19.

## 19. Cost per decision is trivial. Cost per minute is not

The credits ran out twice. Neither time was an experiment's fault, and the
reason is a number nobody had computed.

At the measured ~800 tokens per comment and 205 items/sec sustained:

| | |
|---|---|
| per 1,000 comments | **$0.034** |
| per 10,000 comments | $0.34 |
| **per minute of streaming** | **$0.41** |
| per hour | $24.80 |
| an 8-hour conference day | **$198** |

Both columns are true and they tell opposite stories. PRD §6's third success
criterion — "spend counter stays visibly trivial (cents) against a five-figure
volume counter" — holds exactly as written: 10,000 comments really is 34 cents,
and that is the claim worth making to a buyer costing out a moderation queue.

But **the demo is not a queue, it is a wall that runs continuously**, and
nobody had multiplied the per-comment figure by wall-clock seconds. An ambient
display left up during a day of meetings costs about two hundred dollars.

### The model is cheap. The request is fat.

$0.042/M input tokens really is cheap: one comment costs about **$0.000025**.
The spend does not come from the unit price, it comes from sending 15× more
tokens than the comment contains. Decomposed per comment at B=15:

| | tokens | share |
|---|---|---|
| rubric levels, sent once **per question** | 346 | **58%** |
| comment body, sent once **per question** | 156 | 26% |
| parent snippet, on the two context axes | 54 | 9% |
| axis question text | 37 | 6% |
| **total sent** | **~593** | |
| the comment itself | 39 | **7%** |

**93% of every request is overhead**, and it is structural rather than sloppy:
each Score question is answered independently against the shared state, so each
one has to carry its own criteria. Four axes means four copies of the rubric and
— under `quoted` addressing — four copies of the comment. The fan-out that makes
Jev fast is what makes it token-fat.

Multiply by 205 comments/sec and it is ~121K tokens/sec, against a published
ceiling of 250K. **We are running the thing at roughly half its maximum
possible throughput, continuously.** The most this API can bill is about
$38/hour; at $25 we are simply near that. No model is cheap when held flat out.

Three ways down, in order of leverage:

- **Drip rate.** 20/s is $2.42/hour and still reads as a stream. The headline
  205/s does not need to be *sustained* to be shown.
- **Shorter rubric levels** would cut the largest slice, but §11 showed level
  wording is exactly what confidence is made of. That is a direct trade against
  the Pen, and the Pen is the product.
- **Index-address the two intrinsic axes.** §2's measured win from `quoted` was
  entirely on `on_topic` (+0.140) and `substance` (+0.031); `hostility` (−0.012)
  and `contempt` (+0.015) were both *ns*. So those two could take the comment
  from the shared state instead of quoting it. Worth **~7%**, not the 13% first
  estimated — index addressing still puts the body in the state once, so this
  goes from four copies to three, not from four to two. Untested, and the
  smallest of the three levers.

**Done: the drip rate is now the ambient default, at 60/s.** Measured on the
slider: 20/s gives 19 items/sec at 2 req/s, 200/s gives 195 items/sec at 13
req/s — a 6.5× spend difference on one control. 60/s costs ~$7.25/hour and
still fills the wall, because the render cap only shows ~96 cards/sec anyway.
PRD §6's "≥200/sec sustained" is a claim about *capability*, it is measured and
recorded in §16, and it does not need to be burning money while nobody is
asking. The slider goes to 300 for the moment someone does.

### What actually drained it

`preview_stop` does not kill the `uv run` child process. Eight restarts left
eight pipelines judging at ~225 items/sec with no browser attached — roughly
$3/minute in aggregate, for nobody. The 429 storm that looked like a rate-limit
mystery in §16 was partly these servers competing with each other: killing them
took the error rate from 49% to 2% with no code change.

### Mitigations, in order of how much they matter

1. **Judge only while someone is watching.** `Replay` now waits on a `demand`
   event that `web/app.py` clears whenever no socket is connected. An orphaned
   dev server now costs nothing. This is a cost control, not an optimisation,
   and it is the single change that would have prevented both outages.
2. **The drip rate is the cost dial, and it is already in the UI.** 205/s is
   $24.80/hour; 20/s is $2.42/hour and still looks like a stream. The headline
   throughput number does not have to be *sustained* to be demonstrated — the
   counter can be pushed to 200/s for the moment someone asks and left low
   otherwise.
3. **Offline replay (`TASKS.md` 4b.2) is the right default for an all-day
   wall**, not merely insurance. Recorded verdicts cost nothing, and live
   judging can be reserved for the interaction hooks — test-your-own-comment
   and rubric editing — which is where a viewer actually wants to see the model
   think.

This is the clearest capability finding in the document, and it is not about
accuracy. **Jev is cheap per decision and the architecture is cheap per
decision; a demo that never stops making decisions is not cheap.**

## 20. Phase 5: the Pen closes the loop

Allow / Bounce work. A penned card leaves the Pen, lands in the lane the human
chose marked "you decided", loses its buttons so it cannot be clicked twice,
and a counter tracks how many the room has resolved. Nothing persists — PRD §8
has no accounts or storage — and that is fine: the visible effect is the point,
because the Pen *draining as people work it* is the human-in-the-loop story the
whole demo is making.

Two things worth recording.

**Decisions travel the WebSocket, not the POST response.** `/decide` records
and returns nothing; the move renders on the next 12Hz frame. That keeps every
DOM mutation on one channel, serialised on the tick, which is what invariant 1
was already asking for.

**But that refactor was not the bug fix, and the first diagnosis was wrong.**
Allow silently did nothing while Bounce worked, and the obvious story — two
channels racing for the same busy lane container — was wrong. The actual cause
was the per-frame card sample: the resolved card was appended to the Approved
list *behind* the ~19 streamed verdicts of that tick, then truncated away by
`CARDS_PER_FRAME = 8`. Bounced worked only because its lane is nearly always
empty, so nothing got truncated.

The tell was there the whole time: the failure tracked lane *volume*, not lane
identity. Decided cards are now kept out of the sampled list entirely, which is
also the correct rule — §17's note that the Pen is never sampled applies just
as much to a comment a human has already acted on.

## 21. Retuning the policy is free, and it is the best thing in the demo

Moving a threshold needs **no model calls at all**. Every verdict already
carries its four scores and its four per-level probability distributions, so a
new floor, a new severity threshold or a different gate is a pure recompute
over verdicts already in hand. `Pipeline.retune` walks the retained backlog and
re-runs `lane()`; nothing touches the API.

Measured on the live wall, dragging only the confidence floor:

| floor | Approved | Bounced | Pen | auto-handled |
|---|---|---|---|---|
| 0.99 | 150 (capped) | 1 | 150 (capped) | **31%** |
| 0.85 | 150 (capped) | 23 | 141 | 75% |
| 0.55 | 150 (capped) | 91 | 22 | **96%** |

API requests over the whole exercise: **zero beyond the ongoing drip.**

This matters more than it sounds. PRD §6.1 argued the auto-handled share should
be a live number beside the control rather than a hard-coded "we handle 94%"
claim the room cannot check — and it turns out that version is not just more
honest, it is *cheaper than the claim*. A viewer drags the floor, watches the
Pen fill or drain, and reads the trade off the screen. That is the calibration
curve from §13 made physical, and it costs nothing to run.

Worth keeping the distinction clear, because it is easy to conflate:

- **Thresholds, gate, drip rate — free.** Pure recompute over stored verdicts.
- **Rubric *wording* — expensive.** It changes the question, so the backlog has
  to be re-judged (spec §6). That is the one in `TASKS.md` 6.2, and it is the
  one that costs money.

## 22. Test-your-own-comment, and the axis that shows up in it

PRD §4.2's shareable artifact works — one comment, one request, the same gate,
the same fingerprint. Four cases, run live:

| typed | lane | fingerprint (h/c/s/o) | gate |
|---|---|---|---|
| "You are a complete moron and everyone here knows it." | **bounced** | 7.5 / 9.5 / 1.1 / 0.2 | 95% |
| "I think the second point is wrong, because the study only sampled undergraduates." *(with context)* | **approved** | 0.0 / 2.4 / 6.4 / 6.7 | 100% |
| "lol" | **pen** | 0.1 / 0.0 / 0.0 / 0.9 | 82% |
| "Anyone who still believes this is beyond help. Not worth explaining again." | **pen** | 4.8 / **9.2** / 0.8 / 5.0 | 79% |

The first two are the demo working exactly as advertised, and the first is the
screenshot people will take.

**The fourth is the finding.** That is a textbook contempt comment — "not worth
explaining again" is almost the rubric's level-4 wording — and the model scores
`contempt` at **9.2 out of 10**. It still goes to the Pen, because the gate is
only 79% sure which side of the line it falls, and `contempt` is the axis §11
found has a confidence floor around 0.66 that three separate rewrites could not
move.

So the weakest axis in the rubric is the one a viewer is most likely to probe
deliberately, and on the clearest possible example it produces a *high score
with low confidence* — which routes to a human. That is the mechanism behaving
correctly and the demo reading slightly oddly at the same time, and it is worth
being ready to say so out loud rather than reloading until it bounces.

"lol" landing in the Pen at 82% is the better story: substance 0.0 and on_topic
0.9 fire the low-quality rule, but the model is genuinely unsure whether a
two-letter reaction is noise or harmless, which is exactly the judgement a human
should make.

### The context field is not decoration

A typed comment has no parent, and §9 established that scoring `on_topic`
against nothing is a documented cause of low confidence. Without somewhere to
put context, every test comment would pen on `on_topic` for a reason that has
nothing to do with what was typed. The optional "replying to…" field is what
makes the approved case above reach 100%.

### Rate limiting

Spec §9 asked for a per-IP limit and a hard total cap on `/test`, since the API
key is on the presenter's laptop and an open text box pointed at a paid API is
the kind of thing that gets found. Both are in `web/limits.py`: a sliding
per-IP window and a ceiling for the process run. A held request does not
consume the total budget, or a retry loop would exhaust the cap without anyone
ever getting an answer.

At $0.000025 a comment this is about abuse rather than the cost of honest use —
but §19 is the reminder that small numbers times a large multiplier is how the
credits went the first two times.

## 23. Live rubric editing, and the edit that made it worse

Spec §6's budget was three seconds. Measured, on a live wall:

```
re-judged 630 comments in 2.9s for $0.0305 — 43 changed lane (7%)
```

Inside budget, and the seam is visible as the wall re-sorts. At a saturated
800-comment window it would be ~3.7s, so `REJUDGE_WINDOW` is the dial if that
matters more than sweep size.

### The edit, and what it did

I edited one `contempt` level, and I edited it *in the direction the project's
own rules recommend*. The level read:

> "A differing view is called not worth discussing, **or** is refused engagement."

That is a level containing "X, or Y" — exactly what the rubric rules in
`CLAUDE.md` say not to write. I replaced it with a single situation:

> "A differing view is brushed aside as not worth discussing."

Per-axis mean confidence, same 630 comments before and after:

| axis | before | after | |
|---|---|---|---|
| `hostility` | 0.754 | 0.751 | — |
| **`contempt`** ✎ | **0.649** | **0.632** | **▼ −0.017** |
| `substance` | 0.761 | 0.761 | — |
| `on_topic` | 0.487 | 0.490 | — |

**The edit made the axis I edited worse.** A rule-following change, removing a
documented defect, on the axis it was meant to help — and confidence fell.

That is the third independent confirmation of §11's finding that `contempt` has
a floor near 0.66 that rewording does not move. It is also the strongest
possible argument for how this feature should be framed.

### So frame it as a trade, not an improvement

PRD §4.2 offers live rubric editing as the answer to "why not just train a
classifier" — a trained model needs a labelling run and a retrain; this needs a
sentence. That claim is **completely intact**: the sentence was rewritten and
630 comments were re-judged in under three seconds for three cents. No
retraining pipeline does that.

What is *not* intact is any suggestion that the edit will be an improvement.
A viewer who edits a level mid-demo will quite likely make it worse, and the
demo should be built to survive that rather than hope it does not happen. So
the report shows **every axis**, before and after, on the same comments:

- showing only the edited axis would hide the cost elsewhere;
- showing only "43 changed lane" would imply motion is progress;
- showing the deltas makes the honest claim — *this is what tuning actually
  looks like* — legible without anyone having to say it.

The panel says so in the UI too, above the fields: "Expect a trade rather than
an improvement."

This is the most useful thing the demo does, and it only works because the
report is honest. A version that claimed improvement would be wrong roughly
half the time in front of the room.

## 24. The calibration curve, and the claim it actually supports

> **Superseded in part by §27.** The curve below is correct and the reading of
> it in this section is not: agreement rises with the floor largely because the
> auto-handled subset sheds its toxic comments, and against the right baseline
> the gate buys a third of what this section claims. The chart in the app now
> carries that baseline. Read §27 with this one.

PRD §4.3's second screen, and the direct answer to the accuracy objection in
§2. Entirely offline — it renders a recorded sweep from `calibrate/curve.json`
and calls nothing, which is deliberate: the objection gets answered from
evidence already gathered, not from a live run whose numbers would move while
someone was looking at them.

The `decision` gate, 300 labelled Civil Comments:

| floor | auto-handled | agreement on those |
|---|---|---|
| 0.50 | 100% | 0.807 |
| 0.65 | 89% | 0.850 |
| 0.75 | 80% | 0.866 |
| **0.85** *(shipped)* | **70%** | **0.876** |
| 0.95 | 57% | 0.924 |
| 0.99 | 47% | 0.950 |

**Agreement rises monotonically with the floor across the entire range**, from
0.807 to 0.950, while the auto-handled share falls from 100% to 47%. The two
lines cross, and the crossing point is the trade. That is the whole claim:
*the model's confidence predicts its accuracy*, so a threshold buys agreement
with volume in a way you can read off a chart and choose.

PRD §2 wanted this to turn "we have a nice amber lane" into "here is the
curve". It does.

### What the curve does not cover, stated on the screen

Agreement is against Civil Comments' human `toxicity` label, which is
overwhelmingly insult and abuse. That makes it a fair test of `hostility` and
`contempt` and **no test at all** of `substance` or `on_topic`, which need
thread context the labelled corpus does not have (§5, §9). The recorded sweep
therefore covers the two intrinsic axes only, and the caveat is printed above
the chart rather than buried here.

### One process note worth keeping

The chart's two colours were chosen by running the validator, not by eye. The
first pair — blue `#58a6ff` and purple `#bc8cff` — looked clearly distinct on
screen and scored a colourblind separation of **ΔE 2.7** under deuteranopia,
which is indistinguishable. They also failed the normal-vision floor at 13.2.
The shipped pair, `#388bfd` and `#db6d28`, scores 29.3 and 33.5.

Two colours that look fine and are not is exactly the kind of thing that
survives a review, so: run the check. The lane colours (green, red, amber) are
deliberately not reused for series here — they mean Approved, Bounced and Pen
everywhere else in the app, and overloading them would cost more than a fresh
hue does.

## 25. Offline replay: the same wall, for nothing

```
uv run python -m web.app --offline
```

6,492 comments judged, **0 errors, $0.0000, no key in the environment.** The
launch config for offline mode does not even pass `--env-file`.

This is the mitigation invariant 5 already applies to Reddit — pre-bake it and
the upstream cannot take the demo down — applied to the model. §18 is why it
exists: the credits ran out twice mid-build, and a wall where every card reads
"could not judge" demonstrates nothing. §19 is why it is the *right default*
for an ambient display: $0.41 a minute live, nothing from a recording.

### It fakes at the client boundary, not the pipeline

`OfflineClient` returns the same shape `system_one` does, so the batcher, the
rubric, the lane policy, the gate and the render loop are all the real ones.
Two consequences worth having:

- **Threshold and gate controls still work offline**, because retuning is a
  pure recompute over stored probabilities (§21). Verified: 29% auto-handled at
  floor 0.99, 96% at 0.55, with no model calls. So the most interesting control
  in the demo is free to demonstrate.
- **Allow / Bounce still works**, since it never needed the model.

### Every score on screen is real

The recording is 350 actual verdicts from `experiments/thread_probe.py`, baked
by `feed/bake.py` and committed. Nothing is synthesised or interpolated, and the
cost of that choice is coverage: only sampled comments have verdicts, so offline
mode streams those 350 and no others. A wall capped at 150 cards does not
notice.

The alternative — reusing recorded answers for unscored comments, keyed by hash
— would have given full coverage and a plausible-looking wall. It was rejected.
A fabricated fingerprint on screen is a lie told to an audience, and the whole
claim of this project is that it reports what it measured.

For the same reason, the two features that need a *new* answer refuse instead of
returning stale numbers: a rubric edit asks the model a question it has never
been asked, and so does a typed comment. Both say so and name the flag to drop.
A banner across the top says what is being replayed.

### What this unblocks

`TASKS.md` 2.3 — reading 30 penned comments cold to decide whether they are
genuinely hard — is PRD success criterion 4 and the one thing measurement cannot
settle. It now costs nothing to do, repeatedly, by anyone, with no key.

## 26. Demo readiness, and the route spec §9 did not know about

### The measured number, from the machine that will present

45 seconds live, drip 250/s:

```
9,552 judged in 45.0s = 212.3/sec     approved 81%  bounced 3%  pen 15%
639 requests, 6 errors (0.9%)         latency p50 275ms, p95 1,766ms
875 tokens/comment                    185,797 tokens/sec — 74% of the ceiling
$0.3512                               14.2 req/sec of the 20/sec budget
```

**Clears the PRD's 200/sec target on this machine and this network**, which is
what §9 of the spec asked to be verified rather than assumed. The six errors are
429s at three-quarters of the published token ceiling — the shape §16 predicts,
not a new problem.

### The exposure audit found the wrong route protected

Spec §9 says: per-IP rate limit on `/test` and a hard cap on total requests. Both
were in place. Costing the routes out shows `/test` is the *cheapest thing on the
list*:

| route | per click | was limited |
|---|---|---|
| `/tune` | free — **but it sets the burn rate** | no |
| `/rubric` | **~$0.027** (an 800-comment re-judge) | **no** |
| `/test` | ~$0.000034 | yes, 6/min |
| `/decide`, `/calibration` | free | n/a |

`/rubric` is **790× more expensive per click than `/test`** and had no limit at
all — it did not exist when spec §9 was written. It is now 2/min per IP and 30
per process run, and the limit applies always rather than only when exposed: an
accidental double-click costs money on a laptop too.

The larger exposure is subtler. **The drip slider spends no money itself and
decides how fast money leaves.** Anyone reaching the page can push it to 300/s
and walk away, which is ~$36/hour. `--public` clamps the ceiling to 60/s.

The general lesson: a security note written against one feature does not cover
the features added afterwards, and the cheapest route to protect is rarely the
one that got protected. Cost out every reachable route, not the one in the doc.

### Two dry runs

Back to back, cold starts, offline. The lane mix reproduces:

| | Approved | Bounced | Pen |
|---|---|---|---|
| run 1 | 72.8% | 3.4% | 23.7% |
| run 2 | 72.2% | 3.5% | 24.3% |

Within half a point — which is the "identical re-runs" property spec §5.3 wanted
from pre-baked data, and it matters because a demo gets given twice. The
absolute counts differ only because the two samples were taken at different
elapsed times; `Replay` also shuffles within a thread by default, since file
order front-loads top-level comments and makes the first minute
unrepresentative.

### The machine will sleep

10 minutes on mains, 4 on battery. That will interrupt a presentation, and it is
a system-wide setting rather than anything this project owns, so it is in
`DEMO.md` as a pre-flight step with the commands rather than changed silently.

## 27. §24's curve is mostly the base rate

Measured 2026-09-20 while writing `RESEARCH.md`, via `calibrate/baseline.py`.
It is the largest correction in this document and it lands on the project's
headline claim.

§24 reported agreement on the auto-handled subset rising monotonically with the
confidence floor — 0.807 to 0.950 — and read that as *the model's confidence
predicts its accuracy*. The first half is a fact. The second half does not
follow from it.

**Raising the floor does not only remove comments the model is unsure about. It
removes toxic ones.** The auto-handled subset goes from 30.0% toxic at floor
0.50 to 5.7% at 0.99. A subset that is 94% one class is trivially easy, and
agreement on it rises whether or not the model got better at anything.

The right comparison is what "approve everything" scores **on that same
subset** — an implementable strategy rather than an oracle, because the benign
class is the majority at every floor:

| floor | auto | toxic in subset | agreement | always-approve | **lift** | balanced acc | F1 | MCC |
|---|---|---|---|---|---|---|---|---|
| 0.50 | 100% | 30.0% | 0.807 | 0.700 | **+0.107** | 0.729 | 0.623 | 0.511 |
| 0.60 | 93% | 27.6% | 0.835 | 0.724 | **+0.111** | 0.737 | 0.635 | 0.558 |
| **0.65** | 89% | 25.8% | 0.850 | 0.742 | **+0.109** | **0.748** | **0.649** | **0.580** |
| 0.75 | 80% | 20.5% | 0.866 | 0.795 | +0.071 | 0.711 | 0.579 | 0.539 |
| **0.85** *(shipped)* | 70% | 16.3% | 0.876 | 0.837 | **+0.038** | 0.641 | 0.435 | 0.448 |
| 0.95 | 57% | 9.3% | 0.924 | 0.907 | +0.017 | 0.594 | 0.316 | 0.416 |
| 0.99 | 47% | 5.7% | 0.950 | 0.943 | **+0.007** | 0.562 | 0.222 | 0.345 |

Three readings:

1. **Lift collapses from +0.107 to +0.007.** At floor 0.99, where the agreement
   number looks best, the gate beats approving everything by seven thousandths.
2. **On base-rate-immune metrics the system gets worse above floor 0.65.**
   Balanced accuracy peaks at 0.748 and falls to 0.562; F1 peaks at 0.649 and
   falls to 0.222; MCC peaks at 0.580 and falls to 0.345. Past that point the
   gate is buying class balance, not skill.
3. **The signal is still real.** Lift is positive at every floor, MCC at the
   low end is 0.511, and balanced accuracy genuinely improves between 0.50 and
   0.65. Confidence carries information — roughly a third of what §24 implied.

### The threshold-free number

Ranking AUC over all 300, immune to both base rate and operating point:

| ranking signal | AUC |
|---|---|
| max P(level≥3) across both severity axes | **0.864** |
| `hostility` alone | 0.842 |
| `contempt` alone | 0.834 |
| max raw score | 0.848 |

**0.864 zero-shot, from eight hand-written sentences and no training data.**
That is the number to quote in both directions: strong for a system that saw no
labels, and the number a fine-tuned classifier should be expected to beat.

### What the operating point actually does

| at floor 0.85 | |
|---|---|
| specificity | **0.989** |
| recall | **0.294** |
| precision | 0.833 |

The system automates the benign majority very reliably and escalates 71% of the
actual toxicity. For a triage queue that is the correct shape — wrongly
bouncing someone is the expensive error, and precision on automated actions
keeps climbing past the lift peak (0.82 at 0.65, 1.00 at 0.90). It is the wrong
shape for any claim that begins "we automate moderation".

Recall is also partly a choice rather than a ceiling. Sweeping the *severity*
threshold instead of the confidence floor, F1 peaks at a far more permissive
rule than the one we ship — 0.696 at `P(over) > 0.2` against 0.623 at 0.5 — and
moving along that curve costs nothing, because the probabilities are already
stored (§21).

### What changed as a result

- `calibrate/sweep.py` reports `always`, `lift` and `balanced` on every row.
- **`knee()` was optimising the inflated metric.** It maximised raw agreement,
  which always picks the strictest floor available; it picked 0.95, at +0.017.
  It now maximises lift and picks 0.60. The shipped floor stays at 0.85 for the
  precision reason above, and the gap between the two is now visible rather
  than implied.
- The calibration tab draws the baseline as a dashed line **on the chart**, not
  as a footnote, because the shape of the chart alone reads as a better result
  than it is. Two paragraphs under it explain the gap and why 0.85 ships anyway.
- `README.md` and `RESEARCH.md` §5 carry the corrected table.

### The methodological lesson, which is §8's again

§8 recorded reporting a rubric change as a win on the strength of aggregate
rates that moved two comments out of 300. This is the same error one level up:
a metric that *looked* like it measured the model measured the dataset instead,
and it survived for two days and into three artefacts because the curve had the
shape we expected.

**Check what a trivial baseline scores on exactly the data you are scoring.**
It costs one column and it is the difference between a real result and a
restatement of the class balance.
