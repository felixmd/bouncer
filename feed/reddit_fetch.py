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
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from feed.store import RawComment, build_document, report, write_thread

USER_AGENT = (
    "the-bouncer/0.1 (dev-time thread fetch for a local moderation demo; "
    "contact: run by a human at a laptop)"
)
PARENT_SNIPPET_CHARS = 200
SKIP_AUTHORS = {"AutoModerator", "[deleted]", "RemindMeBot", "sneakpeekbot"}
SKIP_BODIES = {"[deleted]", "[removed]"}


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


def walk(children: list[dict], depth: int = 0,
         parent_body: str | None = None) -> list[RawComment]:
    """Depth-first over the comment tree, carrying one level of parent text.

    One level only — spec §3.3. "You're an idiot" as a top-level comment and as
    a reply to a specific claim are different objects, but two levels is
    diminishing returns and stops the thread context amortising across a batch.
    """
    out: list[RawComment] = []
    for child in children:
        # "more" nodes are Reddit's load-more stubs; there is no text in them.
        if child.get("kind") != "t1":
            continue
        data = child.get("data", {})
        body = (data.get("body") or "").strip()
        author = data.get("author") or "[deleted]"

        keep = bool(body) and body not in SKIP_BODIES and author not in SKIP_AUTHORS
        if keep:
            out.append(
                RawComment(
                    author=author,
                    body=body,
                    parent_body=parent_body,
                    depth=depth,
                    score=data.get("score", 0),
                    controversiality=data.get("controversiality", 0),
                )
            )

        replies = data.get("replies")
        if isinstance(replies, dict):
            out += walk(
                replies.get("data", {}).get("children", []),
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
    subreddit = post.get("subreddit", "")
    comments = walk(payload[1]["data"]["children"])

    document, stats = build_document(
        thread_id=thread_id,
        title=post.get("title", ""),
        selftext=post.get("selftext") or "",
        source=f"r/{subreddit}",
        origin=f"https://www.reddit.com{post.get('permalink', '')}",
        comments=comments,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        min_controversiality=args.min_controversiality,
    )
    document["thread"]["sort"] = args.sort

    path = write_thread(document, args.out, f"{subreddit}-{thread_id}".lower())
    contested = sum(1 for c in document["comments"] if c["controversiality"])
    report(document, stats, path)
    print(f"  {contested} flagged controversial by reddit")


if __name__ == "__main__":
    main()
