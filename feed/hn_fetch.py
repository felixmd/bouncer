"""Dev-time only. Fetches Hacker News threads to data/threads/.

    uv run python -m feed.hn_fetch --search                 # find contentious threads
    uv run python -m feed.hn_fetch 41002195                 # fetch one by id
    uv run python -m feed.hn_fetch --search --top 3         # fetch the top 3 hits

**Never called at runtime** — invariant 5, same as every other fetcher.

Why HN is here at all: Reddit started serving 403 to unauthenticated requests
from this network, and getting credentials needs a bot account and a wait. HN's
API needs none, and it earns its place rather than just being available:

- PRD §3 puts the audience at "technical-adjacent viewers" — HN threads are
  legible to exactly that room without a subreddit in-joke to explain.
- Contempt is the axis the PRD singles out as the interesting one, and
  condescension is HN's native register. `hostility` fires less often here than
  on Reddit, which is a real weakness for the Bounced lane, but `contempt` and
  `substance` get a better workout than they would on r/AmItheAsshole.
- It is much safer on a screen in an office than a CMV thread about race.

Uses the Algolia mirror rather than the official Firebase API: `items/<id>`
returns the whole nested thread in one request, where Firebase needs one call
per comment.
"""

import argparse
import html
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from feed.store import RawComment, build_document, report, write_thread

SEARCH_URL = "https://hn.algolia.com/api/v1/search"
ITEM_URL = "https://hn.algolia.com/api/v1/items"
USER_AGENT = "the-bouncer/0.1 (dev-time thread fetch for a local moderation demo)"

TAG = re.compile(r"<[^>]+>")
BLANKS = re.compile(r"\n{3,}")


def clean(raw: str | None) -> str:
    """HN serves comment bodies as HTML fragments."""
    if not raw:
        return ""
    text = raw.replace("</p>", "\n\n").replace("<p>", "\n\n")
    text = TAG.sub("", text)
    text = html.unescape(text)
    # Mojibake in the source survives unescaping and renders as a black diamond
    # on a card. A straight apostrophe is the overwhelmingly common intent.
    text = text.replace("�", "'")
    return BLANKS.sub("\n\n", text).strip()


def get(url: str, params: dict[str, Any] | None = None, attempts: int = 4) -> Any:
    for attempt in range(attempts):
        response = httpx.get(
            url, params=params, headers={"User-Agent": USER_AGENT},
            timeout=60, follow_redirects=True,
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503) and attempt < attempts - 1:
            wait = 2**attempt
            print(f"  {response.status_code}, waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        raise SystemExit(f"algolia returned {response.status_code} for {url}")
    raise SystemExit("gave up after retries")


def search(min_comments: int, hits: int, query: str | None) -> list[dict]:
    """Comment count is the closest available proxy for contentiousness.

    Reddit has a `controversiality` flag; HN has nothing equivalent. A thread
    with 400 comments is not necessarily an argument, but a thread with 12
    certainly is not one, and the pile gets eyeballed before it ships anyway
    (TASKS.md 1.2).
    """
    params: dict[str, Any] = {
        "tags": "story",
        "numericFilters": f"num_comments>{min_comments}",
        "hitsPerPage": hits,
    }
    if query:
        params["query"] = query
    return get(SEARCH_URL, params)["hits"]


def walk(children: list[dict], depth: int = 0,
         parent_body: str | None = None) -> list[RawComment]:
    """Depth-first, carrying one level of parent text — spec §3.3.

    A dead or deleted comment still parents its replies, so it passes the
    grandparent down rather than a hole. `on_topic` is scored against that
    string and inventing context that was not there is worse than reaching past
    it.
    """
    out: list[RawComment] = []
    for child in children:
        body = clean(child.get("text"))
        author = child.get("author")
        keep = bool(body and author)
        if keep:
            out.append(
                RawComment(
                    author=author,
                    body=body,
                    parent_body=parent_body,
                    depth=depth,
                    score=child.get("points") or 0,
                )
            )
        out += walk(child.get("children") or [], depth + 1, body if keep else parent_body)
    return out


def fetch_thread(story_id: int, args: argparse.Namespace) -> None:
    item = get(f"{ITEM_URL}/{story_id}")
    comments = walk(item.get("children") or [])

    document, stats = build_document(
        thread_id=f"hn{story_id}",
        title=item.get("title") or "",
        # Link posts have no body. The linked article is deliberately not
        # fetched — summarising it would need a second model (invariant 6), and
        # on Hacker News the comments argue with each other far more than with
        # the article anyway.
        selftext=clean(item.get("text")),
        source="HackerNews",
        origin=f"https://news.ycombinator.com/item?id={story_id}",
        comments=comments,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
    )
    path = write_thread(document, args.out, f"hn-{story_id}")
    report(document, stats, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Hacker News threads to disk.")
    parser.add_argument("story_id", nargs="?", type=int, help="HN story id")
    parser.add_argument("--search", action="store_true", help="list contentious threads")
    parser.add_argument("--query", default=None, help="narrow the search")
    parser.add_argument("--min-comments", type=int, default=300)
    parser.add_argument("--hits", type=int, default=15)
    parser.add_argument("--top", type=int, default=0, help="fetch the first N hits")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=1500)
    args = parser.parse_args()

    if args.story_id:
        fetch_thread(args.story_id, args)
        return

    if not args.search:
        parser.error("give a story id, or --search")

    hits = search(args.min_comments, args.hits, args.query)
    print(f"{len(hits)} threads with more than {args.min_comments} comments:\n")
    for hit in hits:
        print(f"  {hit['objectID']:<10} {hit.get('num_comments', 0):>5} cmts  "
              f"{(hit.get('title') or '')[:68]}")

    for hit in hits[:args.top]:
        print(f"\n--- fetching {hit['objectID']}")
        fetch_thread(int(hit["objectID"]), args)

    if not args.top:
        print("\nre-run with --top N, or pass one of those ids directly")


if __name__ == "__main__":
    main()
