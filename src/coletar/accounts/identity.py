"""The identity seam: what has to change to adopt Clerk or Supabase Auth, and what does not.

The whole of coletar's dependency on an identity provider is this file. A provider's
job is to turn whatever a browser presents — a session cookie, a bearer JWT — into an
`ExternalIdentity`: who vouched, their stable subject, and optionally an email. That
is the entire contract.

Adopting Clerk or Supabase Auth is therefore:

  1. Implement `verify` against that provider's JWKS. Both issue standard JWTs, so
     this is a signature check and a claims read — roughly twenty lines.
  2. Point `COLETAR_IDENTITY_PROVIDER` at it.

Nothing else moves. No credential migrates, because none is stored. No account rows
change, because accounts already carry `(identity_provider, external_id)` and match
on the pair. Accounts provisioned under `local` are claimed by `link_identity` the
first time their owner signs in through the real provider, so a workspace built on a
laptop today survives the switch.

The reason this is a seam rather than a TODO is that the alternative — password
columns now, ripped out later — would leave real user secrets in a schema and in
backups long after the code stopped reading them.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from coletar.accounts.models import LOCAL_IDENTITY, ExternalIdentity

#: Duplicated from `clerk.py` so this module can dispatch without importing it.
CLERK_IDENTITY = "clerk"


class IdentityError(RuntimeError):
    """The presented credential is not usable. Never raised for a *missing* one."""


@runtime_checkable
class IdentityProvider(Protocol):
    @property
    def name(self) -> str:
        """Recorded on the account, so a row says who vouched for it."""
        ...

    async def verify(self, credential: str | None) -> ExternalIdentity | None:
        """Resolve a presented credential, or None if there is none to resolve.

        None means "nobody is signed in", which is an ordinary state — the hosted
        workspace serves anonymous readers today. Raise `IdentityError` only for a
        credential that was presented and is *bad*, so that the two cases stay
        distinguishable to a caller deciding what to log.
        """
        ...


class LocalIdentityProvider(IdentityProvider):
    """Single-operator development. Trusts the machine, because the machine is the boundary.

    Deliberately not a login: there is no password to check because there is no
    password anywhere in coletar. On a laptop, whoever can reach the loopback port
    is already the owner of the files the store is written to, so a credential here
    would be ceremony rather than security. It is refused outright in a hosted
    deployment (see `build_identity_provider`), so this cannot become production
    auth by forgetting to change a setting.
    """

    def __init__(self, subject: str = "local-owner") -> None:
        self._subject = subject

    @property
    def name(self) -> str:
        return LOCAL_IDENTITY

    async def verify(self, credential: str | None) -> ExternalIdentity | None:
        return ExternalIdentity(provider=LOCAL_IDENTITY, subject=self._subject)


def build_identity_provider(name: str, *, hosted: bool) -> IdentityProvider:
    """Resolve the configured provider, refusing the local one where it is unsafe."""
    if name in {"", LOCAL_IDENTITY}:
        if hosted:
            raise IdentityError(
                "The local identity provider trusts whoever can reach the port, which "
                "is a laptop assumption. Configure COLETAR_IDENTITY_PROVIDER before "
                "putting accounts behind a hosted deployment."
            )
        return LocalIdentityProvider()
    if name == CLERK_IDENTITY:
        # Imported here rather than at module scope so the seam keeps its promise:
        # nothing in coletar depends on a provider's library until one is chosen.
        from coletar.accounts.clerk import build_clerk_provider

        return build_clerk_provider()
    # Supabase Auth lands here next. Kept as an explicit refusal rather than a
    # silent fallback to `local`: a typo in this setting must not be the thing
    # that opens a hosted workspace.
    raise IdentityError(
        f"Unknown identity provider {name!r}. Implement IdentityProvider and register "
        "it here; see this module's docstring for what that involves."
    )
