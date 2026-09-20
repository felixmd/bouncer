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

    def score_question(self, comment_id: str, text: str, addressing: str) -> Score:
        """A Score question scoped to one comment inside a batched request.

        `quoted` carries the comment in the question and leaves it out of the
        state entirely, so nothing has to be located by index and no other
        comment is present to act as a distractor. See config.ADDRESSING.
        """
        if addressing == "index":
            instructions = f"For comment {comment_id}: {self.question}"
        else:
            instructions = f'The comment is:\n"""\n{text}\n"""\n{self.question}'
        return Score(instructions=instructions, criteria=list(self.levels))


# Level descriptions name *situations*, not degrees. That is the Score docs'
# instruction and it is not cosmetic: the first version of this rubric was a
# degree ladder ("mildly pointed", "openly critical", "sustained abuse") and its
# contempt level 0 read "treats disagreement as legitimate, *or* does not engage
# with opposing views at all" — two situations in one level. Two of the three
# documented causes of low confidence, in one rubric. Rewriting to situations
# raised hostility recall from 0.43 to 0.57 and on_topic confidence from 0.34 to
# 0.50 in experiments/rubric_ab.py.

HOSTILITY = Axis(
    key="hostility",
    question="is it attacking a person?",
    levels=[
        "The comment is about a topic, an event, a policy or an idea. No person is described.",
        "The comment reports what a person did or said, without judging them for it.",
        "The comment says a person is wrong, mistaken, hypocritical, or behaving badly.",
        "The comment calls a person a name, mocks them, or says what kind of person they are.",
        "The comment swears at a person, uses degrading language about them, or says they "
        "deserve harm.",
    ],
    higher_is_worse=True,
    needs_thread_context=False,
)

CONTEMPT = Axis(
    key="contempt",
    question="how does it treat people who hold the opposing view?",
    levels=[
        "The comment does not refer to anyone who disagrees.",
        "The comment answers an opposing argument on its merits.",
        "The comment says the opposing side is wrong without saying why.",
        "The comment says the opposing argument is not worth discussing, or refuses to engage "
        "with it.",
        "The comment says the people who hold the opposing view are stupid, dishonest, or "
        "beneath replying to.",
    ],
    higher_is_worse=True,
    needs_thread_context=False,
)

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

ON_TOPIC = Axis(
    key="on_topic",
    question="what is it responding to?",
    levels=[
        "The comment is about a different subject entirely.",
        "The comment starts from the thread's subject and moves on to a different one.",
        "The comment is about the thread's general subject.",
        "The comment addresses the specific question or claim the thread is about.",
        "The comment restates or quotes a specific point and responds to that point.",
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
        self, comment_id: str, text: str, addressing: str = config.ADDRESSING
    ) -> dict[str, Score]:
        """Question set for one comment, keyed `<comment_id>_<axis>`."""
        return {
            f"{comment_id}_{axis.key}": axis.score_question(comment_id, text, addressing)
            for axis in self.axes
        }


DEFAULT_RUBRIC = Rubric()


def normalise(raw_score: float, levels: int = config.SCORE_RUBRIC_LEVELS) -> float:
    """Map a raw score in 0..levels-1 onto the 0-10 scale the thresholds use."""
    return raw_score * (config.SCORE_SCALE_MAX / (levels - 1))
