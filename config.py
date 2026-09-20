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
REQUESTS_PER_SECOND = 20.0
TOKEN_BUCKET_CAPACITY = 20
MAX_CONCURRENT_REQUESTS = 32

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

DRIP_RATE_PER_SECOND = 200  # UI slider
RENDER_TICK_HZ = 12  # fixed tick; never one WS frame per classification
MAX_VISIBLE_CARDS = 150  # hard DOM cap, evict from the tail
REJUDGE_WINDOW = 800  # backlog re-scored on a rubric edit (§6)

# --- Cost ------------------------------------------------------------------

INPUT_COST_PER_MTOK = 0.042  # output is free

# --- Paths -----------------------------------------------------------------

THREADS_DIR = "data/threads"
