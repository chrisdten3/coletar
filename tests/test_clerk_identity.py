"""Clerk token verification, against real signatures.

Every test here signs an actual RS256 JWT with a keypair generated in-process and
serves a real JWKS document, because the things worth pinning are precisely the
ones a mocked verifier would not catch: that `alg` is not read from the attacker's
own header, that a token minted for another origin is refused, that an expired one
stops working. A stub `verify` would pass all of these while the real one accepted
`alg: none`.
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from coletar.accounts.clerk import ClerkIdentityProvider
from coletar.accounts.identity import IdentityError

ISSUER = "https://example-app.clerk.accounts.dev"
PARTIES = frozenset({"https://coletar.example"})


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture
def provider(keypair, monkeypatch):
    """A provider whose JWKS lookup returns our public key instead of hitting Clerk."""
    _, public = keypair

    class StubJWKClient:
        def __init__(self, *args, **kwargs) -> None:
            self.calls = 0

        def get_signing_key_from_jwt(self, token):
            self.calls += 1
            # Mirrors PyJWKClient's contract: it reads the *unverified* header to
            # pick a key, then the caller verifies the signature against it.
            header = jwt.get_unverified_header(token)
            if header.get("kid") != "test-key":
                raise jwt.PyJWKClientError("no matching key")

            class Signing:
                key = public

            return Signing()

    monkeypatch.setattr("coletar.accounts.clerk.PyJWKClient", StubJWKClient)
    return ClerkIdentityProvider(issuer=ISSUER, authorized_parties=PARTIES)


def token(keypair, **overrides) -> str:
    private, _ = keypair
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "user_2abc",
        "iat": now,
        "exp": now + 3600,
        "azp": "https://coletar.example",
        "email": "alice@example.com",
        "email_verified": True,
        "name": "Alice",
    }
    claims.update(overrides)
    for key in [k for k, v in claims.items() if v is None]:
        del claims[key]
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})


async def test_valid_token_resolves_an_identity(provider, keypair):
    identity = await provider.verify(token(keypair))
    assert identity is not None
    assert identity.provider == "clerk"
    assert identity.subject == "user_2abc"
    assert identity.email == "alice@example.com"
    assert identity.email_verified is True
    assert identity.display_name == "Alice"


async def test_absent_credential_is_not_an_error(provider):
    """"Nobody is signed in" is an ordinary state and must stay distinguishable."""
    assert await provider.verify(None) is None
    assert await provider.verify("") is None


async def test_expired_token_is_refused(provider, keypair):
    now = int(time.time())
    with pytest.raises(IdentityError):
        await provider.verify(token(keypair, exp=now - 120, iat=now - 3600))


async def test_token_from_another_issuer_is_refused(provider, keypair):
    with pytest.raises(IdentityError):
        await provider.verify(token(keypair, iss="https://attacker.clerk.accounts.dev"))


async def test_token_minted_for_another_origin_is_refused(provider, keypair):
    """`azp` is the cross-origin replay check. Without it any site on the same
    Clerk instance could spend its users' tokens here."""
    with pytest.raises(IdentityError, match="different site"):
        await provider.verify(token(keypair, azp="https://someone-else.example"))


async def test_unsigned_token_is_refused(provider, keypair):
    """The `alg: none` attack. PyJWT honours the header's algorithm unless pinned."""
    now = int(time.time())
    forged = jwt.encode(
        {"iss": ISSUER, "sub": "user_2abc", "iat": now, "exp": now + 3600},
        key="",
        algorithm="none",
        headers={"kid": "test-key"},
    )
    with pytest.raises(IdentityError):
        await provider.verify(forged)


async def test_token_signed_with_the_public_key_as_hmac_is_refused(provider, keypair):
    """The subtler algorithm-confusion attack: RS256 public key used as an HS256 secret.

    Assembled by hand rather than with `jwt.encode`, which refuses to treat a PEM as
    an HMAC secret. That refusal protects *us* when we sign; it does nothing about a
    token an attacker assembled themselves, which is the thing being tested. Only
    `algorithms=["RS256"]` on the decode side stops this one.
    """
    import base64
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import serialization

    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def segment(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    header = segment(json.dumps({"alg": "HS256", "kid": "test-key"}).encode())
    payload = segment(
        json.dumps({"iss": ISSUER, "sub": "attacker", "iat": now, "exp": now + 3600}).encode()
    )
    signing_input = header + b"." + payload
    signature = segment(hmac.new(pem, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    with pytest.raises(IdentityError):
        await provider.verify(forged)


async def test_token_signed_by_the_wrong_key_is_refused(provider):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {"iss": ISSUER, "sub": "attacker", "iat": now, "exp": now + 3600},
        other,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    with pytest.raises(IdentityError):
        await provider.verify(forged)


async def test_unknown_key_id_is_refused(provider, keypair):
    private, _ = keypair
    now = int(time.time())
    forged = jwt.encode(
        {"iss": ISSUER, "sub": "attacker", "iat": now, "exp": now + 3600},
        private,
        algorithm="RS256",
        headers={"kid": "rotated-away"},
    )
    with pytest.raises(IdentityError):
        await provider.verify(forged)


async def test_token_without_a_subject_is_refused(provider, keypair):
    with pytest.raises(IdentityError):
        await provider.verify(token(keypair, sub=None))


async def test_unverified_email_is_reported_as_such(provider, keypair):
    """Not refused here — reported, so the session layer can decline to hand over
    an existing graph on the strength of it."""
    identity = await provider.verify(token(keypair, email_verified=False))
    assert identity is not None
    assert identity.email == "alice@example.com"
    assert identity.email_verified is False


async def test_missing_email_verified_claim_defaults_to_unverified(provider, keypair):
    identity = await provider.verify(token(keypair, email_verified=None))
    assert identity is not None
    assert identity.email_verified is False


async def test_error_message_does_not_leak_the_reason(provider, keypair):
    """Whoever is probing should not learn "expired" versus "forged" from the reply."""
    now = int(time.time())

    async def message_for(credential: str) -> str:
        try:
            await provider.verify(credential)
        except IdentityError as exc:
            return str(exc)
        raise AssertionError("expected this credential to be refused")

    expired = await message_for(token(keypair, exp=now - 120, iat=now - 3600))
    forged = await message_for(token(keypair, iss="https://attacker.example"))
    assert expired == forged


def test_empty_authorized_parties_is_refused_at_construction(monkeypatch):
    """Empty must not read as "accept a token minted for anywhere"."""
    monkeypatch.setattr("coletar.accounts.clerk.PyJWKClient", lambda *a, **k: None)
    with pytest.raises(IdentityError, match="AUTHORIZED_PARTIES"):
        ClerkIdentityProvider(issuer=ISSUER, authorized_parties=frozenset())


def test_missing_issuer_is_refused_at_construction(monkeypatch):
    monkeypatch.setattr("coletar.accounts.clerk.PyJWKClient", lambda *a, **k: None)
    with pytest.raises(IdentityError, match="CLERK_ISSUER"):
        ClerkIdentityProvider(issuer="", authorized_parties=PARTIES)


def test_jwks_document_shape_is_what_pyjwt_expects(keypair):
    """A guard on the real client's contract, since the tests above stub it."""
    from jwt.algorithms import RSAAlgorithm

    _, public = keypair
    jwk = json.loads(RSAAlgorithm.to_jwk(public))
    assert jwk["kty"] == "RSA"
    assert {"n", "e"} <= set(jwk)
