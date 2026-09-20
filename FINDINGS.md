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
