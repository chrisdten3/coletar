"""Accounts: who owns a graph, and what may reach it on their behalf.

Run against both directories, the discipline test_locality.py and test_tenancy.py
already hold, because the whole value of the in-process one is that it behaves like
the real one on a laptop with nothing installed.

The properties worth pinning here are the ones that would be expensive to discover
later: that no credential a human chose is ever stored, that an issued secret is
unrecoverable after the call that made it, that revocation takes effect without a
redeploy, and that identity matches on the provider's stable subject rather than on
an email a provider can change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from coletar.accounts.directory import DirectoryError
from coletar.accounts.memory import InMemoryDirectory
from coletar.accounts.models import ExternalIdentity, hash_secret
from coletar.schema.objects import Provider

EMAIL = "owner@example.com"


@pytest.fixture(params=["memory", "postgres"])
async def directory(request) -> AsyncIterator[object]:
    if request.param == "memory":
        yield InMemoryDirectory()
        return

    import uuid
    from urllib.parse import urlparse, urlunparse

    import psycopg

    from coletar.accounts.postgres import PostgresDirectory
    from coletar.store.migrate import run_migrations

    dsn = request.getfixturevalue("postgres_dsn")  # skips when unreachable
    name = f"coletar_accounts_{uuid.uuid4().hex[:10]}"
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE DATABASE "{name}"')
    scoped = urlunparse(urlparse(dsn)._replace(path=f"/{name}"))
    await run_migrations(scoped)

    backend = PostgresDirectory(scoped)
    try:
        yield backend
    finally:
        await backend.close()
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (name,),
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture
async def account(directory):
    return directory, await directory.create_account(EMAIL, display_name="Owner")


async def test_provisioning_gives_an_account_its_own_tenant(account) -> None:
    directory, owner = account
    assert owner.email == EMAIL
    assert owner.tenant_id.startswith("tenant_")
    assert owner.is_active
    # Unclaimed until an identity provider vouches for it: an account provisioned
    # from the CLI exists before anyone has signed in as it.
    assert owner.identity_provider == "local"
    assert owner.external_id is None

    assert (await directory.account_by_tenant(owner.tenant_id)).id == owner.id
    assert (await directory.account_by_email(EMAIL.upper())).id == owner.id


async def test_the_tenant_is_derived_so_provisioning_is_reproducible(account) -> None:
    """Derived rather than random, so re-provisioning the same person cannot
    silently strand their graph under a second tenant."""
    from coletar.accounts.models import Account

    _, owner = account
    assert Account.tenant_for(" OWNER@Example.com ") == owner.tenant_id


async def test_a_second_account_for_one_address_is_refused(account) -> None:
    """Never an upsert. Quietly returning the existing account would make `create`
    a way to acquire someone else's graph by guessing their address."""
    directory, _ = account
    with pytest.raises(DirectoryError, match="already exists"):
        await directory.create_account(EMAIL)


async def test_an_issued_secret_is_never_stored_and_never_shown_twice(account) -> None:
    directory, owner = account
    issued = await directory.issue_key(owner.id, name="claude", surface=Provider.CLAUDE)

    assert issued.secret
    # What is kept is a hash and a short prefix: enough to tell two keys apart in a
    # list, far too little to reconstruct one.
    assert issued.key.secret_hash == hash_secret(issued.secret)
    assert issued.secret.startswith(issued.key.prefix)
    stored = (await directory.list_keys(owner.id))[0]
    assert issued.secret not in stored.model_dump_json()

    assert (await directory.key_for_secret(issued.secret)).id == issued.key.id
    assert await directory.key_for_secret("not-a-key") is None


async def test_a_key_fixes_its_surface_and_scopes_at_issuance(account) -> None:
    """The locality gate is decided when the key is minted, for the reason
    Principal documents: a connector that could name its own surface is not gated."""
    directory, owner = account
    issued = await directory.issue_key(
        owner.id, name="dashboard", surface=Provider.CHATGPT, scopes=frozenset({"read"})
    )
    assert issued.key.surface is Provider.CHATGPT
    assert issued.key.scopes == frozenset({"read"})
    assert issued.key.tenant_id == owner.tenant_id


async def test_revocation_needs_no_redeploy_and_keeps_its_first_timestamp(account) -> None:
    directory, owner = account
    issued = await directory.issue_key(owner.id, name="claude")

    revoked = await directory.revoke_key(issued.key.id)
    assert not revoked.is_active

    # The key still resolves — the caller needs to tell "revoked" from "never
    # existed" when deciding what to log — but it is no longer active.
    found = await directory.key_for_secret(issued.secret)
    assert found is not None and not found.is_active

    # Revoking twice is not an error, and when it stopped being valid is the
    # auditable fact, so the first timestamp stands.
    again = await directory.revoke_key(issued.key.id)
    assert again.revoked_at == revoked.revoked_at


async def test_identity_matches_the_subject_not_the_email(account) -> None:
    """An email is a claim a provider makes and can change. Matching the mutable
    one is how account takeover by address reuse happens."""
    directory, owner = account
    identity = ExternalIdentity(provider="clerk", subject="user_abc", email=EMAIL)

    assert await directory.account_for_identity(identity) is None

    linked = await directory.link_identity(owner.id, identity)
    assert linked.identity_provider == "clerk"
    assert linked.external_id == "user_abc"
    assert (await directory.account_for_identity(identity)).id == owner.id

    # Same subject, a different address: still the same person.
    moved = ExternalIdentity(provider="clerk", subject="user_abc", email="new@example.com")
    assert (await directory.account_for_identity(moved)).id == owner.id

    # Same address, a different subject: not the same person.
    stranger = ExternalIdentity(provider="clerk", subject="user_xyz", email=EMAIL)
    assert await directory.account_for_identity(stranger) is None


