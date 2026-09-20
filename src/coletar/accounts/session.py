"""Turning a signed-in identity into the account whose graph a request reaches.

This is the application boundary `store/base.py` means when it says only an
application boundary resolves a tenant. Everything below the boundary names a
tenant and never defaults one; everything above it has a token and needs to find
out which graph that token opens. That translation happens here and nowhere else.

The order of the lookups is the security-relevant part:

1. **`(provider, subject)` first.** The provider's own stable handle. An account
   already linked is found here and nothing else runs.

2. **A verified email second, and only to *claim* an unlinked account.** This is
   the documented path off the `local` provider: a workspace built on a laptop is
   claimed the first time its owner signs in for real. It is gated on the provider
   asserting it *verified* the address, because without that gate the path reads
   "type someone else's email into a sign-up form, receive their graph". Clerk will
   happily hold an unverified address on a user; it tells us which kind it is, and
   we are obliged to look.

3. **Provisioning last**, and only for someone the invite list names.

An account that exists and is disabled is refused outright rather than reprovisioned
— otherwise "disable this account" would mean "make them sign in again".
"""

from __future__ import annotations

from coletar.accounts.directory import Directory, DirectoryError
from coletar.accounts.identity import IdentityError, IdentityProvider
from coletar.accounts.models import Account, ExternalIdentity


class NotInvited(RuntimeError):
    """Authenticated by the provider, but not allowed to provision an account.

    Distinct from `IdentityError`: the credential is perfectly good. The answer is
    "not yet", and a caller should say so rather than implying a broken sign-in.
    """


def _allowlist(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def may_provision(email: str, *, allowlist: str, open_registration: bool) -> bool:
    """Whether this address may create a *new* account.

    An empty allowlist with registration closed means closed. Deliberately not
    "empty means everyone": the failure mode of the other reading is that clearing
    a config value silently opens public signup.
    """
    if open_registration:
        return True
    return bool(email) and email.strip().lower() in _allowlist(allowlist)


async def resolve_account(
    credential: str | None,
    *,
    provider: IdentityProvider,
    directory: Directory,
    allowlist: str = "",
    open_registration: bool = False,
) -> Account | None:
    """Resolve a presented credential to its account, provisioning if invited.

    Returns None when nobody is signed in — an ordinary state that the caller
    decides what to do about. Raises `IdentityError` for a credential that was
    presented and is bad, and `NotInvited` for a good one with nowhere to go.
    """
    identity = await provider.verify(credential)
    if identity is None:
        return None

    account = await directory.account_for_identity(identity)
    if account is not None:
        if not account.is_active:
            raise IdentityError("This account is disabled.")
        return account

    claimed = await _claim_by_verified_email(identity, directory=directory)
    if claimed is not None:
        return claimed

    if not may_provision(
        identity.email, allowlist=allowlist, open_registration=open_registration
    ):
        raise NotInvited(
            "coletar is in invite-only beta and this address is not on the list yet."
        )
    return await _provision(identity, directory=directory)


async def _claim_by_verified_email(
    identity: ExternalIdentity, *, directory: Directory
) -> Account | None:
    """Bind an existing, *unlinked* account to this identity. See the module docstring."""
    if not identity.email or not identity.email_verified:
        return None
    existing = await directory.account_by_email(identity.email)
    if existing is None:
        return None
    if not existing.is_active:
        raise IdentityError("This account is disabled.")
    if existing.external_id is not None:
        # Already linked — to a different subject, since step 1 did not find it.
        # Two people are presenting the same address from different provider
        # identities. Refusing is the only safe answer: silently relinking would
        # hand the graph to whoever signed in most recently.
        raise IdentityError(
            "This address is already linked to a different sign-in. "
            "Contact support rather than creating a second account."
        )
    return await directory.link_identity(existing.id, identity)


async def _provision(identity: ExternalIdentity, *, directory: Directory) -> Account:
    """Create the account and the tenant it owns."""
    if not identity.email:
        raise IdentityError(
            "This sign-in carries no email address, and an account is keyed by one."
        )
    try:
        return await directory.create_account(
            identity.email,
            display_name=identity.display_name,
            identity_provider=identity.provider,
            external_id=identity.subject,
        )
    except DirectoryError:
        # Two first requests raced and the other one won. `create_account` refuses
        # duplicates rather than upserting, which is what makes this recoverable:
        # re-resolve and use whatever landed.
        account = await directory.account_for_identity(identity)
        if account is None:
            raise
        return account
