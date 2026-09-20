"""Dev-time only. Bakes recorded verdicts into the offline replay corpus.

    uv run python -m feed.bake

Reads `experiments/out/probe-*.json` — real model output from
`experiments/thread_probe.py` — and writes `data/recorded/verdicts.json`, which
is committed so the demo can run with no key, no credits and no network.

**Every verdict in here is a real answer from the model.** Nothing is
synthesised or interpolated. That matters because offline mode is a fallback for
a room, not a mock for tests: a fabricated fingerprint on screen would be a lie
told to an audience, and this project's whole claim is that it reports what it
measured. The cost is coverage — only the sampled comments have verdicts, so
offline mode streams those and no others.

The client replays raw scores, so the normalised 0-10 values the probe recorded
are converted back to the model's own scale on the way in.
"""

import json
from pathlib import Path

import config

PROBES = Path("experiments/out")
THREADS = Path(config.THREADS_DIR)
OUT = Path("data/recorded/verdicts.json")


def raw(normalised: float) -> float:
    """Undo `judge.rubric.normalise`, since the client hands back raw scores."""
    return normalised * (config.SCORE_RUBRIC_LEVELS - 1) / config.SCORE_SCALE_MAX


def main() -> None:
    probes = sorted(PROBES.glob("probe-*.json"))
    if not probes:
        raise SystemExit(
            f"no recordings in {PROBES} — run experiments.thread_probe against a "
            "thread first"
        )

    baked: dict[str, dict] = {}
    for probe in probes:
        slug = probe.stem.removeprefix("probe-")
        thread_path = THREADS / f"{slug}.json"
        if not thread_path.exists():
            print(f"  skipping {probe.name}: no {thread_path}")
            continue
        thread_id = json.loads(thread_path.read_text(encoding="utf-8"))["thread"]["id"]

        kept = 0
        for row in json.loads(probe.read_text(encoding="utf-8")):
            if row.get("error") or not row.get("probabilities"):
                continue
            # The pipeline's ids are composite; the probe recorded the per-thread
            # ones, so rebuild the key the client will be asked for.
            baked[f"{thread_id}:{row['id']}"] = {
                axis: {
                    "score": raw(row["scores"][axis]),
                    "confidence": row["confidences"][axis],
                    "probabilities": row["probabilities"][axis],
                }
                for axis in row["scores"]
            }
            kept += 1
        print(f"  {probe.name} -> {kept} verdicts on thread {thread_id}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(baked, separators=(",", ":")), encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"\nwrote {len(baked)} verdicts to {OUT} ({size:.0f} KB)")


if __name__ == "__main__":
    main()
