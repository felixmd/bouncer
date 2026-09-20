"""Shared by every dev-time fetcher: anonymise, shape, write.

Reddit blocked us (see `feed/reddit_fetch.py`), so the demo now takes threads
from several places. Where a thread came from is an implementation detail of the
fetch step — the app only ever reads `data/threads/*.json` in one schema, and
invariants 5 and 8 hold identically whichever source produced the file.

Every fetcher walks its own tree into `RawComment`s and hands them here.
Anonymisation happens in exactly one place so a new source cannot forget it.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import config

PARENT_SNIPPET_CHARS = 200

# Two-word names: enough combinations that collisions are rare, short enough to
# read on a card at a glance.
ADJECTIVES = [
    "amber", "ash", "azure", "bronze", "clay", "cobalt", "copper", "coral",
    "dusk", "ember", "flint", "frost", "glass", "hazel", "indigo", "iron",
    "ivory", "jade", "lilac", "maple", "mauve", "moss", "ochre", "olive",
    "onyx", "pewter", "plum", "rust", "sable", "sage", "slate", "umber",
]
NOUNS = [
    "adder", "badger", "crane", "dipper", "eider", "falcon", "finch", "gannet",
    "grebe", "harrier", "heron", "ibis", "jackdaw", "kestrel", "linnet", "marten",
    "merlin", "osprey", "otter", "petrel", "pipit", "plover", "raven", "shrike",
    "siskin", "stoat", "swift", "teal", "vireo", "wagtail", "weasel", "wren",
]

# Reddit-style mentions. Handles from outside the thread are not in the author
# list, so a pattern is the only way to catch them.
MENTION = re.compile(r"(?<![\w/])/?u/([A-Za-z0-9_-]{3,20})\b")

# Links that resolve to a specific comment or profile on the source platform.
# These are a direct lookup back to an author, which is the same reason real
# comment ids are not stored. Links to outside articles are left alone — they
# are context, and they identify nobody.
PERMALINK = re.compile(
    r"https?://(?:"
    r"news\.ycombinator\.com/(?:item|user|reply)\?\S+"
    r"|(?:www\.|old\.|np\.)?reddit\.com/(?:r/\S*comments/\S+|u(?:ser)?/\S+)"
    r")",
    re.IGNORECASE,
)


@dataclass
class RawComment:
    """What a fetcher produces. Real handle still attached — this is pre-hash."""

    author: str
    body: str
    parent_body: str | None
    depth: int
    score: int = 0
    controversiality: int = 0


def display_name(author: str, thread_id: str, taken: dict[str, str]) -> str:
    """A stable two-word name for one author within one thread.

    Salted with the thread id so the same handle in two threads gets two names.
    Collisions are resolved by probing, so two distinct authors never share a
    name — otherwise the reply structure would read as one person talking.
    """
    if author in taken:
        return taken[author]

    digest = hashlib.sha256(f"{thread_id}:{author}".encode()).digest()
    index = int.from_bytes(digest[:8], "big")
    used = set(taken.values())

    def compose(offset: int) -> str:
        candidate = index + offset
        return (
            f"{ADJECTIVES[candidate % len(ADJECTIVES)]}-"
            f"{NOUNS[(candidate // len(ADJECTIVES)) % len(NOUNS)]}"
        )

    for probe in range(len(ADJECTIVES) * len(NOUNS)):
        name = compose(probe)
        if name not in used:
            taken[author] = name
            return name

    # A 3,859-comment Hacker News thread has more than 1,024 distinct authors,
    # so the two-word space does run out on real data. Fall back to a numeric
    # suffix rather than failing: distinctness matters more than prettiness, and
    # by this point the thread is far larger than anything on screen at once.
    base, suffix = compose(0), 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    taken[author] = f"{base}-{suffix}"
    return taken[author]


def scrub_text(text: str, thread_id: str, taken: dict[str, str], handles: set[str]) -> str:
    """Rewrite handles that appear *inside* comment text.

    Hashing the author field is not enough on its own. People address each other
    by name in the body, and that string goes on screen under the word "toxic"
    exactly as invariant 8 forbids.

    Three passes, because people get named in three different ways.

    Reddit has an explicit `u/name` syntax, which catches people who never
    posted in the thread. Hacker News has no mention syntax at all, so the only
    reliable signal is a handle belonging to someone who *did* post here — which
    we have. And both platforms let you link straight at a comment or a profile,
    which identifies its author as surely as naming them.

    Handle matching is deliberately **case-insensitive**. A thread about HN's
    moderation policy referred to `dang` as "Dang's comments" and, once, as
    "Fuck Dang" — a case-sensitive match left a real handle attached to that.
    The cost is that an author whose handle is an ordinary word gets that word
    rewritten wherever it appears, which is a much smaller problem than the leak.
    """
    text = MENTION.sub(lambda m: "u/" + display_name(m.group(1), thread_id, taken), text)
    for handle in sorted(handles, key=len, reverse=True):
        if len(handle) < 3:
            continue  # too short to match without mangling ordinary words
        text = re.sub(
            rf"(?<![\w/]){re.escape(handle)}\b",
            lambda _m, h=handle: display_name(h, thread_id, taken),
            text,
            flags=re.IGNORECASE,
        )
    return PERMALINK.sub("[link]", text)


def build_document(
    *,
    thread_id: str,
    title: str,
    selftext: str,
    source: str,
    origin: str,
    comments: list[RawComment],
    min_chars: int = 20,
    max_chars: int = 1500,
    min_controversiality: int = 0,
) -> tuple[dict, dict[str, object]]:
    """Filter, anonymise and shape into the TECHNICAL_SPEC §5.2 schema.

    Returns (document, stats). `source` and `origin` are additions to §5.2 — the
    spec assumed one feed, and the Pen needs to say where a comment came from
    once several are in play.
    """
    kept = [
        c for c in comments
        if min_chars <= len(c.body) <= max_chars
        and c.controversiality >= min_controversiality
    ]

    taken: dict[str, str] = {}
    handles = {c.author for c in kept if c.author}
    # Name every author before scrubbing, so a handle mentioned in text resolves
    # to the same display name as that person's own comments.
    for comment in kept:
        display_name(comment.author, thread_id, taken)

    rewritten = 0
    out = []
    for index, comment in enumerate(kept):
        body = scrub_text(comment.body, thread_id, taken, handles)
        parent = (
            scrub_text(comment.parent_body, thread_id, taken, handles)
            if comment.parent_body else None
        )
        rewritten += body != comment.body
        out.append(
            {
                "id": f"c{index:03d}",
                "author_hash": taken[comment.author],
                "body": body,
                "parent_snippet": parent[:PARENT_SNIPPET_CHARS] if parent else None,
                "depth": comment.depth,
                "score": comment.score,
                "controversiality": comment.controversiality,
            }
        )

    document = {
        "thread": {
            "id": thread_id,
            "title": scrub_text(title, thread_id, taken, handles),
            "selftext": scrub_text(selftext, thread_id, taken, handles)[:1200],
            "subreddit": source,
            "source": source,
            "origin": origin,
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "comments": out,
    }
    stats = {
        "seen": len(comments),
        "kept": len(kept),
        "authors": len(taken),
        "rewritten": rewritten,
        "with_parent": sum(1 for c in out if c["parent_snippet"]),
        "max_depth": max((c["depth"] for c in out), default=0),
    }
    return document, stats


def write_thread(document: dict, out_path: Path | None, slug: str) -> Path:
    path = out_path or Path(config.THREADS_DIR) / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def report(document: dict, stats: dict, path: Path) -> None:
    thread = document["thread"]
    print(
        f"\n{thread['source']}  {thread['title'][:70]}\n"
        f"  {stats['seen']} in tree, {stats['kept']} kept after filters\n"
        f"  {stats['authors']} handles hashed, {stats['rewritten']} bodies rewritten\n"
        f"  max depth {stats['max_depth']}, {stats['with_parent']} have parent context\n"
        f"\nwrote {path}"
    )
