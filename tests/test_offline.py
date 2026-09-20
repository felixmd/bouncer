"""Offline replay. No network, no key — which is the whole point of the module."""

import json

import pytest

from feed.replay import Comment, Thread, ThreadStore
from judge.offline import OfflineClient, restrict
from judge.rubric import DEFAULT_RUBRIC

AXES = ["hostility", "contempt", "substance", "on_topic"]


@pytest.fixture
def recording(tmp_path):
    payload = {
        "t1:c000": {
            axis: {
                "score": 1.2,
                "confidence": 0.9,
                "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
            }
            for axis in AXES
        }
    }
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


async def test_it_answers_from_the_recording(recording):
    client = OfflineClient(recording)
    questions = DEFAULT_RUBRIC.questions_for("t1:c000", "a body", "a parent")
    response, latency, error = await client.ask({}, questions)

    assert error is None
    assert latency > 0  # a plausible one, so the wall moves like the live one
    assert set(response.scores) == set(questions)
    assert response.scores["t1:c000_hostility"].confidence == 0.9


async def test_on_topic_keys_parse_despite_the_underscore(recording):
    """The bug that made every comment error: question keys are
    `{comment_id}_{axis}` and `on_topic` contains an underscore, so splitting on
    the last one yields "topic" and nothing ever matches."""
    client = OfflineClient(recording)
    response, _, _ = await client.ask(
        {}, DEFAULT_RUBRIC.questions_for("t1:c000", "a body")
    )
    assert "t1:c000_on_topic" in response.scores


async def test_probability_levels_come_back_as_integers(recording):
    """JSON keys are strings; the decision gate compares them to level numbers."""
    client = OfflineClient(recording)
    response, _, _ = await client.ask(
        {}, DEFAULT_RUBRIC.questions_for("t1:c000", "a body")
    )
    levels = response.scores["t1:c000_contempt"].probabilities
    assert all(isinstance(level, int) for level in levels)


async def test_an_unrecorded_comment_is_left_unanswered(recording):
    """The pipeline routes a missing answer to the Pen — the same path a real
    missing answer takes — so this degrades rather than inventing a score."""
    client = OfflineClient(recording)
    response, _, error = await client.ask(
        {}, DEFAULT_RUBRIC.questions_for("t1:c999", "never scored")
    )
    assert error is None
    assert response.scores == {}


async def test_it_never_reports_spend(recording):
    client = OfflineClient(recording)
    await client.ask({}, DEFAULT_RUBRIC.questions_for("t1:c000", "a body"))
    assert client.stats.input_tokens == 0
    assert client.stats.cost_usd == 0.0
    assert client.stats.errors == 0


def test_a_missing_recording_fails_loudly(tmp_path):
    with pytest.raises(SystemExit, match="feed.bake"):
        OfflineClient(tmp_path / "nope.json")


def comment(index: int, thread_id: str = "t1") -> Comment:
    return Comment(
        id=f"{thread_id}:c{index:03d}", thread_id=thread_id, body="body",
        parent_snippet=None, author_hash="amber-finch", depth=0, score=0,
    )


def test_restrict_keeps_only_recorded_comments():
    """Offline mode shows real model output or nothing — a comment we never
    scored is not shown at all."""
    thread = Thread(id="t1", title="t", selftext="", source="s")
    thread.comments = [comment(i) for i in range(5)]
    store = ThreadStore([thread])

    kept = restrict(store, {"t1:c001", "t1:c003"})
    assert kept == 2
    assert [c.id for c in store.by_id["t1"].comments] == ["t1:c001", "t1:c003"]


def test_restrict_drops_threads_left_empty():
    """An empty thread would make the batcher wait on a source that never
    produces, and the replay would stall on it every lap."""
    kept_thread = Thread(id="t1", title="t", selftext="", source="s")
    kept_thread.comments = [comment(0, "t1")]
    empty = Thread(id="t2", title="t", selftext="", source="s")
    empty.comments = [comment(0, "t2")]
    store = ThreadStore([kept_thread, empty])

    restrict(store, {"t1:c000"})
    assert [t.id for t in store.threads] == ["t1"]
    assert "t2" not in store.by_id
