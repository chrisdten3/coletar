"""Restore when each object was actually said, from the archives it came from.

The importers parsed every message's timestamp and then never assigned it, so the
whole graph carries the import date instead. That collapses years of history onto
one instant, and the bitemporal model goes with it: "what did the graph believe on
3 March" is unanswerable when everything was believed at once, supersession cannot
tell which of two statements is newer, and retrieval cannot prefer a current answer
over one the user outgrew years ago.

Objects already record which node they came from in `provenance.source_object_ids`,
so the archives can be re-read for timestamps and matched without re-extracting
anything or calling a model. Each change appends an event with full before/after
state — a claim about the record, corrected in place (constraints 4, 5, 6).

    uv run python scripts/backfill_said_at.py --tenant tenant_v1 \\
        --claude-export DIR --chatgpt-archive FILE.zip
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.tenancy import tenant_id as parse_tenant
from coletar.store import build_store


def claude_times(root: Path) -> dict[str, datetime]:
    """node id -> when it was said, from an unpacked Claude export."""
    import json

    from coletar.acquisition.claude_export import CONVERSATIONS, parse_conversation

    path = root / CONVERSATIONS
    if not path.exists():
        return {}
    out: dict[str, datetime] = {}
    for raw in json.loads(path.read_text(encoding="utf-8")):
        if not isinstance(raw, dict):
            continue
        conversation = parse_conversation(raw)
        if conversation is None:
            continue
        for message in conversation.messages:
            if message.created_at and message.message_id:
                out[message.message_id] = message.created_at
    return out


def chatgpt_times(archive: Path) -> dict[str, datetime]:
    from coletar.acquisition.chatgpt_export import read_export

    out: dict[str, datetime] = {}
    for conversation in read_export(archive):
        for message in conversation.messages:
            if message.created_at and message.node_id:
                out[message.node_id] = message.created_at
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--claude-export", type=Path)
    parser.add_argument("--chatgpt-archive", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    times: dict[str, datetime] = {}
    if args.claude_export:
        times |= claude_times(args.claude_export)
        print(f"claude:  {len(times)} timestamped turns")
    if args.chatgpt_archive:
        before = len(times)
        times |= chatgpt_times(args.chatgpt_archive)
        print(f"chatgpt: {len(times) - before} timestamped turns")
    if not times:
        print("No timestamps found; nothing to do.")
        return 1

    store = build_store()
    tenant = parse_tenant(args.tenant)
    objects = await store.list_objects(
        tenant, limit=200_000, include_retired=True, include_superseded=True
    )

    matched = [
        (o, next((times[s] for s in o.provenance.source_object_ids if s in times), None))
        for o in objects
    ]
    fixable = [(o, at) for o, at in matched if at is not None and o.created_at != at]
    print(f"{len(fixable)} of {len(objects)} objects can be dated from their source")
    if fixable:
        spread = sorted(at for _, at in fixable)
        print(f"range: {spread[0]:%Y-%m-%d} to {spread[-1]:%Y-%m-%d}")
    if args.dry_run or not fixable:
        return 0

    for obj, at in fixable:
        before_state = obj.model_dump(mode="json")
        obj.created_at = at
        obj.updated_at = at
        obj.provenance.captured_at = at
        await store.put_object(
            tenant,
            obj,
            event=Event(
                type=EventType.OBJECT_UPDATED,
                actor=Actor.SYSTEM,
                object_id=obj.id,
                before=before_state,
                after=obj.model_dump(mode="json"),
                detail={"repair": "said_at_backfilled_from_source_archive"},
            ),
        )

    # `put_object`'s upsert deliberately does not update `created_at`: for an
    # ordinary write, when an object was created is not something a later write
    # gets to change, and protecting it is correct. This repair is the exception
    # the rule does not cover — the stored date is not the object's creation date,
    # it is the import's, and correcting the record is the whole point.
    #
    # The one place in this repository that reaches past the Store protocol, and
    # only because the protocol is right to refuse. Every change still went through
    # `put_object` above and still appended its event, so history and provenance are
    # intact; this statement fixes the single column that upsert declines to touch.
    await _force_created_at(store, tenant, fixable)
    print(f"dated {len(fixable)} objects from their source turns")
    return 0


async def _force_created_at(store: object, tenant: object, rows: list) -> None:
    from coletar.store.postgres import PostgresStore

    if not isinstance(store, PostgresStore):
        # The in-process store holds live objects, so the assignment above already
        # took effect and its snapshot has been written.
        return
    pool = await store._get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.executemany(
            "UPDATE context_object SET created_at = %s WHERE tenant_id = %s AND id = %s",
            [(at, str(tenant), obj.id) for obj, at in rows],
        )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
