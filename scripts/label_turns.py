"""Sample real turns from an export and label them by hand.

Every extraction number so far rests on fixtures written *and* labelled by the same
party that built the extractor. That measures one party's consistency, not the
systems'. This produces a set with neither problem: turns sampled from a real
export, labelled by the person whose graph it is.

    uv run python scripts/label_turns.py ~/Downloads/<export-dir> --n 100

Stratified deliberately. Turns where the heuristic fires are where it can be *wrong*
(false positives); turns where it stays silent are where it can be *missing*
something (false negatives). Sampling uniformly would fill the set with the silent
majority and measure almost nothing, since most turns contain nothing durable.

Writes a fixture in the same shape the bench already reads, so a finished session
scores immediately:

    BENCH_SET=mine uv run python scripts/bench_extraction.py claude-sonnet-5

Nothing here leaves the machine. The output holds the user's own words, so it is
written outside the repo by default — committing a labelled set means committing
private conversation text, and that should be a deliberate act, not a side effect.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

from coletar.acquisition.chatgpt_export import read_export
from coletar.extraction import extract_memories

PROMPT = """
  [d] durable    — true about this person beyond this conversation
  [t] transient  — about the task in front of them right now
  [s] skip       — unclear, or you would rather not label it
  [q] quit and save
"""


async def _pool(export: Path, cap: int) -> tuple[list[str], list[str]]:
    """Turns split by whether the heuristic fires, which is where each kind of
    error lives."""
    fires: list[str] = []
    silent: list[str] = []
    for conversation in read_export(export):
        for message in conversation.messages:
            bucket = fires if await extract_memories(user_text=message.text) else silent
            if len(bucket) < cap:
                bucket.append(message.text)
        if len(fires) >= cap and len(silent) >= cap:
            break
    return fires, silent


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path, help="Export directory or ZIP.")
    parser.add_argument("--n", type=int, default=100, help="How many turns to offer.")
    parser.add_argument("--out", type=Path, default=Path.home() / "coletar-labels.json")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    fires, silent = await _pool(args.export, args.n * 4)
    random.seed(args.seed)
    # Weighted toward turns the heuristic acts on: that is where a wrong answer
    # actually reaches the user's graph.
    chosen = random.sample(fires, min(len(fires), args.n * 2 // 3))
    chosen += random.sample(silent, min(len(silent), args.n - len(chosen)))
    random.shuffle(chosen)

    print(f"\n  {len(chosen)} turns sampled from {args.export.name}")
    print("  Judge the turn, not what an extractor would do with it.")
    print(PROMPT)

    labelled: list[dict[str, object]] = []
    for i, text in enumerate(chosen, 1):
        print(f"\n  ── {i}/{len(chosen)} " + "─" * 50)
        print(f"  {text[:600]}")
        while True:
            answer = input("  [d/t/s/q] ").strip().lower()
            if answer in {"d", "t", "s", "q"}:
                break
        if answer == "q":
            break
        if answer == "s":
            continue
        labelled.append(
            {"id": f"u{len(labelled) + 1:03d}", "durable": answer == "d", "user": text}
        )

    args.out.write_text(json.dumps({"turns": labelled}, indent=2))
    durable = sum(1 for row in labelled if row["durable"])
    print(f"\n  wrote {len(labelled)} labels ({durable} durable) to {args.out}")
    print("  score it:  BENCH_SET=mine uv run python scripts/bench_extraction.py claude-sonnet-5")
    print(f"  (copy it to tests/fixtures/ first, or point BENCH_SET at {args.out})")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit("\n  stopped; nothing written")
