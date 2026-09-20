"""Reddit tree parsing and URL handling. No network, no credentials.

The live path cannot be tested here — Reddit 403s unauthenticated requests from
this network and the credentials are not in CI. So this exercises everything
*except* the HTTP call, against a payload shaped like Reddit's, which is where
the bugs would be anyway. Anonymisation is tested in `test_store.py`.
"""

import pytest

from feed.reddit_fetch import thread_path, walk


def comment(author, body, replies=None, score=1, controversiality=0):
    """A `t1` node shaped like Reddit's listing JSON."""
    data = {
        "author": author, "body": body,
        "score": score, "controversiality": controversiality,
    }
    # Reddit sends an empty string, not null, when there are no replies.
    data["replies"] = {"data": {"children": replies}} if replies else ""
    return {"kind": "t1", "data": data}


TREE = [
    comment("alice", "Top level from alice.", controversiality=1, replies=[
        comment("bob", "Bob replies."),
        comment("[deleted]", "[removed]", replies=[
            comment("carol", "Carol replies under a removed comment."),
        ]),
    ]),
    comment("AutoModerator", "Please read the rules."),
    comment("alice", "Alice again, second top-level."),
    {"kind": "more", "data": {"count": 47}},
]


@pytest.fixture
def walked():
    return walk(TREE)


@pytest.fixture
def by_body(walked):
    return {c.body: c for c in walked}


def test_skips_automod_removed_bodies_and_more_stubs(walked):
    bodies = [c.body for c in walked]
    assert "Please read the rules." not in bodies
    assert "[removed]" not in bodies
    assert len(walked) == 4  # alice, bob, carol, alice-again


def test_depth_tracks_nesting(by_body):
    assert by_body["Top level from alice."].depth == 0
    assert by_body["Bob replies."].depth == 1
    assert by_body["Carol replies under a removed comment."].depth == 2


def test_top_level_comments_have_no_parent(by_body):
    assert by_body["Top level from alice."].parent_body is None


def test_reply_carries_one_level_of_parent_text(by_body):
    assert by_body["Bob replies."].parent_body == "Top level from alice."


def test_a_dropped_parent_passes_the_grandparent_not_a_hole(by_body):
    """Carol's parent was removed. `on_topic` is scored against this string, and
    inventing context that was not there is worse than reaching past it."""
    assert by_body["Carol replies under a removed comment."].parent_body == (
        "Top level from alice."
    )


def test_controversiality_and_score_survive(by_body):
    assert by_body["Top level from alice."].controversiality == 1
    assert by_body["Bob replies."].score == 1


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.reddit.com/r/news/comments/abc123/some_slug/",
        "https://old.reddit.com/r/news/comments/abc123/some_slug",
        "https://np.reddit.com/r/news/comments/abc123/some_slug/?sort=top",
        "https://reddit.com/r/news/comments/abc123/some_slug/#comment",
        "/r/news/comments/abc123/some_slug",
    ],
)
def test_permalink_forms_normalise_to_one_path(raw):
    assert thread_path(raw) == "/r/news/comments/abc123/some_slug"


def test_a_non_thread_url_is_rejected():
    with pytest.raises(SystemExit):
        thread_path("https://www.reddit.com/r/news/")
