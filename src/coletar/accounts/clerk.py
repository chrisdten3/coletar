"""Clerk as the identity provider. The whole of it, per `identity.py`'s contract.

This file is what the seam was for. It turns a Clerk session JWT into an
`ExternalIdentity` and does nothing else: no session storage, no user table, no
credential of Clerk's kept anywhere in coletar. If Clerk is replaced tomorrow, this
file is what is deleted.

Three checks here are security properties rather than ceremony, and each one is a
known way JWT verification goes wrong:

**The algorithm is pinned to RS256.** PyJWT will otherwise honour the `alg` in a
header the attacker wrote. The classic attack is `alg: none`; the subtler one is
`alg: HS256` against a public key the attacker can fetch, turning the *public* half
of the keypair into a signing secret.

**`azp` is checked against an allowlist.** Clerk's session token names the origin it
was minted for. Without this check a token issued to any other site running on the
same Clerk instance is accepted here, which is the cross-origin token-replay hole
Clerk's own docs call out.

**Clock skew is bounded, not disabled.** A little leeway for real clock drift; not
so much that an expired session keeps working.

The JWKS is fetched and cached by `PyJWKClient`, keyed on `kid`, with its own
lifespan so a rotated Clerk key is picked up without a redeploy.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient

from coletar.accounts.identity import IdentityError, IdentityProvider
from coletar.accounts.models import ExternalIdentity

#: The name recorded on every account this provider vouches for. It is persisted in
#: `account.identity_provider` and matched on, so it is effectively schema: changing
#: the string orphans every account already linked under the old one.
CLERK_IDENTITY = "clerk"

#: Seconds of tolerated clock drift on `exp`/`nbf`. Small on purpose -- see above.
LEEWAY_SECONDS = 30

#: How long a fetched signing key stays cached. Clerk rotates keys, and a client that
#: caches forever keeps trusting a retired one.
JWKS_CACHE_SECONDS = 600


class ClerkIdentityProvider(IdentityProvider):
    """Verify a Clerk session JWT and report who it belongs to.

    `authorized_parties` is required rather than optional. An empty allowlist would
    mean "accept a token minted for any origin", and a provider that silently
    accepts everything is worse than one that refuses to start.
    """

    def __init__(
        self,
        *,
        issuer: str,
        authorized_parties: frozenset[str],
        jwks_url: str = "",
    ) -> None:
        if not issuer:
            raise IdentityError(
                "Clerk needs its issuer URL (COLETAR_CLERK_ISSUER), e.g. "
                "https://your-app.clerk.accounts.dev. It is the `iss` every session "
                "token carries and the root of the JWKS URL."
            )
        if not authorized_parties:
            raise IdentityError(
                "Clerk needs COLETAR_CLERK_AUTHORIZED_PARTIES -- the origins allowed "
                "to present a token here. Empty would mean accepting a token minted "
                "for any other site on your Clerk instance."
            )
        self._issuer = issuer.rstrip("/")
        self._authorized_parties = authorized_parties
        self._jwks = PyJWKClient(
            jwks_url or f"{self._issuer}/.well-known/jwks.json",
            cache_keys=True,
            lifespan=JWKS_CACHE_SECONDS,
        )

    @property
    def name(self) -> str:
        return CLERK_IDENTITY

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
                # Pinned. Never read from the token's own header.
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={
                    "require": ["exp", "iat", "sub", "iss"],
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_signature": True,
                    # Clerk session tokens carry no `aud`. Demanding one would refuse
                    # every valid token; `azp` below is the equivalent binding.
                    "verify_aud": False,
                },
            )
        except jwt.PyJWTError as exc:
            # Deliberately not echoing the token or the library's message to the
            # caller: one ends up in a log, the other distinguishes "expired" from
            # "forged" for whoever is probing. The reason stays server-side.
            raise IdentityError("This sign-in could not be verified.") from exc

        party = claims.get("azp")
        if party is not None and party not in self._authorized_parties:
            raise IdentityError("This sign-in was issued for a different site.")

        subject = str(claims.get("sub") or "")
        if not subject:
            raise IdentityError("This sign-in carries no subject.")

        return ExternalIdentity(
            provider=CLERK_IDENTITY,
            subject=subject,
            email=str(claims.get("email") or ""),
            display_name=str(claims.get("name") or claims.get("full_name") or ""),
            # Whether Clerk says it *checked* the address, not merely that it holds
            # one. `account_for_email_claim` refuses to hand over an existing graph
            # without this; see `coletar.accounts.session`.
            email_verified=bool(claims.get("email_verified", False)),
        )


def build_clerk_provider() -> ClerkIdentityProvider:
    """Construct from settings. Separate so `build_identity_provider` stays a table."""
    from coletar.config import get_settings

    settings = get_settings()
    parties = frozenset(
        part.strip() for part in settings.clerk_authorized_parties.split(",") if part.strip()
    )
    return ClerkIdentityProvider(
        issuer=settings.clerk_issuer,
        authorized_parties=parties,
        jwks_url=settings.clerk_jwks_url,
    )
