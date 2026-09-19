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

# UNSET ON PURPOSE. Run the batch-size-vs-accuracy experiment in
# TECHNICAL_SPEC §3.2 before picking a value. Prior guess is 8-15, but the
# knee is unmeasured and the throughput story depends on it.
BATCH_SIZE: int | None = None

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
CONFIDENCE_FLOOR = 0.80  # provisional; set from the Jigsaw sweep, not by taste
LOW_SUBSTANCE_THRESHOLD = 3.0
LOW_ON_TOPIC_THRESHOLD = 3.0

# --- Replay and render -----------------------------------------------------

DRIP_RATE_PER_SECOND = 200  # UI slider
RENDER_TICK_HZ = 12  # fixed tick; never one WS frame per classification
MAX_VISIBLE_CARDS = 150  # hard DOM cap, evict from the tail
REJUDGE_WINDOW = 800  # backlog re-scored on a rubric edit (§6)

# --- Cost ------------------------------------------------------------------

INPUT_COST_PER_MTOK = 0.042  # output is free

# --- Paths -----------------------------------------------------------------

THREADS_DIR = "data/threads"
