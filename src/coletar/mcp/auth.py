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

The `Authenticator` protocol is the seam OAuth arrives through in M3. The store
itself is still **single-tenant**: a valid key today reaches the whole graph. Scopes
are enforced, tenancy is not. Do not deploy this for more than one user until M3.1
lands a tenant column and the query-level filtering that goes with it.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: Read is search and inspection; write is anything that mutates the graph. The
#: split exists because M7.1 requires a ChatGPT connector whose write attempts are
#: rejected *server-side* rather than merely hidden in the client -- and because a
#: read-only key for a dashboard is useful on its own.
SCOPE_READ = "read"
SCOPE_WRITE = "write"
DEFAULT_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE})

#: Liveness probes cannot carry a credential. Exactly one path, and a test pins the
#: size of this set so the exemption list cannot quietly grow.
EXEMPT_PATHS = frozenset({"/healthz"})


@dataclass(frozen=True)
class Principal:
    """Who is calling. `id` is what lands in the event log's actor detail."""

    id: str
    scopes: frozenset[str] = DEFAULT_SCOPES

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
    def authenticate(self, credential: str | None) -> Principal | None:
        """Return the principal, or None to reject. Must not raise on bad input."""
        ...


class ApiKeyAuthenticator:
    """Bearer API keys from configuration.

    Key format is `id:secret` or `id:secret:read|write`, comma-separated:

        COLETAR_MCP_API_KEYS="alice:sk-live-...,dashboard:sk-ro-...:read"
    """

    def __init__(self, keys: Iterable[tuple[str, str, frozenset[str]]]) -> None:
        self._by_secret: dict[str, Principal] = {
            secret: Principal(id=principal_id, scopes=scopes)
            for principal_id, secret, scopes in keys
        }

    def __len__(self) -> int:
        return len(self._by_secret)

    @classmethod
    def from_config(cls, raw: str) -> ApiKeyAuthenticator:
        keys: list[tuple[str, str, frozenset[str]]] = []
        for entry in (part.strip() for part in raw.split(",")):
            if not entry:
                continue
            fields = entry.split(":")
            if len(fields) < 2 or not fields[0] or not fields[1]:
                raise AuthError(
                    f"malformed COLETAR_MCP_API_KEYS entry {entry!r}; "
                    f"expected 'id:secret' or 'id:secret:read|write'"
                )
            principal_id, secret = fields[0], fields[1]
            if len(fields) > 2 and fields[2]:
                scopes = frozenset(s for s in fields[2].split("|") if s)
                unknown = scopes - DEFAULT_SCOPES
                if unknown:
                    raise AuthError(f"unknown scope(s) {sorted(unknown)} in {entry!r}")
            else:
                scopes = DEFAULT_SCOPES
            keys.append((principal_id, secret, scopes))
        return cls(keys)

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


class AuthMiddleware:
    """Pure ASGI middleware. Wraps the MCP app so every HTTP request is gated."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        authenticator: Authenticator,
        *,
        exempt_paths: frozenset[str] = EXEMPT_PATHS,
    ) -> None:
        self.app = app
        self.authenticator = authenticator
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        # Lifespan and websocket scopes are not credentialed HTTP requests; passing
        # them through is what lets the app start at all.
        if scope.get("type") != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        principal = self.authenticator.authenticate(bearer_token(scope.get("headers", [])))
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
                    ],
                }
            )
            await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
            return

        with principal_scope(principal):
            await self.app(scope, receive, send)
