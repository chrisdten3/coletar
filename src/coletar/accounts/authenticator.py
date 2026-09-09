"""Bearer keys resolved from the directory rather than from an environment variable.

The replacement for `COLETAR_MCP_API_KEYS`, and the difference that matters is
revocation: a static env var can only be changed by a redeploy, so "revoke this
key" meant "redeploy the server". Here it is a row update that takes effect on the
next request.

Nothing is cached. A per-request lookup is the price of that property, and it is one
indexed read on a hash column — cheaper than the retrieval every authenticated call
is about to do.
"""

from __future__ import annotations

from contextlib import suppress
from inspect import isawaitable

from coletar.accounts.directory import Directory
from coletar.mcp.auth import Authenticator, Principal
from coletar.schema.tenancy import TenantId


class DirectoryAuthenticator:
    """Authenticate against issued keys. Falls back to a configured authenticator.

    The fallback exists so a deployment can move to issued keys without a flag day:
    keys already in the environment keep working while the accounts they belong to
    are provisioned. It is checked *second*, so an issued key always wins over a
    stale environment entry with the same secret.
    """

    def __init__(self, directory: Directory, *, fallback: Authenticator | None = None) -> None:
        self._directory = directory
        self._fallback = fallback

    @property
    def tenants(self) -> set[TenantId]:
        # Reported at startup for a misconfiguration check. The directory's answer
        # changes as accounts are provisioned, so this reports only what the
        # fallback knows — the directory is not a fixed set and pretending it is
        # would make the startup line a lie the first time someone signs up.
        return set(self._fallback.tenants) if self._fallback is not None else set()

    async def authenticate(self, credential: str | None) -> Principal | None:
        if not credential:
            return None
        key = await self._directory.key_for_secret(credential)
        if key is not None:
            if not key.is_active:
                # A revoked key is refused like an unknown one. The distinction is
                # available to a caller that wants to log it; it must never change
                # the answer.
                return None
            account = await self._directory.account_by_id(key.account_id)
            if account is None or not account.is_active:
                return None
            # Best-effort, and guarded here because this is the call that must not
            # fail: refusing a valid credential because a last-used stamp could not
            # be written would turn a bookkeeping problem into an outage.
            with suppress(Exception):
                await self._directory.touch_key(key.id)
            return Principal(
                id=key.id,
                tenant_id=key.tenant_id,
                scopes=key.scopes,
                surface=key.surface,
            )
        if self._fallback is None:
            return None
        resolved = self._fallback.authenticate(credential)
        return await resolved if isawaitable(resolved) else resolved
