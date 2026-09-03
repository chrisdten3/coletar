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

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "extraction_set.json"


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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set — this script calls a paid API.")
    models = sys.argv[1:] or ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"]

    raw = json.loads(FIXTURE.read_text())
    turns = raw if isinstance(raw, list) else raw.get("turns") or list(raw.values())[0]

    print(f"  {len(turns)} labelled turns; bars are precision >=0.85, kind exact\n")
    print(f"  {'model':22} {'prec':>6} {'recall':>7} {'kind✗':>6} {'n/a':>5} {'secs':>6}")
    for model in models:
        card = await score(model, turns)
        print(
            f"  {model:22} {card['precision']:>6} {card['recall']:>7} "
            f"{card['wrong_kind']:>6} {card['unavailable']:>5} {card['seconds']:>6}"
        )


asyncio.run(main())
