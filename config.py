"""Single home for every tunable constant. No magic numbers in logic.

Anything here that a viewer might want to move during a demo should also be
surfaced in the UI (see PRD §4.2).
"""

# --- Jev model -------------------------------------------------------------

# Pin the version. Never "jev-latest" — it moves, and it will move under
# thresholds tuned against it. Log the `model` field from the response, not this.
#
# UNSET ON PURPOSE. The SDK's own default is "jev-latest" (see
# typesafe_sdk.constants.DEFAULT_MODEL), so leaving this None would silently
# inherit the moving target. Resolve the concrete version once, with a key set:
#
#     uv run python -c "from typesafe_sdk import TypeSafeClient; \
#         print([m.id for m in TypeSafeClient().models.list().data])"
JEV_MODEL: str | None = None

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

# --- Lane policy (TECHNICAL_SPEC §4) ---------------------------------------

SEVERITY_THRESHOLD = 6.0  # on a 0-10 Score
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
