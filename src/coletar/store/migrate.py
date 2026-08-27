"""Migration runner.

Small on purpose. The schema lives in `migrations/*.sql` as plain SQL a reviewer can
read without running anything, and this applies them in filename order against a
ledger table so a re-run is a no-op. Nothing here ever drops or alters data --
migrations that would need to are a conversation, not a script.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migration (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True)
class Migration:
    filename: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()[:16]


def discover(directory: Path | None = None) -> list[Migration]:
    directory = directory or MIGRATIONS_DIR
    return [
        Migration(filename=path.name, sql=path.read_text())
        for path in sorted(directory.glob("*.sql"))
    ]


async def run_migrations(dsn: str, *, directory: Path | None = None) -> list[str]:
    """Apply every unapplied migration. Returns the filenames actually applied."""
    import psycopg

    applied: list[str] = []
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.cursor() as cur:
        await cur.execute(_LEDGER)
        await conn.commit()

        for migration in discover(directory):
            await cur.execute(
                "SELECT checksum FROM schema_migration WHERE filename = %s",
                (migration.filename,),
            )
            row = await cur.fetchone()
            if row is not None:
                if row[0] != migration.checksum:
                    # Editing an applied migration silently diverges every
                    # deployment from every other one. Refuse rather than guess.
                    raise RuntimeError(
                        f"{migration.filename} changed after it was applied "
                        f"(recorded {row[0]}, now {migration.checksum}). Add a new "
                        f"migration instead of editing an applied one."
                    )
                continue

            await cur.execute(migration.sql)
            await cur.execute(
                "INSERT INTO schema_migration (filename, checksum) VALUES (%s, %s)",
                (migration.filename, migration.checksum),
            )
            await conn.commit()
            applied.append(migration.filename)
    return applied
