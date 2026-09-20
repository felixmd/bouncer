"""Hacker News tree parsing and HTML cleanup. No network."""

import pytest

from feed.hn_fetch import clean, walk


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<p>Two<p>paragraphs", "Two\n\nparagraphs"),
        ("it&#x27;s &amp; &quot;quoted&quot;", "it's & \"quoted\""),
        ('<a href="http://x.test" rel="nofollow">x.test</a>', "x.test"),
        ("<i>emphasis</i> and <code>code()</code>", "emphasis and code()"),
        (None, ""),
        ("", ""),
    ],
)
def test_html_fragments_become_plain_text(raw, expected):
    assert clean(raw) == expected


def node(author, text, children=None, points=None):
    return {
        "author": author, "text": text, "points": points,
        "children": children or [],
    }


TREE = [
    node("alice", "<p>Top level.", points=12, children=[
        node("bob", "<p>A reply."),
        # Deleted comments keep their children but carry no author or text.
        node(None, None, children=[node("carol", "<p>Under a deleted parent.")]),
    ]),
]


@pytest.fixture
def by_body():
    return {c.body: c for c in walk(TREE)}


def test_deleted_nodes_are_dropped_but_their_children_survive(by_body):
    assert len(by_body) == 3
    assert "Under a deleted parent." in by_body


def test_depth_counts_the_deleted_node(by_body):
    """The node is gone from the output but it was still a level of nesting."""
    assert by_body["Top level."].depth == 0
    assert by_body["A reply."].depth == 1
    assert by_body["Under a deleted parent."].depth == 2


def test_a_deleted_parent_passes_the_grandparent(by_body):
    assert by_body["Under a deleted parent."].parent_body == "Top level."


def test_points_are_carried_when_present(by_body):
    assert by_body["Top level."].score == 12
    assert by_body["A reply."].score == 0  # HN hides per-comment points; default cleanly
