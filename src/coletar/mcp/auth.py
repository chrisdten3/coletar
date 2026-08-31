"""Auth for the hosted MCP server (SCOPE §3.1, §9).

Three decisions worth stating, because each of them is a security property rather
than a style preference:

**Gated at the ASGI layer, not per tool.** The build plan's requirement is an auth
layer gating *every* call. A check inside each tool body is a check a future tool can
forget to add; middleware in front of the whole app cannot be forgotten, and it
covers the MCP protocol handshake as well as the tool calls.

**Fail closed.** With no keys configured the server rejects everything and refuses to
start. "Unauthenticated requests are rejected" must never quietly degrade into
"everything is allowed" because someone did not set an environment variable.

**Authentication resolves a principal, and the principal is who the event log
records.** Until now every connector write was attributed to a generic `model`
actor. The observability dashboard (§6) has to answer "who wrote this", and M3.1
requires that one user's token cannot reach another user's objects -- both start
from knowing who is calling.

The `Authenticator` protocol is the seam OAuth arrives through in M3.3.

**Every principal belongs to exactly one tenant, and the MCP server resolves the
tenant from the principal alone.** It never consults configuration for a fallback: a
connector that falls back to a configured tenant is a connector serving someone
else's graph. `COLETAR_DEFAULT_TENANT_ID` exists for the CLI and the local proxy and
is deliberately unreachable from here.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from coletar.mcp.ratelimit import RateLimiter
from coletar.schema.objects import Provider
from coletar.schema.tenancy import InvalidTenantId, TenantId
from coletar.schema.tenancy import tenant_id as parse_tenant_id

#: Read is search and inspection; write is anything that mutates the graph. The
#: split exists because M7.1 requires a ChatGPT connector whose write attempts are
#: rejected *server-side* rather than merely hidden in the client -- and because a
#: read-only key for a dashboard is useful on its own.
SCOPE_READ = "read"
SCOPE_WRITE = "write"
DEFAULT_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE})

#: Liveness probes cannot carry a credential. A test pins this set so it cannot grow
#: quietly; growing it deliberately, with a reason, is what this comment is for.
EXEMPT_PATHS = frozenset({"/healthz"})

#: Discovery is public by definition — a client cannot authenticate before finding
#: out *how* to authenticate. We implement no OAuth, so these return 404, and that is
#: the point: 404 tells a client "this server does not do OAuth", while the 401 they
#: previously got says "your credentials are wrong for discovery too", which is false
#: and turns a plain auth failure into an unexplained connection error.
EXEMPT_PREFIXES = ("/.well-known/",)


@dataclass(frozen=True)
class Principal:
    """Who is calling, and whose graph they reach.

    `id` is what lands in the event log's actor detail; `tenant_id` is the only thing
    that decides which objects exist as far as this caller is concerned. `surface` is
    the third thing: it is the *locality* gate (see the `Store` protocol docstring),
    fixed at key-issuance time rather than inferred from the request, because a
    connector that could claim its own surface is not gated at all. It defaults to
    `Provider.COLETAR` -- a generic, unspecified surface -- for keys configured before
    this field existed or that never named one; such a principal reads every `synced`
    object as before and no `local_only` object at all, which is the safe default
    rather than a guess.
    """

    id: str
    tenant_id: TenantId
    scopes: frozenset[str] = DEFAULT_SCOPES
    surface: Provider = Provider.COLETAR

    def can(self, scope: str) -> bool:
        return scope in self.scopes


#: Set by the middleware for the duration of one request. Tool bodies read it rather
#: than threading a principal argument through every signature.
_CURRENT_PRINCIPAL: ContextVar[Principal | None] = ContextVar(
    "coletar_principal", default=None
)


def current_principal() -> Principal | None:
    return _CURRENT_PRINCIPAL.get()


@contextmanager
def principal_scope(principal: Principal) -> Iterator[Principal]:
    """Bind the calling principal for the duration of a block.

    The middleware is the only production caller. It is public so tests and future
    embedding hosts establish identity through the same path the server does,
    rather than reaching into a module private and drifting from it.
    """
    token = _CURRENT_PRINCIPAL.set(principal)
    try:
        yield principal
    finally:
        _CURRENT_PRINCIPAL.reset(token)


class AuthError(RuntimeError):
    """Raised at startup, not per request -- a misconfigured server should fail to
    boot rather than fail closed silently on every call."""


@runtime_checkable
class Authenticator(Protocol):
    @property
    def tenants(self) -> set[TenantId]:
        """Every tenant this authenticator can issue a principal for.

        Reported at startup so a misconfigured deployment is visible in the first log
        line rather than at the first request.
        """
        ...

    def authenticate(self, credential: str | None) -> Principal | None:
        """Return the principal, or None to reject. Must not raise on bad input."""
        ...


class ApiKeyAuthenticator:
    """Bearer API keys from configuration, as JSON.

        COLETAR_MCP_API_KEYS='[
          {"id": "alice-claude", "secret": "sk-live-...", "tenant_id": "tenant_alice",
           "surface": "claude"},
          {"id": "dashboard", "secret": "sk-ro-...", "tenant_id": "tenant_alice",
           "scopes": ["read"]}
        ]'

    `surface` names which connector this key was issued for (M3.3: one key per
    registration), and is optional -- an entry that omits it gets `Provider.COLETAR`,
    the generic default `Principal.surface` already documents.

    JSON rather than the colon-delimited form this started as. Adding the tenant would
    have made it a four-field positional string, and a positional string whose third
    field silently decides whose data you reach is a configuration format that will
    eventually be got wrong.
    """

    def __init__(self, principals: Iterable[tuple[str, Principal]]) -> None:
        self._by_secret: dict[str, Principal] = dict(principals)

    def __len__(self) -> int:
        return len(self._by_secret)

    @property
    def tenants(self) -> set[TenantId]:
        return {p.tenant_id for p in self._by_secret.values()}

    @classmethod
    def from_config(cls, raw: str) -> ApiKeyAuthenticator:
        text = (raw or "").strip()
        if not text:
            return cls([])
        try:
            entries = json.loads(text)
        except ValueError as exc:
            raise AuthError(
                f"COLETAR_MCP_API_KEYS must be a JSON array of "
                f"{{id, secret, tenant_id, scopes?, surface?}} objects: {exc}"
            ) from exc
        if not isinstance(entries, list):
            raise AuthError("COLETAR_MCP_API_KEYS must be a JSON *array*.")

        principals: list[tuple[str, Principal]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise AuthError(f"each key entry must be an object; got {entry!r}")
            missing = {"id", "secret", "tenant_id"} - set(entry)
            if missing:
                raise AuthError(f"key entry {entry.get('id', '?')!r} is missing {sorted(missing)}")
            try:
                tenant = parse_tenant_id(str(entry["tenant_id"]))
            except InvalidTenantId as exc:
                raise AuthError(str(exc)) from exc
            scopes = frozenset(entry.get("scopes") or DEFAULT_SCOPES)
            unknown = scopes - DEFAULT_SCOPES
            if unknown:
                raise AuthError(f"unknown scope(s) {sorted(unknown)} on {entry['id']!r}")
            surface_raw = entry.get("surface")
            if surface_raw is None:
                surface = Provider.COLETAR
            else:
                try:
                    surface = Provider(str(surface_raw))
                except ValueError:
                    legal = ", ".join(sorted(p.value for p in Provider))
                    raise AuthError(
                        f"key entry {entry['id']!r} has surface {surface_raw!r}; "
                        f"must be one of: {legal}"
                    ) from None
            principals.append(
                (str(entry["secret"]), Principal(id=str(entry["id"]), tenant_id=tenant,
                                                 scopes=scopes, surface=surface))
            )
        return cls(principals)

    def authenticate(self, credential: str | None) -> Principal | None:
        if not credential:
            return None
        # Constant-time comparison against every configured secret. A dict lookup
        # would be faster and would leak key length and prefix through timing.
        for secret, principal in self._by_secret.items():
            if secrets.compare_digest(credential, secret):
                return principal
        return None


def bearer_token(headers: Iterable[tuple[bytes, bytes]]) -> str | None:
    """Pull the credential out of raw ASGI headers.

    Accepts `Authorization: Bearer <token>` and, because several MCP clients send
    it, a bare `X-API-Key: <token>`.
    """
    for name, value in headers:
        lowered = name.lower()
        if lowered == b"authorization":
            decoded = value.decode("latin-1").strip()
            scheme, _, token = decoded.partition(" ")
            if scheme.lower() == "bearer" and token:
                return token.strip()
        elif lowered == b"x-api-key" and value:
            return value.decode("latin-1").strip()
    return None


_UNAUTHORIZED_BODY = (
    b'{"error":"unauthorized",'
    b'"message":"This MCP server requires a bearer token. '
    b'Send Authorization: Bearer <your key>."}'
)


def _origin(headers: Iterable[tuple[bytes, bytes]]) -> str | None:
    for name, value in headers:
        if name.lower() == b"origin":
            return value.decode("latin-1")
    return None


_TOO_MANY_BODY = (
    b'{"error": "rate limited", "detail": "too many requests for this key; '
    b'see Retry-After"}'
)


class AuthMiddleware:
    """Pure ASGI middleware. Wraps the app so every HTTP request is gated."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        authenticator: Authenticator,
        *,
        exempt_paths: frozenset[str] = EXEMPT_PATHS,
        allowed_origins: frozenset[str] = frozenset(),
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self.authenticator = authenticator
        self.exempt_paths = exempt_paths
        # Applied here rather than per-route so MCP and REST are limited by the
        # same bucket. A limit one surface can walk around is not a limit.
        self.rate_limiter = rate_limiter or RateLimiter()
        # An allowlist, never a wildcard. These endpoints are authenticated, and a
        # wildcard would let any page the user visits attempt to spend their token.
        self.allowed_origins = allowed_origins

    def _cors_headers(self, origin: str | None) -> list[tuple[bytes, bytes]]:
        if origin is None or origin not in self.allowed_origins:
            return []
        return [
            (b"access-control-allow-origin", origin.encode()),
            (b"access-control-allow-headers", b"authorization, x-api-key, content-type"),
            (b"access-control-allow-methods", b"POST, OPTIONS"),
            (b"access-control-max-age", b"600"),
            # The origin decides the response, so caches must not share it.
            (b"vary", b"Origin"),
        ]

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        # Lifespan and websocket scopes are not credentialed HTTP requests; passing
        # them through is what lets the app start at all.
        path = str(scope.get("path", ""))
        if (
            scope.get("type") != "http"
            or path in self.exempt_paths
            or path.startswith(EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        origin = _origin(headers)
        cors = self._cors_headers(origin)

        # A CORS preflight carries no credentials by definition — the browser strips
        # them — so gating it on auth would make every cross-origin call fail before
        # the real request was ever sent. It reveals nothing: the response is a fixed
        # statement about which methods and headers are permitted.
        if scope.get("method") == "OPTIONS" and origin is not None:
            status = 204 if cors else 403
            await send({"type": "http.response.start", "status": status,
                        "headers": [*cors, (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return

        principal = self.authenticator.authenticate(bearer_token(headers))
        if principal is None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        # RFC 6750: tell the client how to authenticate rather than
                        # leaving it to guess.
                        (b"www-authenticate", b'Bearer realm="coletar"'),
                        (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode()),
                        # Without these the browser hides the 401 from the caller and
                        # it presents as an unexplained network failure.
                        *cors,
                    ],
                }
            )
            await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
            return

        retry_after = self.rate_limiter.check(principal.id)
        if retry_after is not None:
            body = _TOO_MANY_BODY
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        # Truthful, so a client told "no" waits instead of retrying
                        # immediately and making the problem worse.
                        (b"retry-after", str(max(1, int(retry_after + 0.5))).encode()),
                        (b"content-length", str(len(body)).encode()),
                        *cors,
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_cors(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and cors:
                message = {**message, "headers": [*message.get("headers", []), *cors]}
            await send(message)

        with principal_scope(principal):
            await self.app(scope, receive, send_with_cors)
