"""Dev-time only. Pulls a stratified Civil Comments sample to data/jigsaw/.

    uv run python -m calibrate.fetch_jigsaw --n 300

Civil Comments (the Jigsaw dataset) ships human toxicity labels, which is the
whole reason it is here: the Reddit path gives spectacle, this gives ground
truth. Read openly from the HuggingFace datasets-server, no auth.

The raw distribution is roughly 90% benign, so a uniform sample would be almost
all clean and would tell us nothing about the threshold. We stratify to a
requested toxic fraction instead. That makes the sample useless for estimating
base rates and fine for measuring agreement, which is what it is for.

Like feed/reddit_fetch.py this never runs at demo time. Output is gitignored.
"""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROWS_URL = "https://datasets-server.huggingface.co/rows"
DATASET = "google/civil_comments"
OUT_PATH = Path("data/jigsaw/sample.json")

TOXIC_AT = 0.5
MIN_CHARS = 40
MAX_CHARS = 900
PAGE = 100


def fetch_page(offset: int, length: int = PAGE, attempts: int = 6) -> list[dict]:
    """One page, with backoff. The datasets-server 429s a sustained scan."""
    query = urllib.parse.urlencode(
        {"dataset": DATASET, "config": "default", "split": "train",
         "offset": offset, "length": length}
    )
    request = urllib.request.Request(
        f"{ROWS_URL}?{query}",
        headers={"User-Agent": "the-bouncer/0.1 (dev-time calibration sample)"},
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return [row["row"] for row in json.loads(response.read())["rows"]]
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 502, 503) or attempt == attempts - 1:
                raise
            wait = float(exc.headers.get("Retry-After") or 2 ** attempt)
            print(f"    {exc.code} at offset {offset}, waiting {wait:.0f}s")
            time.sleep(wait)
    return []


def usable(row: dict) -> bool:
    text = (row.get("text") or "").strip()
    return MIN_CHARS <= len(text) <= MAX_CHARS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--toxic-fraction", type=float, default=0.4)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()

    want_toxic = int(args.n * args.toxic_fraction)
    want_clean = args.n - want_toxic
    toxic: list[dict] = []
    clean: list[dict] = []

    offset = args.start
    while len(toxic) < want_toxic or len(clean) < want_clean:
        rows = fetch_page(offset)
        if not rows:
            break
        offset += len(rows)
        for row in rows:
            if not usable(row):
                continue
            bucket = toxic if row["toxicity"] >= TOXIC_AT else clean
            want = want_toxic if row["toxicity"] >= TOXIC_AT else want_clean
            if len(bucket) < want:
                bucket.append(row)
        print(f"  scanned {offset}  toxic {len(toxic)}/{want_toxic}  "
              f"clean {len(clean)}/{want_clean}")

    sample = []
    # Interleave so any ordering effect in the sweep does not line up with the label.
    for index in range(max(len(toxic), len(clean))):
        for bucket in (clean, toxic):
            if index < len(bucket):
                row = bucket[index]
                sample.append(
                    {
                        "id": f"j{len(sample):03d}",
                        "text": row["text"].strip(),
                        "toxicity": row["toxicity"],
                        "insult": row["insult"],
                        "identity_attack": row["identity_attack"],
                        "threat": row["threat"],
                        "label_toxic": row["toxicity"] >= TOXIC_AT,
                    }
                )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(sample, indent=2), encoding="utf-8")
    n_toxic = sum(1 for s in sample if s["label_toxic"])
    print(f"\nwrote {len(sample)} comments to {OUT_PATH} "
          f"({n_toxic} toxic / {len(sample) - n_toxic} clean, scanned {offset} rows)")


if __name__ == "__main__":
    main()