async def test_one_identity_cannot_be_linked_to_two_accounts(account) -> None:
    directory, owner = account
    other = await directory.create_account("second@example.com")
    identity = ExternalIdentity(provider="clerk", subject="user_abc")

    await directory.link_identity(owner.id, identity)
    with pytest.raises(DirectoryError, match="already linked"):
        await directory.link_identity(other.id, identity)


async def test_keys_cannot_be_issued_for_an_account_that_does_not_exist(account) -> None:
    directory, _ = account
    with pytest.raises(DirectoryError, match="No such account"):
        await directory.issue_key("acct_nonexistent", name="claude")


async def test_issued_keys_authenticate_and_revocation_takes_effect_immediately(
    account,
) -> None:
    """The reason keys moved out of an environment variable.

    A static env var can only be changed by a redeploy, so "revoke this key" meant
    "redeploy the server". Nothing here is cached, so the next request is the one
    that fails.
    """
    from coletar.accounts.authenticator import DirectoryAuthenticator

    directory, owner = account
    issued = await directory.issue_key(
        owner.id, name="claude", surface=Provider.CLAUDE, scopes=frozenset({"read"})
    )
    auth = DirectoryAuthenticator(directory)

    principal = await auth.authenticate(issued.secret)
    assert principal is not None
    assert principal.tenant_id == owner.tenant_id
    assert principal.surface is Provider.CLAUDE
    assert principal.can("read") and not principal.can("write")

    # Using a key records that it was used, which is what the key list shows.
    assert (await directory.list_keys(owner.id))[0].last_used_at is not None

    await directory.revoke_key(issued.key.id)
    assert await auth.authenticate(issued.secret) is None
    # A revoked key is refused exactly like an unknown one.
    assert await auth.authenticate("never-existed") is None
    assert await auth.authenticate(None) is None


async def test_the_environment_fallback_lets_a_deployment_migrate_without_a_flag_day(
    account,
) -> None:
    from coletar.accounts.authenticator import DirectoryAuthenticator
    from coletar.mcp.auth import ApiKeyAuthenticator

    directory, owner = account
    legacy = ApiKeyAuthenticator.from_config(
        '[{"id": "legacy", "secret": "sk-legacy", "tenant_id": "tenant_legacy"}]'
    )
    auth = DirectoryAuthenticator(directory, fallback=legacy)

    # Keys already in the environment keep working while accounts are provisioned.
    from_env = await auth.authenticate("sk-legacy")
    assert from_env is not None and from_env.tenant_id == "tenant_legacy"

    issued = await directory.issue_key(owner.id, name="claude")
    from_db = await auth.authenticate(issued.secret)
    assert from_db is not None and from_db.tenant_id == owner.tenant_id


def test_the_local_identity_provider_is_refused_in_a_hosted_deployment() -> None:
    """It trusts whoever can reach the port, which is a laptop assumption.

    Refused loudly rather than falling back, so a hosted workspace cannot end up
    with development auth because nobody changed a setting.
    """
    from coletar.accounts.identity import (
        IdentityError,
        LocalIdentityProvider,
        build_identity_provider,
    )

    assert isinstance(build_identity_provider("local", hosted=False), LocalIdentityProvider)
    with pytest.raises(IdentityError, match="laptop assumption"):
        build_identity_provider("local", hosted=True)
    # A typo must not be the thing that opens a workspace either.
    with pytest.raises(IdentityError, match="Unknown identity provider"):
        build_identity_provider("clrek", hosted=False)


async def test_recording_a_key_as_used_returns_no_rows_and_must_not_pretend_otherwise(
    account,
) -> None:
    """Regression: `touch_key` ran an UPDATE with no RETURNING through the fetching
    helper, so it raised on Postgres and passed in memory.

    It is called on every authenticated request, so the failure mode was every
    connector call erroring in production while the suite stayed green. Pinned
    because the two helpers still look interchangeable at a glance.
    """
    directory, owner = account
    issued = await directory.issue_key(owner.id, name="claude")

    await directory.touch_key(issued.key.id)
    stamped = (await directory.list_keys(owner.id))[0]
    assert stamped.last_used_at is not None

    # Touching twice moves the stamp rather than raising.
    await directory.touch_key(issued.key.id)
    # An unknown key is a no-op, not an error: it is bookkeeping, not a check.
    await directory.touch_key("key_nonexistent")


async def test_a_failed_last_used_stamp_never_costs_a_valid_request(account) -> None:
    """Bookkeeping must not become an outage.

    Refusing a good credential because a timestamp could not be written would turn
    a degraded database into a total one.
    """
    from coletar.accounts.authenticator import DirectoryAuthenticator

    directory, owner = account
    issued = await directory.issue_key(owner.id, name="claude")

    async def failing_touch(key_id: str, *, at: object = None) -> None:
        raise RuntimeError("the stamp could not be written")

    directory.touch_key = failing_touch  # type: ignore[method-assign]
    principal = await DirectoryAuthenticator(directory).authenticate(issued.secret)
    assert principal is not None and principal.tenant_id == owner.tenant_id
