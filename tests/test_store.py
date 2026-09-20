"""Anonymisation and schema shaping, shared by every fetcher.

This is where invariant 8 is actually enforced, so it is tested here once rather
than once per source — a new fetcher cannot forget it.
"""

from feed.store import RawComment, build_document, display_name, scrub_text


def test_distinct_authors_never_collide():
    """Two people sharing a name would read as one person talking to themselves."""
    taken: dict[str, str] = {}
    names = {display_name(f"user{i}", "t3_abc", taken) for i in range(400)}
    assert len(names) == 400


def test_the_name_space_survives_a_thread_bigger_than_it_is():
    """A 3,859-comment HN thread has more distinct authors than there are
    two-word combinations, and it used to raise."""
    taken: dict[str, str] = {}
    names = {display_name(f"user{i}", "t", taken) for i in range(1500)}
    assert len(names) == 1500


def test_the_same_handle_differs_across_threads():
    """Salting by thread id keeps a person unlinkable between two data files."""
    assert display_name("alice", "t3_aaa", {}) != display_name("alice", "t3_bbb", {})


def test_display_name_is_deterministic():
    assert display_name("alice", "t3_abc", {}) == display_name("alice", "t3_abc", {})


def test_reddit_style_mentions_are_rewritten():
    taken: dict[str, str] = {}
    alice = display_name("alice", "t3_abc", taken)
    out = scrub_text("u/alice is right, unlike /u/bob", "t3_abc", taken, set())
    assert "alice" not in out and "bob" not in out
    assert alice in out  # one person reads consistently across the thread


def test_bare_handles_are_rewritten_when_the_person_posted_here():
    """Hacker News has no mention syntax, so a known author handle is the signal."""
    taken: dict[str, str] = {}
    name = display_name("tptacek", "hn1", taken)
    out = scrub_text("tptacek is wrong about this", "hn1", taken, {"tptacek"})
    assert "tptacek" not in out
    assert out.startswith(name)


def test_handle_matching_is_case_insensitive():
    """A real leak: a thread about HN moderation said "Fuck Dang" while the
    handle is `dang`, and a case-sensitive match left it in place."""
    taken: dict[str, str] = {}
    out = scrub_text("Dang's comments, and honestly, fuck Dang", "hn1", taken, {"dang"})
    assert "Dang" not in out


def test_links_to_a_comment_or_profile_are_neutralised():
    """A permalink identifies its author as surely as naming them — same reason
    real comment ids are never stored."""
    for url in (
        "https://news.ycombinator.com/item?id=47342616",
        "https://news.ycombinator.com/user?id=someone",
        "https://www.reddit.com/r/news/comments/abc123/slug/",
        "https://reddit.com/u/someone",
    ):
        assert scrub_text(f"see {url} for it", "t", {}, set()) == "see [link] for it"


def test_links_to_outside_articles_survive():
    """They are context, and they identify nobody."""
    text = "the paper is at https://arxiv.org/abs/2512.03389 if you want it"
    assert scrub_text(text, "t", {}, set()) == text


def test_scrubbing_leaves_urls_subreddits_and_ordinary_words_alone():
    text = "cross-posted from r/news, see http://x.test/u/path and you/us"
    assert scrub_text(text, "t3_abc", {}, set()) == text


TOP = "Top level, alice speaking, at some length."
REPLY = "alice is wrong about all of this, frankly."
THREAD = [
    RawComment(author="alice", body=TOP, parent_body=None, depth=0),
    RawComment(author="bob", body=REPLY, parent_body=TOP, depth=1, score=4),
    RawComment(author="alice", body="No u/bob, you are the wrong one here.",
               parent_body=REPLY, depth=2),
    RawComment(author="carol", body="short", parent_body=None, depth=0),  # below min_chars
]


def build():
    return build_document(
        thread_id="t3_abc", title="A thread by alice", selftext="alice wrote this",
        source="r/test", origin="https://example.test/x", comments=THREAD,
    )


def test_no_real_handle_survives_anywhere_in_the_document():
    """Invariant 8, end to end: authors, bodies, parent snippets, title, selftext."""
    document, _ = build()
    blob = str(document)
    for handle in ("alice", "bob", "carol"):
        assert handle not in blob


def test_a_mentioned_author_gets_the_same_name_as_their_own_comments():
    document, _ = build()
    by_id = {c["id"]: c for c in document["comments"]}
    bob_name = by_id["c001"]["author_hash"]
    assert bob_name in by_id["c002"]["body"]


def test_short_comments_are_dropped_and_ids_are_reassigned_densely():
    document, stats = build()
    assert stats["kept"] == 3  # "short" is below min_chars
    assert [c["id"] for c in document["comments"]] == ["c000", "c001", "c002"]


def test_schema_matches_the_spec():
    document, _ = build()
    assert set(document) == {"thread", "comments"}
    assert {"id", "title", "selftext", "fetched_at", "source", "origin"} <= set(document["thread"])
    assert set(document["comments"][0]) == {
        "id", "author_hash", "body", "parent_snippet", "depth", "score", "controversiality",
    }


def test_parent_snippet_is_truncated():
    long_parent = "x" * 500
    document, _ = build_document(
        thread_id="t", title="t", selftext="", source="s", origin="o",
        comments=[RawComment(author="a", body="a reply that is long enough to keep",
                             parent_body=long_parent, depth=1)],
    )
    assert len(document["comments"][0]["parent_snippet"]) == 200
