"""Parsing and hashing for the dev-time fetch. No network, no credentials.

The live path cannot be tested here — Reddit 403s unauthenticated requests from
this network and the credentials are not in CI. So this exercises everything
*except* the HTTP call, against a payload shaped like Reddit's, which is where
the bugs would be anyway.
"""

import pytest

from feed.reddit_fetch import display_name, scrub_mentions, thread_path, walk


def comment(author, body, replies=None, score=1, controversiality=0):
    """A `t1` node shaped like Reddit's listing JSON."""
    data = {
        "author": author,
        "body": body,
        "score": score,
        "controversiality": controversiality,
    }
    if replies:
        data["replies"] = {"data": {"children": replies}}
    else:
        data["replies"] = ""  # Reddit sends an empty string, not null
    return {"kind": "t1", "data": data}


TREE = [
    comment("alice", "Top level from alice.", controversiality=1, replies=[
        comment("bob", "Bob replies to alice."),
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
    return walk(TREE, "t3_abc", {})


def test_skips_automod_removed_bodies_and_more_stubs(walked):
    bodies = [c["body"] for c in walked]
    assert "Please read the rules." not in bodies
    assert "[removed]" not in bodies
    assert len(walked) == 4  # alice, bob, carol, alice-again


def test_depth_tracks_nesting(walked):
    by_body = {c["body"]: c for c in walked}
    assert by_body["Top level from alice."]["depth"] == 0
    assert by_body["Bob replies to alice."]["depth"] == 1
    assert by_body["Carol replies under a removed comment."]["depth"] == 2


def test_top_level_comments_have_no_parent_snippet(walked):
    by_body = {c["body"]: c for c in walked}
    assert by_body["Top level from alice."]["parent_snippet"] is None


def test_reply_carries_one_level_of_parent_text(walked):
    by_body = {c["body"]: c for c in walked}
    assert by_body["Bob replies to alice."]["parent_snippet"] == "Top level from alice."


def test_a_dropped_parent_passes_the_grandparent_not_a_hole(walked):
    """Carol's parent was removed. Inventing context that was not there would be
    worse than reaching past it — on_topic is judged against this string."""
    by_body = {c["body"]: c for c in walked}
    assert by_body["Carol replies under a removed comment."]["parent_snippet"] == (
        "Top level from alice."
    )


def test_controversiality_is_preserved(walked):
    by_body = {c["body"]: c for c in walked}
    assert by_body["Top level from alice."]["controversiality"] == 1


def test_no_real_handle_survives_as_an_author(walked):
    """Invariant 8. The point of the whole hashing exercise."""
    assert all("author" not in c for c in walked)
    hashes = {c["author_hash"] for c in walked}
    assert not hashes & {"alice", "bob", "carol", "[deleted]"}
    assert all(h.count("-") == 1 for h in hashes)


def test_same_author_gets_one_name_within_a_thread(walked):
    by_body = {c["body"]: c for c in walked}
    assert (
        by_body["Top level from alice."]["author_hash"]
        == by_body["Alice again, second top-level."]["author_hash"]
    )
    assert by_body["Bob replies to alice."]["author_hash"] != (
        by_body["Top level from alice."]["author_hash"]
    )


def test_u_slash_mentions_in_the_body_are_rewritten():
    """Hashing the author field is not enough — people name each other in text,
    and that string would go on screen under the word "toxic"."""
    taken: dict[str, str] = {}
    alice = display_name("alice", "t3_abc", taken)
    out = scrub_mentions("u/alice is right, unlike /u/bob", "t3_abc", taken)
    assert "alice" not in out
    assert "bob" not in out
    assert alice in out  # the same person reads consistently across the thread


def test_scrubbing_leaves_subreddits_and_ordinary_words_alone():
    taken: dict[str, str] = {}
    text = "cross-posted from r/news, see http://x.test/u/path and you/us"
    assert scrub_mentions(text, "t3_abc", taken) == text


def test_distinct_authors_never_collide():
    """Two people sharing a name would read as one person talking to themselves."""
    taken: dict[str, str] = {}
    names = {display_name(f"user{i}", "t3_abc", taken) for i in range(400)}
    assert len(names) == 400


def test_the_same_handle_differs_across_threads():
    """Salting by thread id keeps a person unlinkable between two data files."""
    a = display_name("alice", "t3_aaa", {})
    b = display_name("alice", "t3_bbb", {})
    assert a != b


def test_display_name_is_deterministic():
    assert display_name("alice", "t3_abc", {}) == display_name("alice", "t3_abc", {})


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
