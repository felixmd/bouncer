"""Single home for every tunable constant. No magic numbers in logic.

Anything here that a viewer might want to move during a demo should also be
surfaced in the UI (see PRD §4.2).
"""

# --- Jev model -------------------------------------------------------------

# Pin the version. Never "jev-latest" — it moves, and it will move under
# thresholds tuned against it. Log the `model` field from the response, not this.
#
# The SDK's own default is "jev-latest" (typesafe_sdk.constants.DEFAULT_MODEL),
# which is the moving target this must not be. Resolved from the `model` field
# of a live response during the 15-comment smoke run on 2026-09-19.
JEV_MODEL = "jev-1.13.0"

API_TIMEOUT_S = 30
MAX_RETRIES = 3

# --- Rate limiting (TECHNICAL_SPEC §3.1) -----------------------------------

# Published limit is 1,200 req/min. Hold under it with an explicit token
# bucket — the semaphore alone will overshoot when latency drops.
#
# **Capacity matters as much as rate, and it is the part that is easy to get
# wrong.** A capacity equal to the per-second rate lets a full second of
# requests leave in one instant. Measured: at rate 20 / capacity 20 the
# pipeline averaged 16 req/s — comfortably under budget — and still collected
# 41 rate-limit errors in 25 seconds, because the server sees the burst, not
# the average. A small capacity paces requests out evenly instead.
#
# The rate carries 10% headroom under the published 20/s for the same reason.
REQUESTS_PER_SECOND = 18.0
TOKEN_BUCKET_CAPACITY = 4

# Bounds memory and sockets. It is deliberately *not* the rate limiter — at
# 300ms latency a semaphore of 32 would happily issue 100 req/s.
MAX_CONCURRENT_REQUESTS = 32

# How many requests are actually in flight, since one worker holds one request.
# TECHNICAL_SPEC §3.1 reasoned that ~10 would sustain 20 req/s and that "a
# semaphore of 32 is ample". Ample is not the risk — measured over 30 seconds:
#
#   workers   throughput   429s   p95 latency
#         8       225/s       3        1,291ms
#        24       245/s      44        3,542ms
#
# More concurrency buys no throughput and costs errors, because the ceiling is
# elsewhere (see the token note below). Worse, every failed request pens 15
# comments, so the Pen filled with failures rather than uncertainty — 22%
# against 14% — which is the one thing this demo cannot afford.
PIPELINE_WORKERS = 8

# --- Batching --------------------------------------------------------------

# Measured, not guessed. experiments/batch_sweep.py scored 300 labelled Civil
# Comments at B = 1,3,5,8,12,15,20,30 and found no accuracy knee anywhere in
# that range — agreement with human labels was 0.78 at B=1 and 0.79 at B=30.
# 15 is chosen for headroom against the 64K state+questions ceiling (B=30 with
# four questions each is ~120 questions in one request), not for accuracy.
BATCH_SIZE = 15

# How a question points at one comment inside a batched request.
#   "index"   comments live in the state, the question says "for comment X".
#   "quoted"  the state carries no comments at all; each question carries only
#             the comment it asks about.
# "quoted" won on every measured axis: higher confidence, higher recall, higher
# agreement. Both jev-1.13 failure modes it avoids are documented — the model
# "cannot reliably count items, with error growing with the size of the thing
# being counted", and "accuracy falls as the state grows with content unrelated
# to the decision". At B=15, thirteen of the other comments are exactly that
# unrelated content.
ADDRESSING = "quoted"

# --- Scoring scale ---------------------------------------------------------

# Score.criteria is one description per level *starting at zero*, so N levels
# give raw scores in 0..N-1. The spec's "0-10 Score" would need 11 levels, one
# past the documented maximum of 10. Five levels are what a human can actually
# write and edit live; judge.rubric.normalise maps them onto 0-10 so the
# thresholds below stay on the scale the spec and the UI talk about.
SCORE_RUBRIC_LEVELS = 5
SCORE_SCALE_MAX = 10.0

# --- Lane policy (TECHNICAL_SPEC §4) ---------------------------------------

SEVERITY_THRESHOLD = 6.0  # on the normalised 0-10 scale
LOW_SUBSTANCE_THRESHOLD = 3.0
LOW_ON_TOPIC_THRESHOLD = 3.0

# The rubric level at which an axis crosses the line, used by the decision gate.
# Level 3 of 0..4 is the first rung the rubric describes as over the line — "a
# direct personal attack", "not worth discussing". Levels 0 and 1 are the
# "adds nothing" end of the quality axes.
#
# Thresholding the *mean* at a level boundary instead of at 6.0 was tested and
# made no difference: 3:2 on five discordant comments out of 300, agreement
# 0.810 against 0.807. The docs' warning that score levels are "weak in
# numerical calibration" is real but does not bite at this threshold, so
# SEVERITY_THRESHOLD stays where it is and these constants are used only by the
# gate. See FINDINGS §13.
OVER_AT_LEVEL = 3
LOW_AT_LEVEL = 1

