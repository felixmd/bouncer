"""Dev-time only. Fetches one Reddit thread and writes it to data/threads/.

    uv run --env-file .env python -m feed.reddit_fetch <thread_url>
    uv run --env-file .env python -m feed.reddit_fetch <thread_url> --out data/threads/x.json

**This is never called at runtime** — invariant 5.

On authentication
-----------------
`TECHNICAL_SPEC.md` §5.1 said unauthenticated `.json` endpoints were fine at
this volume and that OAuth was over-engineering. **That is no longer true.**
From this network Reddit serves 403 to `www.reddit.com/....json` regardless of
User-Agent, and `old.reddit.com` redirects to `/login/?reason=lor2`. The block
is by IP, not by client string — a browser User-Agent gets the same 403.

App-only OAuth does work. Create a **script** app at
<https://www.reddit.com/prefs/apps> and put the two values in `.env`:

    REDDIT_CLIENT_ID=...
    REDDIT_CLIENT_SECRET=...

The script uses them if present and falls back to the public endpoint if not,
so it keeps working from a network that is not blocked. The app reads only from
`data/threads/*.json`. That is what makes the demo independent of Reddit's
availability, of datacenter-IP blocks, and of the thread changing under us; it
also means two demo runs are identical, which matters when presenting twice.

On usernames
------------
Handles are hashed at fetch time to a stable two-word display name, per
invariant 8. Authors map to distinct names within a thread so reply structure
stays legible, and the hash is salted with the thread id so the same person in
two threads does not get the same name.

**This is not anonymisation and should not be described as such.** Comment
bodies are stored verbatim, so anyone holding this file can find the original by
searching for the text. What the hash actually buys is that the app never *puts
a real handle next to the word "toxic" on a screen*, which is the risk PRD §7
names. Real comment ids are deliberately not stored either — they would be a
direct lookup back to the author.

If you need the stronger property, the thread must be paraphrased, and that
needs a generative model, which invariant 6 rules out. So: keep these files out
of public repos. `data/threads/` is gitignored apart from a small committed test
fixture.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

import config

USER_AGENT = (
    "the-bouncer/0.1 (dev-time thread fetch for a local moderation demo; "
    "contact: run by a human at a laptop)"
)
PARENT_SNIPPET_CHARS = 200
SKIP_AUTHORS = {"AutoModerator", "[deleted]", "RemindMeBot", "sneakpeekbot"}
SKIP_BODIES = {"[deleted]", "[removed]"}

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
    for probe in range(len(ADJECTIVES) * len(NOUNS)):
        candidate = index + probe
        name = (
            f"{ADJECTIVES[candidate % len(ADJECTIVES)]}-"
            f"{NOUNS[(candidate // len(ADJECTIVES)) % len(NOUNS)]}"
        )
        if name not in used:
            taken[author] = name
            return name
    raise RuntimeError("ran out of display names")  # 1024 authors in one thread


MENTION = re.compile(r"(?<![\w/])/?u/([A-Za-z0-9_-]{3,20})\b")


def scrub_mentions(text: str, thread_id: str, taken: dict[str, str]) -> str:
    """Replace `u/handle` inside comment text with the same hashed display name.

    Hashing the `author` field is not enough on its own. People address each
    other by handle in the body — "u/alice is right about this" — and that
    string would go on screen under the word "toxic" exactly as invariant 8
    forbids. Mentions route through the same `taken` map, so a person who is
    both an author and a mentioned handle reads consistently.
    """
    return MENTION.sub(lambda m: "u/" + display_name(m.group(1), thread_id, taken), text)


def thread_path(raw: str) -> str:
    """Accept a permalink in any of the forms people actually paste, return the path."""
    url = raw.strip().split("?")[0].split("#")[0].rstrip("/")
    url = re.sub(r"^https?://(www|old|new|np|m|api)\.reddit\.com", "", url)
    url = re.sub(r"^https?://reddit\.com", "", url)
    path = "/" + url.lstrip("/")
    if not re.search(r"/comments/[a-z0-9]+", path):
        raise SystemExit(f"does not look like a thread permalink: {raw}")
    return path


def get_token(client_id: str, client_secret: str) -> str:
    """App-only OAuth. No user context needed — we only read public threads."""
    response = httpx.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    if response.status_code == 401:
        raise SystemExit("reddit rejected REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (401)")
    if response.status_code != 200:
        raise SystemExit(f"token request failed: {response.status_code} {response.text[:200]}")
    return response.json()["access_token"]


def build_session() -> tuple[str, dict[str, str]]:
    """(base_url, headers). Uses OAuth when credentials are present."""
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if client_id and client_secret:
        headers["Authorization"] = f"Bearer {get_token(client_id, client_secret)}"
        print("  authenticated (app-only OAuth)")
        return "https://oauth.reddit.com", headers
    print("  unauthenticated — will fall back if this network is blocked")
    return "https://www.reddit.com", headers


BLOCKED_HELP = """
Reddit returned 403 for an unauthenticated request.

This is an IP-level block, not a User-Agent problem — a browser User-Agent gets
the same 403, and old.reddit.com redirects to /login/?reason=lor2. It is exactly
the risk PRD §7 lists as "Reddit unreachable at showtime", arriving early, and
it is also why the app never calls Reddit at runtime.

To fix it, create a *script* app at https://www.reddit.com/prefs/apps and add to .env:

    REDDIT_CLIENT_ID=<the string under the app name>
    REDDIT_CLIENT_SECRET=<the "secret" field>

then re-run with:  uv run --env-file .env python -m feed.reddit_fetch <url>

