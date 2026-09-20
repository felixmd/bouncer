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
