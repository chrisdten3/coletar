"""Migration runner.

Small on purpose. The schema lives in `migrations/*.sql` as plain SQL a reviewer can
read without running anything, and this applies them in filename order against a
ledger table so a re-run is a no-op. Nothing here ever drops or alters data --
migrations that would need to are a conversation, not a script.

One run at a time, enforced by an advisory lock rather than by convention. This is
reachable from a developer's shell, from a container host's release command and now
from an operator endpoint (`/api/jobs/migrate`), so "nobody would run two at once"
stopped being a safe assumption the moment there was more than one way to run it.
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

#: The advisory-lock key every migration run takes before touching anything.
#:
#: The ledger makes a *sequential* re-run a no-op; it does nothing about two runs
#: at once, which each see the same migration unapplied and each try to apply it.
#: One then fails on the ledger's primary key, or -- worse, for a migration whose
#: DDL is not itself idempotent -- on the DDL, leaving a half-applied schema and a
#: stack trace that does not say why.
#:
#: Advisory locks share one namespace per database, so the number is arbitrary and
#: matters only in that nothing else here uses it. It is session-scoped rather than
#: transaction-scoped deliberately: the run commits once per migration, and a
#: transaction lock would be released by the first of those commits.
_LOCK_KEY = 8_374_461_002


class MigrationInProgress(RuntimeError):
    """Another connection is already applying migrations to this database.

    Not an error to retry blindly and not a failure of the caller's request: the
    work is being done, by someone else, and the right answer is to wait and look
    again rather than to start a second run behind the first.
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
    """Apply every unapplied migration. Returns the filenames actually applied.

    Raises `MigrationInProgress` when another run holds the lock. Refusing beats
    waiting: the caller may be a request with a deadline, and "someone else is
    already doing this" is an answer, where a connection blocked on a lock until
    the platform kills it is not.
    """
    import psycopg

    applied: list[str] = []
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.cursor() as cur:
        await cur.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,))
        held = await cur.fetchone()
        if held is None or not held[0]:
            raise MigrationInProgress(
                "Another connection is applying migrations to this database. "
                "Wait for it to finish and check the schema_migration ledger."
            )
        # Released when this connection closes, which the context manager does on
        # every path out of here, success or not.

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
