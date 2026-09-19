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

    def score_question(self, comment_id: str) -> Score:
        """A Score question scoped to one comment inside a possibly-batched state."""
        return Score(
            instructions=f"For comment {comment_id}: {self.question}",
            criteria=list(self.levels),
        )


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

CONTEMPT = Axis(
    key="contempt",
    question="does it dismiss anyone who disagrees as not worth answering?",
    levels=[
        "Treats disagreement as legitimate, or does not engage with opposing views at all.",
        "Confident and one-sided, but leaves room for the other view to exist.",
        "Implies people who disagree are uninformed or have not thought it through.",
        "States or strongly implies that opposing views are not worth engaging with.",
        "Writes off everyone who disagrees as stupid, bad faith, or beneath response.",
    ],
    higher_is_worse=True,
    needs_thread_context=False,
)

SUBSTANCE = Axis(
    key="substance",
    question="does it add anything to the discussion?",
    levels=[
        "Adds nothing: a bare reaction, an agreement, a meme, or noise.",
        "States a position with no reasoning, evidence or detail behind it.",
        "Gives a brief reason, or one concrete detail.",
        "Makes a clear argument with reasoning, an example, or relevant experience.",
        "Adds substantial information, evidence, or a developed argument.",
    ],
    higher_is_worse=False,
    needs_thread_context=True,
)

ON_TOPIC = Axis(
    key="on_topic",
    question="does it engage with what it is replying to?",
    levels=[
        "Unrelated to the thread and to the comment it replies to.",
        "Loosely connected — uses the thread as a springboard for something else.",
        "Related to the general subject but not to the specific point being made.",
        "Engages with the thread's question, or with the parent comment's point.",
        "Responds directly and specifically to what it is replying to.",
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

    def questions_for(self, comment_id: str) -> dict[str, Score]:
        """Question set for one comment, keyed `<comment_id>_<axis>`."""
        return {f"{comment_id}_{axis.key}": axis.score_question(comment_id) for axis in self.axes}


DEFAULT_RUBRIC = Rubric()


def normalise(raw_score: float, levels: int = config.SCORE_RUBRIC_LEVELS) -> float:
    """Map a raw score in 0..levels-1 onto the 0-10 scale the thresholds use."""
    return raw_score * (config.SCORE_SCALE_MAX / (levels - 1))
