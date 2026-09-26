"""Supabase Auth as the identity provider. The whole of it, per `identity.py`'s contract.

This is the second provider through the seam `identity.py` describes, and it is the
one the docstring there always named as next. It turns a Supabase access token into
an `ExternalIdentity` and does nothing else: no session storage, no user table of
its own, no credential of Supabase's kept anywhere in coletar.

**Why this provider reads no secret.** Supabase issues access tokens signed with an
asymmetric key (ES256 by default on projects with JWT signing keys enabled) and
publishes the public half at `{url}/auth/v1/.well-known/jwks.json`. Verification is
therefore a signature check against a public document, exactly as it is for Clerk.
That is not a detail: the *legacy* Supabase arrangement is HS256 against a shared
`JWT_SECRET`, and a shared secret that verifies a token can also mint one. Holding
it would make this server able to forge its own users' sessions, and would put a
credential worth stealing into an environment variable. This module refuses HS256
outright rather than supporting both, which means a project still on legacy keys
must migrate to asymmetric keys before it can be used here — one switch in the
Supabase dashboard under Authentication → JWT Keys.

Four checks here are security properties rather than ceremony:

**The algorithm is pinned to the asymmetric set.** PyJWT will otherwise honour the
`alg` in a header the attacker wrote. The classic attack is `alg: none`; the subtler
one is `alg: HS256` verified against a public key the attacker simply fetched,
turning the public half of the keypair into a signing secret.

**`iss` is pinned to this project.** For Clerk the equivalent binding needed a
separate `azp` allowlist, because many sites share one Clerk instance. Here the
issuer *is* the project — a token minted by any other Supabase project carries a
different `iss` and is signed by a JWKS we never fetch — so pinning the issuer is
the whole of that property.

**`aud` must be `authenticated`.** Supabase mints tokens for other audiences, and a
service-role token is not a person signing in.

**Anonymous sign-ins are refused.** Supabase can issue a real, correctly signed
token for a user who proved nothing at all (`is_anonymous: true`). Such a token must
never reach `resolve_account`, because provisioning treats what it is handed as a
person and would mint a workspace for a stranger who clicked once.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient

from coletar.accounts.identity import IdentityError, IdentityProvider
from coletar.accounts.models import ExternalIdentity

#: The name recorded on every account this provider vouches for. Persisted in
#: `account.identity_provider` and matched on, so it is effectively schema: changing
#: the string orphans every account already linked under the old one.
SUPABASE_IDENTITY = "supabase"

#: Supabase signs user access tokens with ES256 by default and RS256 when a project
#: is configured for it. Both are asymmetric; neither can be verified with a secret
#: this server holds. HS256 is deliberately absent -- see the module docstring.
ALGORITHMS = ["ES256", "RS256"]

#: The only audience a person's access token carries. A service-role key is minted
#: for `service_role` and must not resolve to an account.
USER_AUDIENCE = "authenticated"

#: Seconds of tolerated clock drift on `exp`/`nbf`. Small on purpose: a little
#: leeway for real drift, not so much that an expired session keeps working.
LEEWAY_SECONDS = 30

#: How long a fetched signing key stays cached. Supabase rotates keys, and a client
#: that caches forever keeps trusting a retired one.
JWKS_CACHE_SECONDS = 600


class SupabaseIdentityProvider(IdentityProvider):
    """Verify a Supabase access token and report who it belongs to."""

    def __init__(self, *, url: str, jwks_url: str = "") -> None:
        if not url:
            raise IdentityError(
                "Supabase Auth needs its project URL (COLETAR_SUPABASE_URL), e.g. "
                "https://your-ref.supabase.co. It is the root of both the issuer "
                "every access token carries and the JWKS this server verifies "
                "against."
            )
        base = url.rstrip("/")
        # Derived rather than configured separately, so the issuer that is *checked*
        # and the JWKS that is *trusted* cannot be pointed at two different projects
        # by a partial edit to the environment.
        self._issuer = f"{base}/auth/v1"
        self._jwks = PyJWKClient(
            jwks_url or f"{self._issuer}/.well-known/jwks.json",
            cache_keys=True,
            lifespan=JWKS_CACHE_SECONDS,
        )

    @property
    def name(self) -> str:
        return SUPABASE_IDENTITY

    async def verify(self, credential: str | None) -> ExternalIdentity | None:
        if not credential:
            # Nobody is signed in. An ordinary state, and explicitly not an error --
            # see the protocol docstring for why the two must stay distinguishable.
            return None
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(credential)
            claims: dict[str, Any] = jwt.decode(
                credential,
                signing_key.key,
                # Pinned to the asymmetric set. Never read from the token's header.
                algorithms=ALGORITHMS,
                issuer=self._issuer,
                audience=USER_AUDIENCE,
                leeway=LEEWAY_SECONDS,
                options={
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "verify_signature": True,
                },
            )
        except jwt.PyJWTError as exc:
            # Deliberately not echoing the token or the library's message to the
            # caller: one ends up in a log, the other distinguishes "expired" from
            # "forged" for whoever is probing. The reason stays server-side.
            raise IdentityError("This sign-in could not be verified.") from exc

        if claims.get("is_anonymous") is True:
            # Correctly signed, and vouches for nobody. See the module docstring.
            raise IdentityError(
                "This is an anonymous session. Sign in with an email address to open "
                "a workspace."
            )

        subject = str(claims.get("sub") or "")
        if not subject:
            raise IdentityError("This sign-in carries no subject.")

        metadata = claims.get("user_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}

        return ExternalIdentity(
            provider=SUPABASE_IDENTITY,
            subject=subject,
            email=str(claims.get("email") or metadata.get("email") or ""),
            display_name=str(metadata.get("full_name") or metadata.get("name") or ""),
            # Whether Supabase says it *checked* the address, not merely that it
            # holds one. GoTrue puts this in `user_metadata` and mirrors it at the
            # top level on newer versions; both are read because claiming an
            # existing graph by address turns on it. See `accounts.session`.
            email_verified=bool(
                claims.get("email_verified", metadata.get("email_verified", False))
            ),
        )


def build_supabase_provider() -> SupabaseIdentityProvider:
    """Construct from settings. Separate so `build_identity_provider` stays a table."""
    from coletar.config import get_settings

    settings = get_settings()
    return SupabaseIdentityProvider(
        url=settings.supabase_url,
        jwks_url=settings.supabase_jwks_url,
    )
