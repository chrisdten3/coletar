#!/usr/bin/env python
"""Copy one tenant's graph between two Postgres databases, row for row.

Why this exists: graphs predate the hosted deployment. A corpus built and mined on a
laptop is the only real one there is, and a demo of a portable AI workspace that
shows an empty workspace is a demo of nothing. This moves one tenant up without
touching any other tenant in either database.

**It preserves ids, timestamps, embeddings and the event chain.** That is the whole
point rather than an implementation detail: the Event/Revision Log *is* the
provenance record (AGENTS.md constraint 5), so a "migration" that re-created objects
through the write path would stamp today's date on facts recorded in August and
replace 16,000 real events with 3,800 synthetic ones. The Context Inspector would
then be explaining a history that did not happen.

**Nothing is deleted, here or at the source.** The source database is opened
read-only in the sense that this issues only SELECTs against it, and the destination
is appended to. A tenant that already has rows in the destination is refused rather
than merged — see `--force`.

Embeddings copy as text rather than binary, because binary COPY requires the
`vector` type to be registered identically at both ends and its wire format is not
something to bet a one-off migration on. Text is slower and cannot silently
misinterpret a column.

Usage:

    uv run python scripts/copy_tenant.py \
        --source "$COLETAR_DATABASE_URL" \
        --dest "$COLETAR_DEPLOY_DATABASE_URL" \
        --tenant tenant_v1

    # See what would move, touch nothing:
    uv run python scripts/copy_tenant.py ... --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys

import psycopg

#: Copied in this order, because the destination has foreign keys. `context_object`
#: before anything that references an object; `event_log` last because it is the
#: largest and a failure there is the cheapest to retry.
#:
#: Deliberately omitted:
#:   job_lease             — transient worker coordination, meaningless elsewhere
#:   schema_migration      — the destination's own ledger, never someone else's
TABLES: tuple[str, ...] = (
    "context_object",
    "object_embedding",
    "object_content_key",
    "context_edge",
    "extraction_checkpoint",
    "compile_run",
    "event_log",
)


async def counts(dsn: str, tenant: str) -> dict[str, int]:
    """How many rows each table holds for this tenant. Also proves the DSN works."""
    out: dict[str, int] = {}
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        for table in TABLES:
            try:
                cur = await conn.execute(
                    f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (tenant,)
                )
                row = await cur.fetchone()
                out[table] = int(row[0]) if row else 0
            except psycopg.errors.UndefinedTable:
                # A destination a migration behind. Named rather than skipped
                # silently, so "0 rows copied" is never mistaken for "nothing to do".
                await conn.rollback()
                out[table] = -1
    return out


async def copy_table(source: str, dest: str, table: str, tenant: str) -> int:
    """Stream one table's rows for one tenant. Returns rows written."""
    buffer = io.StringIO()
    async with await psycopg.AsyncConnection.connect(source) as src, src.cursor().copy(
        f"COPY (SELECT * FROM {table} WHERE tenant_id = '{tenant}') "
        "TO STDOUT (FORMAT CSV, HEADER FALSE)"
    ) as copy:
        async for chunk in copy:
            buffer.write(bytes(chunk).decode("utf-8"))
    payload = buffer.getvalue()
    if not payload:
        return 0

    written = 0
    async with await psycopg.AsyncConnection.connect(dest) as dst:
        async with dst.cursor() as cur:
            async with cur.copy(f"COPY {table} FROM STDIN (FORMAT CSV, HEADER FALSE)") as copy:
                await copy.write(payload)
            written = cur.rowcount if cur.rowcount and cur.rowcount > 0 else payload.count("\n")
        await dst.commit()
    return written


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="DSN to read from.")
    parser.add_argument("--dest", required=True, help="DSN to write to.")
    parser.add_argument("--tenant", required=True, help="The tenant id to move.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would move and write nothing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Append even though the destination already holds rows for this tenant. "
            "Off by default because the usual cause is a half-finished earlier run, "
            "and appending on top of one duplicates whatever did land."
        ),
    )
    args = parser.parse_args()

    print(f"tenant {args.tenant}\n")
    source_counts = await counts(args.source, args.tenant)
    dest_counts = await counts(args.dest, args.tenant)

    print(f"{'table':<24} {'source':>9} {'dest before':>12}")
    for table in TABLES:
        s, d = source_counts[table], dest_counts[table]
        print(f"{table:<24} {s:>9} {'missing' if d < 0 else d:>12}")

    missing = [t for t, c in dest_counts.items() if c < 0]
    if missing:
        print(f"\nThe destination has no {', '.join(missing)}. Run migrations there first.")
        return 1

    occupied = {t: c for t, c in dest_counts.items() if c > 0}
    if occupied and not args.force:
        print(
            f"\nRefusing: the destination already holds rows for {args.tenant} "
            f"({', '.join(f'{t}={c}' for t, c in occupied.items())}).\n"
            "Clear them or pass --force if you are certain appending is what you want."
        )
        return 1

    total = sum(c for c in source_counts.values() if c > 0)
    if not total:
        print(f"\nNothing to copy — {args.tenant} has no rows in the source.")
        return 1

    if args.dry_run:
        print(f"\nDry run: {total} rows would move. Nothing written.")
        return 0

    print()
    moved = 0
    for table in TABLES:
        if source_counts[table] <= 0:
            continue
        written = await copy_table(args.source, args.dest, table, args.tenant)
        moved += written
        print(f"  {table:<24} {written:>9} rows")

    print(f"\nCopied {moved} rows.")
    after = await counts(args.dest, args.tenant)
    mismatched = [
        t for t in TABLES if source_counts[t] > 0 and after[t] != source_counts[t]
    ]
    if mismatched:
        print(f"Row counts differ after copy: {', '.join(mismatched)}. Check before relying on it.")
        return 1
    print("Destination row counts match the source.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