# Which confidences the floor applies to.
#   min_all   the original rule. Takes the worst of four axes and pens 99% of
#             traffic — arithmetic rather than caution.
#   decisive  scalar confidence, on the axes that carried the verdict.
#   decision  probability mass on one side of the line, on the same axes.
#
# `decision` is not more discriminative than `decisive` — at matched volume the
# two agree with human labels within ±0.03 either way. It is adopted because it
# measures the quantity the lane actually turns on, which makes the floor mean
# something a person can read.
CONFIDENCE_GATE = "decision"

# Under `decision`, 0.85 means "at least 85% of the probability mass is on one
# side of the line". On the Hacker News thread that auto-handles 83% and pens
# 17%; on labelled Civil Comments the auto-handled share agrees with the human
# label 0.876 of the time.
#
# This is an operating point, not a discovery — the same trade was always
# available from the scalar gate at a floor near 0.30. PRD §6.1 is the reason it
# is exposed as a slider with the auto-handled share live beside it rather than
# baked in as a claim.
CONFIDENCE_FLOOR = 0.85

# --- Replay and render -----------------------------------------------------

# The ambient rate, and the cost dial — see FINDINGS §19. 205/s is $24.80/hour;
# 60/s is $7.25 and still fills the wall, because the render cap only shows
# ~96 cards/sec anyway. PRD §6's "≥200 items/sec sustained" is a claim about
# *capability*, which is measured and recorded; it does not have to be burning
# money while nobody is asking. The slider goes to 300 for the moment someone
# does.
DRIP_RATE_PER_SECOND = 60
DRIP_RATE_MAX = 300
RENDER_TICK_HZ = 12  # fixed tick; never one WS frame per classification
MAX_VISIBLE_CARDS = 150  # hard DOM cap, evict from the tail

# Cards *shown* per lane per frame. At 225 items/sec and 12Hz a frame carries
# ~19 verdicts, which against a 150-card cap means the browser inserting and
# evicting 225 nodes a second. The counters carry the truth; the wall is a
# visual, and a wall moving at 96 cards/sec already reads as a torrent.
#
# The Pen is exempt and always shows every card. Sampling Approved is a
# rendering choice; sampling the Pen would mean a comment nobody can click,
# and the Pen is the product.
CARDS_PER_FRAME = 8
REJUDGE_WINDOW = 800  # backlog re-scored on a rubric edit (§6)

# A partial batch is flushed after this long rather than waiting for B comments.
# Without it the pipeline stalls whenever the drip rate is low: at 5 items/sec a
# batch of 15 takes three seconds to fill, and the demo looks frozen.
BATCH_TIMEOUT_S = 0.4

# Bounded so that a drip rate above what the workers can clear applies
# backpressure to the replay reader instead of growing a queue until the
# process dies. Sized for roughly a second of traffic at full rate.
IN_QUEUE_MAX = 400
OUT_QUEUE_MAX = 2000

# Judged comments kept in memory for the rubric-edit re-sort (§6). Raw text
# lives in the ThreadStore, so this holds verdicts only.
BACKLOG_SIZE = 2000

# --- Cost and the real throughput ceiling ----------------------------------

INPUT_COST_PER_MTOK = 0.042  # output is free
TOKENS_PER_SECOND_CEILING = 250_000  # published

# **We are token-limited, not request-limited** — the opposite of what
# TECHNICAL_SPEC §3.1 concluded, and the reason throughput plateaus at ~240/s
# however many workers are added.
#
# The spec estimated 2,400 tokens per request (300 of context plus 15 x 140).
# Measured at B=15 it is ~12,000, because each comment carries four questions
# and each question restates the full rubric — roughly 1,400 tokens of level
# descriptions per comment, dwarfing the comment itself. At ~800 tokens per
# comment the 250K/sec ceiling caps throughput near 300 items/sec, which is
# within noise of the 20 req/s x B=15 request ceiling. Both bind at once, so
# raising B buys nothing.
#
# The lever, if more throughput is ever needed, is shorter rubric levels rather
# than bigger batches — and that trades directly against confidence.
INPUT_COST_PER_MTOK_NOTE = "see FINDINGS §16"

# --- Test-your-own-comment -------------------------------------------------

# Spec §9: the API key is on the laptop, and an open text box pointed at a paid
# API is the kind of thing that gets found. One comment costs ~$0.000025, so
# these caps are about abuse rather than about the cost of honest use.
TEST_PER_IP_PER_MINUTE = 6
TEST_TOTAL_CAP = 400  # per process run

# A typed comment has no thread, and `on_topic` scored against nothing is a
# documented cause of low confidence (FINDINGS §9) — it would pen every test
# comment for a reason that has nothing to do with the comment. The box takes
# optional context; this is the referent when none is given.
TEST_DEFAULT_CONTEXT = "An open comment thread, with no specific topic."

# --- Paths -----------------------------------------------------------------

THREADS_DIR = "data/threads"
