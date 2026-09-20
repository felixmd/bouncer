"""The four axes, as plain-English Score rubrics.

Editable at runtime (PRD §4.2) — an axis is a title, a question, and an ordered
list of level descriptions. Nothing here knows about the model client.

Two things the SDK forces on the design:

- `Score.criteria` is an ordered list with one entry per level *starting at
  zero*, so N descriptions produce raw scores in 0..N-1. A "0-10 score" would
  need 11 levels, one past the documented maximum of 10. We use 5 levels and
  normalise onto 0-10 for policy and display, so the thresholds in `config` keep
  the scale the spec talks about.
- `ScoreAnswer.score` is the probability-weighted mean of the levels, so it is
  already continuous. Fine-grained rubrics buy resolution we get for free;
  fewer, sharply-distinguished levels are easier to write and easier to edit
  live, which is the actual demo.
"""

from dataclasses import dataclass, field

from typesafe_sdk import Score

import config


@dataclass(frozen=True)
class Axis:
    key: str
    question: str
    levels: list[str]
    higher_is_worse: bool
    needs_thread_context: bool

    def score_question(
        self, comment_id: str, text: str, addressing: str, parent: str | None = None
    ) -> Score:
        """A Score question scoped to one comment inside a batched request.

        `quoted` carries the comment in the question and leaves it out of the
        state entirely, so nothing has to be located by index and no other
        comment is present to act as a distractor. See config.ADDRESSING.

        The parent snippet has to travel in the question too, and not for the
        same reason. It *differs per comment*, so it cannot live in a state
        shared across fifteen of them. `needs_thread_context` decides which
        questions get it: `substance` and `on_topic` are judged relative to what
        came before, while `hostility` and `contempt` are intrinsic to the
        comment's own text and would only be reading a distractor.
        """
        if addressing == "index":
            instructions = f"For comment {comment_id}: {self.question}"
        else:
            preamble = ""
            if parent and self.needs_thread_context:
                preamble = f'It is replying to:\n"""\n{parent}\n"""\n'
            instructions = f'{preamble}The comment is:\n"""\n{text}\n"""\n{self.question}'
        return Score(instructions=instructions, criteria=list(self.levels))


# These levels are picked **per axis**, each from a paired head-to-head over
# 300 Hacker News comments and 300 labelled Civil Comments, all variants scored
# inside the same request (experiments/axis_ab.py, FINDINGS §11). A wholesale
# "v2" rewrite lost on two axes and won on two; the mix below is the winner of
# each contest, not a version.
#
# The rule the Score docs give — describe situations, not degrees — is real but
# it is not the whole story. `hostility` below is the *degree ladder*, and it
# beat two situational rewrites by 0.114 and 0.099. What actually predicts
# confidence is whether the levels form one unambiguous ordering: the
# situational rewrites each smuggled in a rung that was a different dimension
# rather than a lower degree, and probability split between it and its neighbour.

# WINNER by 0.114 over the situational rewrite, on both corpora, with no
# accuracy cost (10:12 and 9:8 on discordant comments — differently wrong, not
# better). The highest-confidence axis in the rubric at 0.824.
HOSTILITY = Axis(
    key="hostility",
    question="is it attacking a person?",
    levels=[
        "Addresses ideas, events or the situation only. No personal remarks about anyone.",
        "Mildly pointed at a person — a dig or a sarcastic aside, not an attack.",
        "Openly critical of a person's character, motives or intelligence, but not abusive.",
        "A direct personal attack: insults, name-calling, or open hostility toward someone.",
        "Sustained abuse of a person: degrading language, or wishing harm on them.",
    ],
    higher_is_worse=True,
    needs_thread_context=False,
)

# The one axis where confidence and accuracy disagreed, and the only judgement
# call in the set. These levels are 0.028 *less* confident than the incumbent
# (about 2 sigma, marginal) but meaningfully more accurate against the human
# label — 16:6 on the comments where the two disagree. Accuracy wins: 0.028 is
# noise next to hostility's 0.114, and being right is what the product is for.
# Revisit if Pen volume ever turns on this axis.
CONTEMPT = Axis(
    key="contempt",
    question="how is a differing view treated?",
    levels=[
        "No differing view is addressed.",
        "A differing view is answered on its merits.",
        "A differing view is called wrong, with no reason given.",
        "A differing view is called not worth discussing, or is refused engagement.",
        "The people who hold it are called stupid, dishonest, or not worth replying to.",
    ],
    higher_is_worse=True,
    needs_thread_context=False,
)

# WINNER by 0.045 over the degree-ladder phrasing. The best-behaved axis at
# 0.703, and the only one where the situational rewrite clearly paid.
SUBSTANCE = Axis(
    key="substance",
    question="what does it add to the discussion?",
    levels=[
        "The comment is a reaction, an agreement, a joke, or a single phrase.",
        "The comment states an opinion and stops there.",
        "The comment states an opinion and gives one reason or one concrete detail.",
        "The comment makes an argument with more than one step, or draws on personal "
        "experience.",
        "The comment introduces information, figures or sources not already in the thread.",
    ],
    higher_is_worse=False,
    needs_thread_context=True,
)

# Every level names the *same single referent*: the quoted text the comment is
# replying to. The first version said "the thread's subject" while the question
# supplied a parent comment, which left two competing referents in one question
# — the documented "measuring more than one thing" failure. It cost real
# accuracy: mean confidence 0.354 on a Hacker News thread, with several comments
# scored at 0.00, *worse* than the 0.50 measured on Civil Comments where there
# was no thread context at all. Adding context to an ambiguous question made it
# worse, not better.
#
# Callers pass the thread title as the referent for top-level comments, so there
# is always exactly one thing to be on topic *with*. This also matches spec §3.3
# — on_topic for a deep reply means relevant to its subthread, not to the
# article, and this thread runs fourteen levels deep.
ON_TOPIC = Axis(
    key="on_topic",
    question="how far does it engage with the text it is replying to?",
    levels=[
        "The comment does not engage with the quoted text; it is about something else.",
        "The comment takes a word or a side detail from the quoted text and goes elsewhere.",
        "The comment is about the same general subject as the quoted text.",
        "The comment answers the point the quoted text makes.",
        "The comment restates or quotes a specific claim from it and responds to that claim.",
    ],
    higher_is_worse=False,
    needs_thread_context=True,
)


@dataclass(frozen=True)
class Rubric:
    axes: list[Axis] = field(default_factory=lambda: [HOSTILITY, CONTEMPT, SUBSTANCE, ON_TOPIC])

    @property
    def keys(self) -> list[str]:
        return [axis.key for axis in self.axes]

    def questions_for(
        self,
        comment_id: str,
        text: str,
        parent: str | None = None,
        addressing: str = config.ADDRESSING,
    ) -> dict[str, Score]:
        """Question set for one comment, keyed `<comment_id>_<axis>`."""
        return {
            f"{comment_id}_{axis.key}": axis.score_question(
                comment_id, text, addressing, parent
            )
            for axis in self.axes
        }


DEFAULT_RUBRIC = Rubric()


def normalise(raw_score: float, levels: int = config.SCORE_RUBRIC_LEVELS) -> float:
    """Map a raw score in 0..levels-1 onto the 0-10 scale the thresholds use."""
    return raw_score * (config.SCORE_SCALE_MAX / (levels - 1))
