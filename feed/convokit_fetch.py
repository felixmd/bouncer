"""Dev-time only. Pulls threaded conversations from a ConvoKit corpus.

    uv run python -m feed.convokit_fetch --corpus cmv
    uv run python -m feed.convokit_fetch --corpus wiki --conversations 200
    uv run python -m feed.convokit_fetch --corpus cmv --no-topic-filter

**Never called at runtime** — invariant 5.

This exists because Hacker News produced a 1% Bounced lane. HN argues by
condescension rather than abuse, so `hostility` almost never fires, and a demo
whose red lane never lights up is two-thirds of a demo. `conversations-gone-awry`
is curated for exactly the missing material: conversations that start civil and
derail into personal attacks. It is real Reddit (r/changemyview), threaded, and
downloads without credentials.

Its pair structure is a gift for this demo. Each conversation is one of a
matched pair — one derails, one does not — so taking conversations wholesale
yields a mix rather than a wall of red.

On bundling several conversations into one file
-----------------------------------------------
These conversations are small: median 6 utterances, max 22. `TECHNICAL_SPEC.md`
§3.3 says batch by thread, so that one shared `state` amortises across B
comments — which would argue for one file per conversation and batches of six.

That reasoning no longer applies. Under `quoted` addressing (`FINDINGS.md` §2)
the state is *just the thread title*, twenty tokens or so, and every comment
carries its own text and its own parent snippet inside its own question. There
is nothing left to amortise, so bundling costs nothing and keeps batches full.

What bundling does cost is a meaningful `thread_title`, which is the `on_topic`
referent for root comments. This corpus has no post titles anyway — a
conversation's root is already a top-level comment on a CMV post the corpus does
not include — so that was lost before we got here. Replies, which are the large
majority, are judged against their real parent and are unaffected.
"""

import argparse
import html
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx

from feed.store import RawComment, build_document, report, write_thread

BASE = "https://zissou.infosci.cornell.edu/convokit/datasets"
CACHE = Path("data/raw")

CORPORA = {
    "cmv": ("conversations-gone-awry-cmv-corpus", "r/changemyview"),
    "wiki": ("conversations-gone-awry-corpus", "WikipediaTalk"),
    "winning": ("winning-args-corpus", "r/changemyview"),
}

# The demo runs on a screen in an office. These threads are real arguments and
# they are meant to be, but a debate about race or gender identity on a wall
# display is a different problem from a blurred insult, and not one the Bounced
# lane's blur solves. Excluding by keyword is blunt and will drop some innocent
# threads; that is the intended trade.
EXCLUDE = [
    "abortion", "affirmative action", "antisemit", "black people", "circumcis",
    "gender identity", "genocide", "holocaust", "immigrant", "islam", "jewish",
    "lgbt", "muslim", "nazi", "paedophil", "pedophil", "race realis", "racial",
    "racist", "rape", "refugee", "sexual assault", "slavery", "suicide",
    "trans people", "transgender", "white people",
]
EXCLUDE_RE = re.compile("|".join(re.escape(term) for term in EXCLUDE), re.IGNORECASE)


def ensure_corpus(name: str) -> Path:
    """Download once, keep it. The CMV zip is 51 MB."""
    path = CACHE / f"{name}.zip"
    if path.exists():
        print(f"  using cached {path}")
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{name}/{name}.zip"
    print(f"  downloading {url}")
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as response:
        if response.status_code != 200:
            raise SystemExit(f"convokit returned {response.status_code} for {url}")
        with path.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    print(f"  cached {path} ({path.stat().st_size / 1e6:.0f} MB)")
    return path


def load_conversations(zip_path: Path, name: str) -> dict[str, list[dict]]:
    """Stream utterances.jsonl and group by conversation.

    Streamed rather than read whole: the CMV file is 394 MB uncompressed.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    with zipfile.ZipFile(zip_path) as archive:
        inner = f"{name}/utterances.jsonl"
        with archive.open(inner) as handle:
            for line in handle:
                utterance = json.loads(line)
                grouped[utterance["conversation_id"]].append(
                    {
                        "id": utterance["id"],
                        # Reddit's markdown arrives HTML-escaped in this corpus,
                        # so quoted text renders as "&gt;" on a card.
                        "text": html.unescape(utterance.get("text") or "").strip(),
                        "speaker": utterance.get("speaker") or "[unknown]",
                        # ConvoKit spells it with a hyphen, unlike every other field.
                        "reply_to": utterance.get("reply-to"),
                        "timestamp": utterance.get("timestamp") or 0,
                    }
                )
    return grouped


def flatten(utterances: list[dict]) -> list[RawComment]:
    """Resolve reply-to into a parent snippet and a depth.

    A removed or empty parent still parents its replies, so its own parent is
    passed down instead — same rule as the other fetchers, and for the same
    reason: `on_topic` is scored against this string and inventing context that
    was not there is worse than reaching past it.
    """
    by_id = {u["id"]: u for u in utterances}
    ordered = sorted(utterances, key=lambda u: (u["timestamp"], u["id"]))

    def lineage(utterance: dict) -> tuple[str | None, int]:
        depth, parent_id = 0, utterance["reply_to"]
        snippet = None
        while parent_id in by_id:
            depth += 1
            parent = by_id[parent_id]
            if snippet is None and parent["text"]:
                snippet = parent["text"]
            parent_id = parent["reply_to"]
        return snippet, depth

    out = []
    for utterance in ordered:
        if not utterance["text"]:
            continue
        snippet, depth = lineage(utterance)
        out.append(
            RawComment(
                author=utterance["speaker"],
                body=utterance["text"],
                parent_body=snippet,
                depth=depth,
            )
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a ConvoKit corpus to disk.")
    parser.add_argument("--corpus", choices=sorted(CORPORA), default="cmv")
    parser.add_argument("--conversations", type=int, default=120)
    parser.add_argument("--min-comments", type=int, default=5,
                        help="skip conversations shorter than this")
    parser.add_argument("--no-topic-filter", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=1500)
    args = parser.parse_args()

    name, source = CORPORA[args.corpus]
    grouped = load_conversations(ensure_corpus(name), name)
    print(f"  {sum(len(v) for v in grouped.values()):,} utterances "
          f"in {len(grouped):,} conversations")

    chosen, skipped_topic, skipped_short = [], 0, 0
    for conversation_id in sorted(grouped):
        utterances = grouped[conversation_id]
        if len(utterances) < args.min_comments:
            skipped_short += 1
            continue
        if not args.no_topic_filter:
            if EXCLUDE_RE.search(" ".join(u["text"] for u in utterances)):
                skipped_topic += 1
                continue
        chosen.append(utterances)
        if len(chosen) >= args.conversations:
            break

    if not chosen:
        raise SystemExit("no conversations survived the filters")

    comments: list[RawComment] = []
    for utterances in chosen:
        comments += flatten(utterances)

    print(f"  {len(chosen)} conversations kept, {skipped_short} too short, "
          f"{skipped_topic} dropped on topic")
    if skipped_topic:
        print("    (topic filter is blunt by design — pass --no-topic-filter to see them)")

    document, stats = build_document(
        thread_id=f"ck-{args.corpus}",
        title=f"{source} — {len(chosen)} conversations that turned hostile",
        selftext="",
        source=source,
        origin=f"{BASE}/{name}/",
        comments=comments,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
    )
    path = write_thread(document, args.out, f"convokit-{args.corpus}")
    report(document, stats, path)

    if stats["kept"] < 200:
        print("\n  warning: fewer than 200 comments — raise --conversations",
              file=sys.stderr)


if __name__ == "__main__":
    main()
