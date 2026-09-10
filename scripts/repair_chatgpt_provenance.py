"""Relabel model-extracted ChatGPT objects that the importer stamped as regex output.

`_store_graph` runs only on the model path, but it overwrote every object's
`extraction_method` with `ACCOUNT_EXPORT_PARSE` at 0.60 — destroying the
`MODEL_EXTRACTED`/0.75 the extractor had correctly assigned. On the first full
archive that mislabelled 3,278 of 3,818 objects.

Re-importing does not fix them: the resume checkpoint skips those turns, so no call
is made and nothing is rewritten. Correcting the record in place is the right repair
anyway, and it is the case `review.edit` already describes — a bad label is a claim
about *the record*, not about the world, so it is corrected rather than superseded.
Every change appends an event carrying full before/after state, so the old label
stays readable in history (constraints 4, 5 and 6).

**The assumption this rests on**, stated because it cannot be inferred from a row:
that the named tenant's ChatGPT import ran in model mode. Entities and facts prove
it on their own — the pattern path cannot produce them — but memories from a
regex-mode import would look identical, so `--model-mode-confirmed` is required
rather than guessed.

    uv run python scripts/repair_chatgpt_provenance.py --tenant tenant_v1 \\
        --model-mode-confirmed
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import ExtractionMethod, Provider, default_confidence
from coletar.schema.tenancy import tenant_id as parse_tenant
from coletar.store import build_store

TARGET = ExtractionMethod.MODEL_EXTRACTED
WRONG = ExtractionMethod.ACCOUNT_EXPORT_PARSE


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument(
        "--model-mode-confirmed",
        action="store_true",
        help="Assert this tenant's ChatGPT import ran with extraction_mode=model.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.model_mode_confirmed:
        print("Refusing to guess. Pass --model-mode-confirmed; see the module docstring.")
        return 2

    store = build_store()
    tenant = parse_tenant(args.tenant)
    objects = await store.list_objects(
        tenant, limit=200_000, include_retired=True, include_superseded=True
    )
    stale = [
        o
        for o in objects
        if o.provenance.provider is Provider.CHATGPT and o.extraction_method is WRONG
    ]
    proof = sum(1 for o in stale if o.type.value in {"entity", "fact"})
    print(
        f"{len(stale)} objects to relabel ({proof} are entities/facts, which only "
        f"the model path can produce)"
    )
    if args.dry_run or not stale:
        return 0

    corrected = default_confidence(TARGET)
    for obj in stale:
        before = obj.model_dump(mode="json")
        obj.extraction_method = TARGET
        obj.confidence = corrected
        obj.provenance.confidence = corrected
        await store.put_object(
            tenant,
            obj,
            event=Event(
                type=EventType.OBJECT_UPDATED,
                actor=Actor.SYSTEM,
                object_id=obj.id,
                before=before,
                after=obj.model_dump(mode="json"),
                detail={
                    "repair": "chatgpt_model_extraction_mislabelled",
                    "from": str(WRONG),
                    "to": str(TARGET),
                },
            ),
        )
    print(f"relabelled {len(stale)} objects to {TARGET} at {corrected}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
