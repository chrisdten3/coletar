"""Who is asking, resolved per request, for the web surface.

This module exists to delete a single line. `web.py` used to say:

    def tenant() -> TenantId:
        return tenant_id(get_settings().default_tenant_id)

...and about twenty routes called it. That is a fine answer on a laptop and exactly
the wrong one for a deployment with more than one user: every request resolved to
the same graph regardless of who sent it. Replacing it with a dependency is most of
what "add accounts" mechanically means here.

**Local development keeps the old behaviour deliberately.** With
`COLETAR_IDENTITY_PROVIDER=local` there is no sign-in, no directory lookup and no
account -- the configured tenant is served as before. That is not a gap left open:
the local provider is *refused outright* the moment `COLETAR_PUBLIC_URL` is set (see
`build_identity_provider`), so this shortcut cannot survive into a hosted
deployment. It is what keeps the zero-infrastructure store dogfoodable on day one.

**Hosted fails closed.** No credential is 401, a bad one is 401, and an
uninvited-but-authentic one is 403 with a different message, because "your sign-in
is broken" and "you are not on the list yet" send a person to two different places.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from coletar.accounts import (
    Account,
    IdentityError,
    NotInvited,
    build_directory,
    build_identity,
    resolve_account,
)
from coletar.accounts.models import LOCAL_IDENTITY
from coletar.config import get_settings
from coletar.schema.tenancy import TenantId, tenant_id

#: Clerk's cookie name when the app is served from the same site as the Clerk
#: instance. The Authorization header is preferred -- it is what
#: `Clerk.session.getToken()` yields and it is immune to CSRF by construction --
#: but the cookie makes a plain page load work before any JavaScript has run.
SESSION_COOKIE = "__session"


def presented_credential(request: Request) -> str | None:
    """The session token on this request, if any."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(SESSION_COOKIE) or None


def local_mode() -> bool:
    """True when there is no identity provider because none was asked for."""
    settings = get_settings()
    return settings.identity_provider in {"", LOCAL_IDENTITY} and not settings.public_url


async def current_account(request: Request) -> Account | None:
    """The signed-in account, or None in local mode.

    None is *only* returned in local mode. In a hosted deployment this either
    returns an account or raises -- there is no anonymous path left.
    """
    if local_mode():
        return None
    settings = get_settings()
    try:
        account = await resolve_account(
            presented_credential(request),
            provider=build_identity(),
            directory=build_directory(),
            allowlist=settings.invite_allowlist,
            open_registration=settings.open_registration,
        )
    except NotInvited as exc:
        raise HTTPException(403, str(exc)) from exc
    except IdentityError as exc:
        raise HTTPException(401, str(exc)) from exc
    if account is None:
        raise HTTPException(401, "Sign in to reach this workspace.")
    return account


async def current_tenant(
    account: Annotated[Account | None, Depends(current_account)],
) -> TenantId:
    """Whose graph this request reaches. The only source of a tenant on this surface."""
    if account is not None:
        return account.tenant_id
    # Local mode only; see the module docstring for why this cannot leak into a
    # hosted deployment.
    return tenant_id(get_settings().default_tenant_id)


#: The annotation every route uses. Spelled once so a new route cannot quietly
#: reintroduce a configured-tenant default by writing a different signature.
Tenant = Annotated[TenantId, Depends(current_tenant)]
Viewer = Annotated[Account | None, Depends(current_account)]
