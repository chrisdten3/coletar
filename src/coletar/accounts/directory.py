"""The account directory: the boundary that resolves a tenant.

`Store` is deliberately unable to do this. Its rule is that every method names a
tenant and none of them defaults one, because a default is how a background job
silently falls into somebody else's graph. Looking an account up is inherently
cross-tenant — you have an email or a token subject and you want to know *whose*
graph to open — so it cannot live behind that protocol without weakening the one
invariant the protocol exists to hold. It lives here instead, and this module is
what `base.py` means by "only application boundaries resolve one".

Two implementations, matching the two stores: one in-process so the wedge still
runs with no infrastructure, one on Postgres. They are held to identical behaviour
by the same suite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from coletar.accounts.models import Account, ApiKey, ExternalIdentity, IssuedKey
from coletar.schema.objects import Provider
from coletar.schema.tenancy import TenantId


class DirectoryError(RuntimeError):
    """A refusal the caller can act on, phrased for a person."""


@runtime_checkable
class Directory(Protocol):
    async def create_account(
        self,
        email: str,
        *,
        display_name: str = "",
        identity_provider: str | None = None,
        external_id: str | None = None,
    ) -> Account:
        """Provision an account and the tenant it owns.

        Raises `DirectoryError` if the email is already taken. Idempotent
        provisioning is the caller's job, not a silent upsert here: quietly
        returning an existing account would make "create" a way to acquire someone
        else's graph by guessing their address.
        """
        ...

    async def account_by_email(self, email: str) -> Account | None: ...

    async def account_by_id(self, account_id: str) -> Account | None: ...

    async def account_by_tenant(self, tenant_id: TenantId) -> Account | None:
        """Whose graph this is. The inverse of the lookup every request does."""
        ...

    async def account_for_identity(self, identity: ExternalIdentity) -> Account | None:
        """Resolve a signed-in identity to an account.

        Matches on `(provider, subject)` — never on email alone. An email is a
        claim a provider makes and can change; the subject is the provider's own
        stable handle, and matching on the mutable one is how account takeover by
        address reuse happens.
        """
        ...

    async def link_identity(self, account_id: str, identity: ExternalIdentity) -> Account:
        """Bind an external identity to an existing account.

        The migration path off the local provider: an account provisioned from the
        CLI is claimed the first time its owner signs in through a real one.
        """
        ...

    async def list_accounts(self, *, limit: int = 100) -> list[Account]: ...

    async def issue_key(
        self,
        account_id: str,
        *,
        name: str,
        surface: Provider = Provider.COLETAR,
        scopes: frozenset[str] | None = None,
    ) -> IssuedKey:
        """Mint a key. The plaintext is returned here and never obtainable again."""
        ...

    async def key_for_secret(self, secret: str) -> ApiKey | None:
        """Look a presented credential up by its hash.

        Returns revoked keys too, so the caller can tell "revoked" from "never
        existed" when it decides what to log. Both are refused.
        """
        ...

    async def list_keys(self, account_id: str) -> list[ApiKey]: ...

    async def revoke_key(self, key_id: str, *, at: datetime | None = None) -> ApiKey: ...

    async def touch_key(self, key_id: str, *, at: datetime | None = None) -> None:
        """Record that a key was used. Best-effort: never fail a request for it."""
        ...
