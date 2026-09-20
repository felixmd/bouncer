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

Tested head to head at B=15, same rubric:

| | mean conf | mean min-conf | hostility recall | lane agreement |
|---|---|---|---|---|
| index | 0.609 | 0.259 | 0.37 | 0.763 |
| **quoted** | **0.626** | **0.284** | **0.43** | **0.793** |

`quoted` puts the comment text in its own question and leaves the state holding
only the thread context. Nothing has to be located, and no other comment is
present to distract. It won on every measure, and it does **not** cost
throughput — still 15 comments per request. It costs about 45% more tokens, and
we are request-limited, not token-limited.

**Decision:** `ADDRESSING = "quoted"`.

## 3. The rubric was the real constraint, not the model

The first rubric was a degree ladder — "mildly pointed", "openly critical",
"sustained abuse" — and its contempt level 0 read *"treats disagreement as
legitimate, **or** does not engage with opposing views at all"*: two situations
in one level.

The Score docs name three causes of low confidence — the levels overlap for this
state, the question measures more than one thing, the state doesn't say enough —
and give one instruction: **describe situations, not degrees**. The v1 rubric
committed two of the three sins.

Rewriting every level as a situation (v2, now in `judge/rubric.py`):

| | hostility recall | on_topic confidence | lane agreement |
|---|---|---|---|
| v1 / quoted | 0.43 | 0.335 | 0.793 |
| **v2 / quoted** | **0.57** | **0.500** | **0.800** |

Recall improved by a third. Hostility precision fell from 0.85 to 0.74, which is
the trade — v2 reaches further and is wrong more often when it does. For a
product whose uncertain cases go to a human, that is the right direction.

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
  careful writing to beat v1 on recall while losing precision; a viewer editing a
  level mid-demo may well make it worse, and the demo should probably be honest
  that this is what tuning looks like.
