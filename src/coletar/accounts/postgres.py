"""Postgres directory. Same behaviour as the in-process one, held by one suite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from coletar.accounts.directory import Directory, DirectoryError
from coletar.accounts.models import (
    KEY_PREFIX_CHARS,
    LOCAL_IDENTITY,
    Account,
    ApiKey,
    ExternalIdentity,
    IssuedKey,
    generate_secret,
    hash_secret,
)
from coletar.mcp.auth import DEFAULT_SCOPES
from coletar.schema.objects import Provider
from coletar.schema.tenancy import TenantId, tenant_id

_ACCOUNT_COLUMNS = (
    "id, tenant_id, email, display_name, identity_provider, external_id, created_at, disabled_at"
)
_KEY_COLUMNS = (
    "id, account_id, tenant_id, name, surface, scopes, secret_hash, prefix, "
    "created_at, last_used_at, revoked_at"
)


def _account(row: dict[str, Any]) -> Account:
    return Account(
        id=row["id"],
        tenant_id=tenant_id(row["tenant_id"]),
        email=row["email"],
        display_name=row["display_name"],
        identity_provider=row["identity_provider"],
        external_id=row["external_id"],
        created_at=row["created_at"],
        disabled_at=row["disabled_at"],
    )


def _key(row: dict[str, Any]) -> ApiKey:
    return ApiKey(
        id=row["id"],
        account_id=row["account_id"],
        tenant_id=tenant_id(row["tenant_id"]),
        name=row["name"],
        surface=Provider(row["surface"]),
        scopes=frozenset(row["scopes"]),
        secret_hash=row["secret_hash"],
        prefix=row["prefix"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


class PostgresDirectory(Directory):
    def __init__(self, dsn: str, *, pool: AsyncConnectionPool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool

    async def _get_pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            self._pool = AsyncConnectionPool(self._dsn, open=False, min_size=1, max_size=4)
            await self._pool.open(wait=True)
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _fetch(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return list(await cur.fetchall())

    async def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        """For statements that return no rows.

        Separate from `_fetch` rather than a flag on it: a statement without
        RETURNING produces no records, and asking psycopg for them raises. Sharing
        one helper is exactly how that goes unnoticed until a real database sees it.
        """
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)

    # -- accounts -----------------------------------------------------------
    async def create_account(
        self,
        email: str,
        *,
        display_name: str = "",
        identity_provider: str | None = None,
        external_id: str | None = None,
        tenant_id: TenantId | None = None,
    ) -> Account:
        address = email.strip().lower()
        if not address:
            raise DirectoryError("An account needs an email address.")
        account = Account(
            # Derived unless the caller is adopting an existing graph; see the
            # protocol docstring for why that is an explicit argument.
            tenant_id=tenant_id or Account.tenant_for(address),
            email=address,
            display_name=display_name,
            identity_provider=identity_provider or LOCAL_IDENTITY,
            external_id=external_id,
        )
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            # ON CONFLICT DO NOTHING rather than DO UPDATE: quietly returning
            # someone else's account for a guessed address is how a graph gets
            # handed to the wrong person.
            await cur.execute(
                f"INSERT INTO account ({_ACCOUNT_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING id",
                (
                    account.id,
                    str(account.tenant_id),
                    account.email,
                    account.display_name,
                    account.identity_provider,
                    account.external_id,
                    account.created_at,
                    account.disabled_at,
                ),
            )
            if await cur.fetchone() is None:
                raise DirectoryError(f"An account already exists for {address}.")
        return account

    async def account_by_email(self, email: str) -> Account | None:
        rows = await self._fetch(
            f"SELECT {_ACCOUNT_COLUMNS} FROM account WHERE email = %s", (email.strip().lower(),)
        )
        return _account(rows[0]) if rows else None

    async def account_by_id(self, account_id: str) -> Account | None:
        rows = await self._fetch(
            f"SELECT {_ACCOUNT_COLUMNS} FROM account WHERE id = %s", (account_id,)
        )
        return _account(rows[0]) if rows else None

    async def account_by_tenant(self, tenant: TenantId) -> Account | None:
        rows = await self._fetch(
            f"SELECT {_ACCOUNT_COLUMNS} FROM account WHERE tenant_id = %s", (str(tenant),)
        )
        return _account(rows[0]) if rows else None

    async def account_for_identity(self, identity: ExternalIdentity) -> Account | None:
        rows = await self._fetch(
            f"SELECT {_ACCOUNT_COLUMNS} FROM account "
            "WHERE identity_provider = %s AND external_id = %s",
            (identity.provider, identity.subject),
        )
        return _account(rows[0]) if rows else None

    async def link_identity(self, account_id: str, identity: ExternalIdentity) -> Account:
        claimed = await self.account_for_identity(identity)
        if claimed is not None and claimed.id != account_id:
            raise DirectoryError("That identity is already linked to another account.")
        rows = await self._fetch(
            "UPDATE account SET identity_provider = %s, external_id = %s "
            f"WHERE id = %s RETURNING {_ACCOUNT_COLUMNS}",
            (identity.provider, identity.subject, account_id),
        )
        if not rows:
            raise DirectoryError("No such account.")
        return _account(rows[0])

    async def list_accounts(self, *, limit: int = 100) -> list[Account]:
        rows = await self._fetch(
            f"SELECT {_ACCOUNT_COLUMNS} FROM account ORDER BY created_at LIMIT %s", (limit,)
        )
        return [_account(row) for row in rows]

    # -- keys ---------------------------------------------------------------
    async def issue_key(
        self,
        account_id: str,
        *,
        name: str,
        surface: Provider = Provider.COLETAR,
        scopes: frozenset[str] | None = None,
    ) -> IssuedKey:
        account = await self.account_by_id(account_id)
        if account is None:
            raise DirectoryError("No such account.")
        if not account.is_active:
            raise DirectoryError("That account is disabled.")
        secret = generate_secret()
        key = ApiKey(
            account_id=account.id,
            tenant_id=account.tenant_id,
            name=name,
            surface=surface,
            scopes=scopes if scopes is not None else DEFAULT_SCOPES,
            secret_hash=hash_secret(secret),
            prefix=secret[:KEY_PREFIX_CHARS],
        )
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"INSERT INTO api_key ({_KEY_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    key.id,
                    key.account_id,
                    str(key.tenant_id),
                    key.name,
                    str(key.surface),
                    Jsonb(sorted(key.scopes)),
                    key.secret_hash,
                    key.prefix,
                    key.created_at,
                    key.last_used_at,
                    key.revoked_at,
                ),
            )
        return IssuedKey(key=key, secret=secret)

    async def key_for_secret(self, secret: str) -> ApiKey | None:
        rows = await self._fetch(
            f"SELECT {_KEY_COLUMNS} FROM api_key WHERE secret_hash = %s", (hash_secret(secret),)
        )
        return _key(rows[0]) if rows else None

    async def list_keys(self, account_id: str) -> list[ApiKey]:
        rows = await self._fetch(
            f"SELECT {_KEY_COLUMNS} FROM api_key WHERE account_id = %s ORDER BY created_at",
            (account_id,),
        )
        return [_key(row) for row in rows]

    async def revoke_key(self, key_id: str, *, at: datetime | None = None) -> ApiKey:
        rows = await self._fetch(
            # COALESCE so revoking twice keeps the first timestamp: when a key
            # stopped being valid is the auditable fact, not when someone last
            # clicked the button.
            "UPDATE api_key SET revoked_at = COALESCE(revoked_at, %s) "
            f"WHERE id = %s RETURNING {_KEY_COLUMNS}",
            (at or datetime.now(UTC), key_id),
        )
        if not rows:
            raise DirectoryError("No such key.")
        return _key(rows[0])

    async def touch_key(self, key_id: str, *, at: datetime | None = None) -> None:
        await self._execute(
            "UPDATE api_key SET last_used_at = %s WHERE id = %s",
            (at or datetime.now(UTC), key_id),
        )
