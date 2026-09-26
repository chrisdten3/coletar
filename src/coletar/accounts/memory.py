"""In-process directory. No infrastructure, same behaviour as the Postgres one.

Exists for the same reason `InMemoryStore` does: the wedge has to be usable on day
one on a laptop with nothing installed. Persists alongside the snapshot store when
given a path, so a locally provisioned account survives a restart.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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
from coletar.schema.tenancy import TenantId

SNAPSHOT_VERSION = 1


class InMemoryDirectory(Directory):
    def __init__(self, path: Path | str | None = None) -> None:
        self._accounts: dict[str, Account] = {}
        self._keys: dict[str, ApiKey] = {}
        self._path = Path(path) if path else None
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        raw = json.loads(self._path.read_text())
        self._accounts = {a["id"]: Account.model_validate(a) for a in raw.get("accounts", [])}
        self._keys = {k["id"]: ApiKey.model_validate(k) for k in raw.get("keys", [])}

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "version": SNAPSHOT_VERSION,
                    "accounts": [a.model_dump(mode="json") for a in self._accounts.values()],
                    "keys": [k.model_dump(mode="json") for k in self._keys.values()],
                },
                indent=2,
            )
        )

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
        if await self.account_by_email(address):
            raise DirectoryError(f"An account already exists for {address}.")
        account = Account(
            # Derived unless the caller is adopting an existing graph; see the
            # protocol docstring for why that is an explicit argument.
            tenant_id=tenant_id or Account.tenant_for(address),
            email=address,
            display_name=display_name,
            identity_provider=identity_provider or LOCAL_IDENTITY,
            external_id=external_id,
        )
        self._accounts[account.id] = account
        self._save()
        return account

    async def account_by_email(self, email: str) -> Account | None:
        address = email.strip().lower()
        return next((a for a in self._accounts.values() if a.email == address), None)

    async def account_by_id(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    async def account_by_tenant(self, tenant_id: TenantId) -> Account | None:
        return next((a for a in self._accounts.values() if a.tenant_id == tenant_id), None)

    async def account_for_identity(self, identity: ExternalIdentity) -> Account | None:
        return next(
            (
                a
                for a in self._accounts.values()
                if a.identity_provider == identity.provider and a.external_id == identity.subject
            ),
            None,
        )

    async def link_identity(self, account_id: str, identity: ExternalIdentity) -> Account:
        account = self._accounts.get(account_id)
        if account is None:
            raise DirectoryError("No such account.")
        claimed = await self.account_for_identity(identity)
        if claimed is not None and claimed.id != account_id:
            raise DirectoryError("That identity is already linked to another account.")
        updated = account.model_copy(
            update={"identity_provider": identity.provider, "external_id": identity.subject}
        )
        self._accounts[account_id] = updated
        self._save()
        return updated

    async def list_accounts(self, *, limit: int = 100) -> list[Account]:
        return sorted(self._accounts.values(), key=lambda a: a.created_at)[:limit]

    # -- keys ---------------------------------------------------------------
    async def issue_key(
        self,
        account_id: str,
        *,
        name: str,
        surface: Provider = Provider.COLETAR,
        scopes: frozenset[str] | None = None,
    ) -> IssuedKey:
        account = self._accounts.get(account_id)
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
        self._keys[key.id] = key
        self._save()
        return IssuedKey(key=key, secret=secret)

    async def key_for_secret(self, secret: str) -> ApiKey | None:
        digest = hash_secret(secret)
        return next((k for k in self._keys.values() if k.secret_hash == digest), None)

    async def list_keys(self, account_id: str) -> list[ApiKey]:
        return sorted(
            (k for k in self._keys.values() if k.account_id == account_id),
            key=lambda k: k.created_at,
        )

    async def revoke_key(self, key_id: str, *, at: datetime | None = None) -> ApiKey:
        key = self._keys.get(key_id)
        if key is None:
            raise DirectoryError("No such key.")
        # Revoking twice is not an error: the caller wants it dead, and it is.
        if key.revoked_at is None:
            key = key.model_copy(update={"revoked_at": at or datetime.now(UTC)})
            self._keys[key_id] = key
            self._save()
        return key

    async def touch_key(self, key_id: str, *, at: datetime | None = None) -> None:
        key = self._keys.get(key_id)
        if key is None:
            return
        self._keys[key_id] = key.model_copy(update={"last_used_at": at or datetime.now(UTC)})
        self._save()
