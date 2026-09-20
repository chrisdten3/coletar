"""Row-level security, asserted over the whole schema rather than a list.

Migration 008 enabled RLS on the eight tables that existed when it was written, and
was verified by hand against Supabase's real `anon` and `authenticated` roles. The
hand-verification is why this file exists: migrations 010 and 011 then added
`account`, `api_key` and `extraction_checkpoint`, none of them enabled RLS, and
nothing noticed for two milestones. `api_key` holds the hash and prefix of every
issued connector credential.

A property checked by a human against a list someone maintains is a property that
reopens the next time the list is not updated. So these tests derive the table set
from `pg_class` at runtime. A new migration that adds a table without RLS fails
here, before it reaches a database that Supabase has handed public roles to.

The gate is a reachable Postgres, not a mock: RLS is a server behaviour and a
mocked cursor would assert nothing about it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from coletar.store.migrate import run_migrations

#: Tables deliberately exempt. Empty, and a test below pins that it is empty, so
#: that adding an exemption is a visible decision with a reason attached rather
#: than a quiet edit.
EXEMPT: frozenset[str] = frozenset()


def _with_database(dsn: str, name: str) -> str:
    from urllib.parse import urlparse, urlunparse

    parts = urlparse(dsn)
    return urlunparse(parts._replace(path=f"/{name}"))


@pytest.fixture
async def migrated_database(postgres_dsn: str) -> AsyncIterator[str]:
    """A database with every migration applied, dropped afterwards."""
    name = f"coletar_rls_{uuid.uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE DATABASE "{name}"')
    target = _with_database(postgres_dsn, name)
    try:
        await run_migrations(target)
        yield target
    finally:
        async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (name,),
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


async def _tables(dsn: str) -> list[tuple[str, bool]]:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cur = await conn.execute(
            """
            SELECT c.relname, c.relrowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
            """
        )
        return [(row[0], row[1]) for row in await cur.fetchall()]


async def test_every_table_has_row_level_security(migrated_database: str):
    """The whole point. Derived from the catalogue, so a new table is covered by
    existing here rather than by someone remembering to add it."""
    tables = await _tables(migrated_database)
    assert tables, "no tables found — the migration fixture did not run"

    missing = sorted(name for name, enabled in tables if not enabled and name not in EXEMPT)
    assert not missing, (
        "these tables have row-level security disabled: "
        f"{', '.join(missing)}. Supabase grants its public API roles table "
        "privileges by default, so a table without RLS is readable by anon and "
        "authenticated. Add an ALTER TABLE ... ENABLE ROW LEVEL SECURITY to a new "
        "migration; see 008 and 012."
    )


async def test_the_tables_added_after_008_are_covered(migrated_database: str):
    """A named regression for the three that were actually missed.

    The test above would catch these, but it would also pass if someone deleted
    them. These are the rows whose exposure was real.
    """
    enabled = {name: on for name, on in await _tables(migrated_database)}
    for table in ("account", "api_key", "extraction_checkpoint"):
        assert table in enabled, f"{table} is missing from the schema"
        assert enabled[table], f"{table} lost row-level security"


async def test_no_client_policies_exist(migrated_database: str):
    """RLS with a permissive policy is RLS turned off with extra steps.

    008 and 012 both enable it with *no* policies, which is what makes anon and
    authenticated unable to read even where grants exist. The backend connects as
    the table owner and is unaffected. A direct browser database API would need
    explicit, tenant-scoped policies — and adding one should break this test and be
    argued for, not slip in.
    """
    async with await psycopg.AsyncConnection.connect(migrated_database) as conn:
        cur = await conn.execute(
            "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public'"
        )
        policies = await cur.fetchall()
    assert not policies, (
        f"unexpected RLS policies: {policies}. These tables are reached through the "
        "backend's tenant and locality checks, not directly by a browser client."
    )


async def test_the_exemption_list_is_empty(migrated_database: str):
    """No table has earned an exemption. If one ever does, this fails and the
    reason has to be written down next to it."""
    assert frozenset() == EXEMPT


async def test_rls_actually_blocks_a_non_owner_role(migrated_database: str):
    """The property end to end, against a real unprivileged role.

    Everything above reads the catalogue, which is a statement about configuration.
    This one reproduces Supabase's actual shape — a role holding table grants, the
    way `anon` and `authenticated` do — and confirms the grants do not get it in.
    Without this, a misunderstanding of what `ENABLE ROW LEVEL SECURITY` does would
    leave every assertion above passing and the data still readable.
    """
    role = f"anon_probe_{uuid.uuid4().hex[:8]}"
    async with await psycopg.AsyncConnection.connect(migrated_database, autocommit=True) as conn:
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
        # Exactly what Supabase does for its public API roles.
        await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await conn.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
        )
        try:
            await conn.execute(
                "INSERT INTO account (id, tenant_id, email) VALUES ('acct_probe', "
                "'tenant_probe', 'probe@example.com')"
            )
            await conn.execute(f'SET ROLE "{role}"')
            cur = await conn.execute("SELECT count(*) FROM account")
            visible = (await cur.fetchone())[0]
            await conn.execute("RESET ROLE")
            assert visible == 0, (
                "a role with table grants could read `account` rows. RLS is enabled "
                "in the catalogue but is not taking effect."
            )
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(
                f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"'
            )
            await conn.execute(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
            await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
