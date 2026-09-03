"""Score extraction models against the labelled set. Costs money — run deliberately.

Answers the only question that matters for model choice: is the cheap model good
enough? Prints precision, recall, and kind-accuracy per model so the decision is
made from a table rather than from an argument.

    ANTHROPIC_API_KEY=... uv run python scripts/bench_extraction.py \
        claude-haiku-4-5 claude-sonnet-5 claude-opus-5

55 turns per model. At the largest model in that list this is a few cents.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from coletar.extraction.frontier import ExtractionUnavailable, propose  # noqa: E402
from coletar.extraction.proposal import Proposal  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

#: Which labelled set to score against. `live` is the original 55 turns, and is the
#: heuristic's own specification written as test cases — it flatters the patterns and
#: punishes anything else. `transient` is the set the heuristic actually fails: task
#: context it stores as standing preference.
SETS = {"live": "extraction_set.json", "transient": "transient_set.json"}

#: `BENCH_SET` also accepts a path, so a set labelled by the user with
#: `scripts/label_turns.py` can be scored without being copied into the repo —
#: committing one means committing private conversation text.


async def score(model: str, turns: list[dict]) -> dict[str, float | int | list[str]]:
    tp: list[str] = []
    fp: list[str] = []
    fn: list[str] = []
    wrong_kind: list[str] = []
    t0 = time.time()

    unavailable = 0
    for turn in turns:
        try:
            proposal: Proposal | None = await propose(transcript=str(turn["user"]), model=model)
        except ExtractionUnavailable:
            # Never examined. Counting these separately is the whole point — folding
            # them into "found nothing" would quietly depress recall and make an
            # overloaded provider look like a worse model.
            unavailable += 1
            continue
        got = list(proposal.memories) if proposal else []
        if turn.get("durable"):
            if got:
                tp.append(turn["id"])
                want = turn.get("kind")
                if want and got[0].kind.value != want:
                    wrong_kind.append(f'{turn["id"]}:{want}->{got[0].kind.value}')
            else:
                fn.append(turn["id"])
        elif got:
            fp.append(turn["id"])

    writes = len(tp) + len(fp)
    durable = len(tp) + len(fn)
    return {
        "seconds": round(time.time() - t0, 1),
        "precision": round(len(tp) / writes, 3) if writes else 0.0,
        "recall": round(len(tp) / durable, 3) if durable else 0.0,
        "wrong_kind": len(wrong_kind),
        "unavailable": unavailable,
        "false_positives": fp,
    }


async def main() -> None:
    # Deliberately no credential check here. The SDK resolves ANTHROPIC_API_KEY, an
    # `ant auth login` profile, or workload identity — in that order — and an
    # earlier version of this script gated on the env var alone, which rejected a
    # perfectly good OAuth profile. Let the SDK decide; a missing credential fails
    # loudly at client construction, which is where it belongs.
    models = sys.argv[1:] or ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"]
    print("  this calls a paid API — 55 turns per model\n")

    which = os.environ.get("BENCH_SET", "live")
    if which in SETS:
        source = FIXTURES / SETS[which]
    elif Path(which).expanduser().exists():
        source = Path(which).expanduser()
    else:
        sys.exit(f"BENCH_SET must be one of {sorted(SETS)}, or a path to a labelled set")
    raw = json.loads(source.read_text())
    turns = raw if isinstance(raw, list) else raw.get("turns") or list(raw.values())[0]
    print(f"  set: {source.name}")

    print(f"  {len(turns)} labelled turns; bars are precision >=0.85, kind exact\n")
    print(f"  {'model':22} {'prec':>6} {'recall':>7} {'kind✗':>6} {'n/a':>5} {'secs':>6}")
    for model in models:
        card = await score(model, turns)
        print(
            f"  {model:22} {card['precision']:>6} {card['recall']:>7} "
            f"{card['wrong_kind']:>6} {card['unavailable']:>5} {card['seconds']:>6}"
        )


asyncio.run(main())
