"""Supabase Auth token verification, against real signatures.

Mirrors `test_clerk_identity.py` and for the same reason: every test here signs an
actual ES256 JWT with a keypair generated in-process, because the properties worth
pinning are precisely the ones a mocked verifier would not catch. A stub `verify`
would pass all of these while the real one accepted `alg: none`.

Two tests have no Clerk counterpart and are the reason this file is not a copy:

  * **HS256 is refused.** Supabase's *legacy* arrangement signs with a shared
    `JWT_SECRET`, and a secret that verifies a token can also mint one. Accepting
    HS256 here would also reopen the classic confusion attack, where the public key
    this server fetches is used as an HMAC secret by whoever fetched it too.

  * **Anonymous sessions are refused.** Supabase will issue a correctly signed
    token for a user who proved nothing at all. Such a token must never reach
    `resolve_account`, which would read it as a person and mint them a workspace.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from coletar.accounts.identity import IdentityError
from coletar.accounts.supabase import SupabaseIdentityProvider

URL = "https://example-ref.supabase.co"
ISSUER = f"{URL}/auth/v1"


@pytest.fixture(scope="module")
def keypair():
    """ES256, which is what Supabase's asymmetric JWT signing keys actually use."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key, key.public_key()


@pytest.fixture
def provider(keypair, monkeypatch):
    """A provider whose JWKS lookup returns our public key instead of hitting Supabase."""
    _, public = keypair

    class StubJWKClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_signing_key_from_jwt(self, token):
            # Mirrors PyJWKClient's contract: it reads the *unverified* header to
            # pick a key, then the caller verifies the signature against it.
            header = jwt.get_unverified_header(token)
            if header.get("kid") != "test-key":
                raise jwt.PyJWKClientError("no matching key")

            class Signing:
                key = public

            return Signing()

    monkeypatch.setattr("coletar.accounts.supabase.PyJWKClient", StubJWKClient)
    return SupabaseIdentityProvider(url=URL)


def token(keypair, *, algorithm: str = "ES256", key=None, **overrides) -> str:
    private, _ = keypair
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "8f14e45f-ceea-467a-9c2b-2e1b3f6a1d90",
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
        "email": "alice@example.com",
        "user_metadata": {"email_verified": True, "full_name": "Alice Nakamura"},
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "is_anonymous": False,
    }
    claims.update(overrides)
    for name in [k for k, v in claims.items() if v is None]:
        del claims[name]
    return jwt.encode(
        claims,
        key if key is not None else private,
        algorithm=algorithm,
        headers={"kid": "test-key"},
    )


async def test_valid_token_resolves_an_identity(provider, keypair):
    identity = await provider.verify(token(keypair))
    assert identity is not None
    assert identity.provider == "supabase"
    assert identity.subject == "8f14e45f-ceea-467a-9c2b-2e1b3f6a1d90"
    assert identity.email == "alice@example.com"
    assert identity.email_verified is True
    assert identity.display_name == "Alice Nakamura"


async def test_absent_credential_is_not_an_error(provider):
    """"Nobody is signed in" is an ordinary state and must stay distinguishable."""
    assert await provider.verify(None) is None
    assert await provider.verify("") is None


async def test_expired_token_is_refused(provider, keypair):
    now = int(time.time())
    with pytest.raises(IdentityError):
        await provider.verify(token(keypair, exp=now - 120, iat=now - 3600))


async def test_token_from_another_project_is_refused(provider, keypair):
    """For Supabase the issuer *is* the project, so pinning `iss` is the whole of
    the cross-project replay check that Clerk needs a separate `azp` allowlist for."""
    with pytest.raises(IdentityError):
        await provider.verify(token(keypair, iss="https://attacker.supabase.co/auth/v1"))


async def test_service_role_token_is_refused(provider, keypair):
    """A service-role key is correctly signed and is not a person signing in."""
    with pytest.raises(IdentityError):
        await provider.verify(token(keypair, aud="service_role", role="service_role"))


async def test_anonymous_session_is_refused(provider, keypair):
    """Correctly signed, and vouches for nobody. Provisioning must never see it."""
    with pytest.raises(IdentityError, match="anonymous"):
        await provider.verify(token(keypair, is_anonymous=True))


async def test_hs256_token_is_refused(provider, keypair):
    """Legacy Supabase signs with a shared secret. A secret that verifies a token
    can mint one, so this provider does not accept that algorithm at all."""
    with pytest.raises(IdentityError):
        await provider.verify(
            token(keypair, algorithm="HS256", key="a-shared-jwt-secret-value-of-legacy-length")
        )


async def test_unsigned_token_is_refused(provider, keypair):
    """`alg: none`. The reason algorithms are pinned rather than read from the header."""
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "nobody",
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
        },
        key="",
        algorithm="none",
        headers={"kid": "test-key"},
    )
    with pytest.raises(IdentityError):
        await provider.verify(unsigned)


async def test_token_without_a_subject_is_refused(provider, keypair):
    with pytest.raises(IdentityError):
        await provider.verify(token(keypair, sub=None))


async def test_unverified_email_is_reported_as_such(provider, keypair):
    """The claim `accounts.session` refuses to hand over an existing graph without."""
    identity = await provider.verify(
        token(keypair, user_metadata={"email_verified": False, "full_name": "Alice"})
    )
    assert identity is not None
    assert identity.email_verified is False


async def test_top_level_email_verified_is_read(provider, keypair):
    """Newer GoTrue mirrors the claim at the top level; both places are read."""
    identity = await provider.verify(
        token(keypair, email_verified=True, user_metadata={"full_name": "Alice"})
    )
    assert identity is not None
    assert identity.email_verified is True


async def test_missing_url_is_refused_at_construction():
    """A provider that silently verifies nothing is worse than one that will not start."""
    with pytest.raises(IdentityError, match="COLETAR_SUPABASE_URL"):
        SupabaseIdentityProvider(url="")


async def test_issuer_and_jwks_are_derived_from_one_setting(monkeypatch):
    """Pinned because deriving both from `url` is what stops a partial environment
    edit from checking one project's issuer against another project's keys."""
    seen: dict[str, str] = {}

    class StubJWKClient:
        def __init__(self, url, *args, **kwargs) -> None:
            seen["jwks"] = url

    monkeypatch.setattr("coletar.accounts.supabase.PyJWKClient", StubJWKClient)
    provider = SupabaseIdentityProvider(url="https://ref.supabase.co/")
    assert provider._issuer == "https://ref.supabase.co/auth/v1"
    assert seen["jwks"] == "https://ref.supabase.co/auth/v1/.well-known/jwks.json"
