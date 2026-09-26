"""Who is asking, resolved per request, for the web surface.

This module exists to delete a single line. `web.py` used to say:

    def tenant() -> TenantId:
        return tenant_id(get_settings().default_tenant_id)

...and about twenty routes called it. That is a fine answer on a laptop and exactly
the wrong one for a deployment with more than one user: every request resolved to
the same graph regardless of who sent it. Replacing it with a dependency is most of
what "add accounts" mechanically means here.

**Local development keeps the old behaviour deliberately.** On loopback, with no
provider configured, there is no sign-in, no directory lookup and no account --
the configured tenant is served as before. It is what keeps the
zero-infrastructure store dogfoodable on day one.

**The host decides that, not a setting.** `local_mode` reads the request, so the
shortcut is unavailable off the laptop by construction. This replaced a rule
that keyed on `COLETAR_PUBLIC_URL`, which had a gap worth naming: a deployment
shipped with *neither* that variable nor an identity provider served an open
workspace behind a homepage with no sign-in link on it -- a missing lock that
looked like a missing link, and a state a platform's per-environment variable
scoping makes easy to reach by accident. A deployed host now always gets a
sign-in, and if nothing can answer it the API refuses loudly instead of falling
open.

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


#: Hosts on which the no-sign-in shortcut is allowed at all: the loopback
#: interface, and the `.localhost` names browsers resolve to it.
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _hostname(raw: str) -> str:
    """A bare hostname from a Host header, with any port and brackets removed."""
    raw = raw.strip()
    if raw.startswith("["):  # [::1]:8000
        return raw[1 : raw.index("]")].lower() if "]" in raw else raw.lower()
    return (raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw).lower()


def request_host(request: Request) -> str:
    """The host the browser actually asked for, through any proxy in front of us.

    `x-forwarded-host` first because that is what a platform sets to the public
    domain; `request.url.hostname` behind a proxy is an internal address and
    would read as "not localhost" for the wrong reason, or as localhost when the
    proxy is on the same box -- which is the reading that would matter.
    """
    forwarded = request.headers.get("x-forwarded-host", "").split(",")[0]
    raw = forwarded.strip() or request.headers.get("host", "") or (request.url.hostname or "")
    return _hostname(raw)


def is_loopback(request: Request) -> bool:
    host = request_host(request)
    return host in _LOOPBACK or host.endswith(".localhost")


def local_mode(request: Request) -> bool:
    """True only on loopback, and only when no provider was asked for.

    **The host decides first, and no environment variable can overrule it.** The
    previous version keyed on settings alone, so a deployment that shipped
    without `COLETAR_IDENTITY_PROVIDER` served an open workspace and a homepage
    with no sign-in on it -- the failure looked like a missing link rather than a
    missing lock. Reading the request instead means the shortcut is unavailable
    off the laptop by construction, which is the property `LocalIdentityProvider`
    already assumed it had: it trusts whoever can reach the port, and that is
    only a safe assumption when the port is loopback.

    Settings still matter in the other direction: running locally *with* a real
    provider configured is a sign-in, because that is the point of testing it.
    """
    if not is_loopback(request):
        return False
    settings = get_settings()
    return settings.identity_provider in {"", LOCAL_IDENTITY} and not settings.public_url


async def current_account(request: Request) -> Account | None:
    """The signed-in account, or None in local mode.

    None is *only* returned in local mode. In a hosted deployment this either
    returns an account or raises -- there is no anonymous path left.
    """
    if local_mode(request):
        return None
    settings = get_settings()
    if settings.identity_provider in {"", LOCAL_IDENTITY}:
        # Remote host, no real provider. `LocalIdentityProvider.verify` returns
        # an identity for *any* credential including none, so reaching
        # `resolve_account` here would hand the workspace to whoever asked.
        # Refusing is the only safe answer, and a loud one: a deployment missing
        # this setting should be obviously broken rather than quietly open.
        raise HTTPException(
            503,
            "This deployment has no identity provider configured. Set "
            "COLETAR_IDENTITY_PROVIDER before serving a workspace off localhost.",
        )
    # Built outside the try, because constructing a provider and checking a
    # credential fail for completely different reasons and used to return the
    # same status. A deployment missing COLETAR_SUPABASE_URL raised
    # `IdentityError` here and answered 401, which the client reads as "your
    # session expired" -- so it sent the user to sign in, the sign-in produced
    # another 401, and the loop had no exit. A deployment that cannot
    # authenticate anyone is broken, not unauthenticated, and 503 is the
    # difference between "this is on us" and "try again".
    try:
        identity = build_identity()
    except IdentityError as exc:
        raise HTTPException(
            503, f"This deployment cannot authenticate anyone: {exc}"
        ) from exc

    try:
        account = await resolve_account(
            presented_credential(request),
            provider=identity,
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
