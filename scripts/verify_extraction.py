"""Check the extraction prompt against the cases a real import got wrong.

Run this after topping up credits, before committing to a full archive:

    uv run python scripts/verify_extraction.py

Each case is a turn that was mishandled on Chris's own archive, plus the durable
statements that must survive the fix. Costs a handful of calls.
"""

from __future__ import annotations

import asyncio
import sys

from coletar.extraction import extract_with_model
from coletar.schema.objects import Provider

#: (turn, should_yield_something_durable, why)
#:
#: "Durable" means a memory *or* an entity-linked fact. A statement naming an
#: organisation is meant to land as a fact linked to that entity rather than as a
#: bare memory — the prompt says so explicitly — so demanding "a memory" here fails
#: the model for obeying its own instruction, which is what the first version of
#: this script did.
CASES: list[tuple[str, bool, str]] = [
    ("I run the build site", False, "narration of the current step"),
    ("i run into infinite recursion", False, "a bug hit just now"),
    ("I use retrieval augmented generation to train gpt-4", False, "one project's technique"),
    ("we are using QuickSelect to get us the kth", False, "implementation detail on screen"),
    ("Hey can you help me debug this react component?", False, "a request"),
    ("Do not use tailwind css", False, "instruction aimed at this artifact"),
    ("don't use lucide react icons", False, "same"),
    ("Keep it technical", False, "steering this response"),
    ("All of the __str__ methods should return single quotes", False, "spec for open code"),
    # Durable, but must not become a memory about the user: entity + fact only.
    ("He has prior experience in machine learning", True, "a claim about a third party"),
    ("my manager wants weekly updates", True, "third party; belongs in facts"),
    ("I prefer fixed-point arithmetic for money, never floats.", True, "standing preference"),
    (
        "Always give me the failing test output before you propose a fix.",
        True,
        "standing instruction",
    ),
    ("I work at JPMorgan on the applied AI/ML desk.", True, "stable role"),
    ("I would prefer to stay in New York.", True, "stable constraint"),
]


async def main() -> int:
    passed = 0
    for turn, want_durable, why in CASES:
        objects, _ = await extract_with_model(transcript=turn, provider=Provider.CHATGPT)
        memories = [o for o in objects if o.type.value == "memory"]
        others = [o for o in objects if o.type.value != "memory"]
        # A third-party claim is durable, but must never land as a memory *about
        # the user*, so it is held to the stricter condition.
        third_party = "third party" in why
        ok = (bool(objects) == want_durable) and not (third_party and memories)
        passed += ok
        want = "entity" if third_party else ("keep" if want_durable else "drop")
        print(
            f"{'PASS' if ok else 'FAIL'}  want={want:6} "
            f"got={len(memories)}mem+{len(others)}other  {turn[:52]!r}  ({why})"
        )
        for obj in objects:
            print(f"          [{obj.type.value}] {obj.content[:72]}")
    print(f"\n{passed}/{len(CASES)} correct")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