The token endpoint is reachable from here, so credentials are the only thing missing.
"""


def fetch(base: str, path: str, params: dict[str, Any], headers: dict[str, str],
          attempts: int = 5) -> Any:
    """A descriptive User-Agent is load-bearing rather than politeness — Reddit
    blocks `python-requests/2.x` outright — but it is not sufficient here."""
    suffix = "" if base.startswith("https://oauth") else ".json"
    url = f"{base}{path}{suffix}"
    for attempt in range(attempts):
        response = httpx.get(
            url, params=params, headers=headers, timeout=30, follow_redirects=True
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503) and attempt < attempts - 1:
            wait = float(response.headers.get("Retry-After") or 2**attempt)
            print(f"  {response.status_code}, waiting {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if response.status_code in (403, 302) and "Authorization" not in headers:
            raise SystemExit(BLOCKED_HELP)
        raise SystemExit(
            f"reddit returned {response.status_code} for {url}\n  {response.text[:300]}"
        )
    raise SystemExit("gave up after retries")


def walk(children: list[dict], thread_id: str, taken: dict[str, str],
         depth: int = 0, parent_body: str | None = None) -> list[dict]:
    """Depth-first over the comment tree, carrying one level of parent text.

    One level only — spec §3.3. "You're an idiot" as a top-level comment and as
    a reply to a specific claim are different objects, but two levels is
    diminishing returns and stops the thread context amortising across a batch.
    """
    out: list[dict] = []
    for child in children:
        # "more" nodes are Reddit's load-more stubs; there is no text in them.
        if child.get("kind") != "t1":
            continue
        data = child.get("data", {})
        body = (data.get("body") or "").strip()
        author = data.get("author") or "[deleted]"

        keep = body and body not in SKIP_BODIES and author not in SKIP_AUTHORS
        if keep:
            out.append(
                {
                    "author_hash": display_name(author, thread_id, taken),
                    "body": body,
                    "parent_snippet": parent_body,
                    "depth": depth,
                    "score": data.get("score", 0),
                    "controversiality": data.get("controversiality", 0),
                }
            )

        replies = data.get("replies")
        if isinstance(replies, dict):
            out += walk(
                replies.get("data", {}).get("children", []),
                thread_id,
                taken,
                depth + 1,
                # A dropped comment still parents its replies; pass the
                # grandparent rather than inventing context that was not there.
                body[:PARENT_SNIPPET_CHARS] if keep else parent_body,
            )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch one Reddit thread to disk.")
    parser.add_argument("url", help="thread permalink")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--sort", default="controversial",
        help="popularity and contentiousness are nearly uncorrelated on Reddit; "
             "the top post on r/all is usually a photo with a friendly comment section",
    )
    parser.add_argument("--limit", type=int, default=500, help="comments requested")
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=1500)
    parser.add_argument(
        "--min-controversiality", type=int, default=0,
        help="1 keeps only comments Reddit itself flags as contested",
    )
    args = parser.parse_args()

    path = thread_path(args.url)
    print(f"fetching {path}  (sort={args.sort})")
    base, headers = build_session()
    payload = fetch(base, path, {"sort": args.sort, "limit": args.limit, "raw_json": 1}, headers)

    if not isinstance(payload, list) or len(payload) < 2:
        raise SystemExit("unexpected response shape — is that a thread permalink?")

    post = payload[0]["data"]["children"][0]["data"]
    thread_id = post["id"]
    taken: dict[str, str] = {}
    comments = walk(payload[1]["data"]["children"], thread_id, taken)

    kept = [
        c for c in comments
        if args.min_chars <= len(c["body"]) <= args.max_chars
        and c["controversiality"] >= args.min_controversiality
    ]
    # Scrub after the walk, so every author in the thread is already in `taken`
    # and a mention resolves to the same display name as that person's comments.
    mentions = 0
    for comment in kept:
        for field in ("body", "parent_snippet"):
            if comment[field]:
                cleaned = scrub_mentions(comment[field], thread_id, taken)
                mentions += cleaned != comment[field]
                comment[field] = cleaned

    for index, comment in enumerate(kept):
        comment["id"] = f"c{index:03d}"
    # Stored key order matches TECHNICAL_SPEC §5.2 so the file reads as specced.
    kept = [
        {
            "id": c["id"], "author_hash": c["author_hash"], "body": c["body"],
            "parent_snippet": c["parent_snippet"], "depth": c["depth"],
            "score": c["score"], "controversiality": c["controversiality"],
        }
        for c in kept
    ]

    document = {
        "thread": {
            "id": thread_id,
            "title": post.get("title", ""),
            "selftext": (post.get("selftext") or "")[:1200],
            "subreddit": post.get("subreddit", ""),
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sort": args.sort,
        },
        "comments": kept,
    }

    out_path = args.out or Path(config.THREADS_DIR) / (
        f"{post.get('subreddit', 'thread')}-{thread_id}".lower()
        .replace(" ", "-") + ".json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")

    contested = sum(1 for c in kept if c["controversiality"])
    depth_max = max((c["depth"] for c in kept), default=0)
    print(
        f"\nr/{post.get('subreddit')}  {post.get('title', '')[:70]}\n"
        f"  {len(comments)} comments in tree, {len(kept)} kept after filters\n"
        f"  {len(taken)} distinct handles hashed, {mentions} u/ mentions rewritten\n"
        f"  {contested} flagged controversial by reddit, max depth {depth_max}\n"
        f"  {sum(1 for c in kept if c['parent_snippet'])} have parent context\n"
        f"\nwrote {out_path}"
    )


if __name__ == "__main__":
    main()
