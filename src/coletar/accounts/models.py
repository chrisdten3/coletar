"""Who owns a graph, and what may reach it on their behalf.

**coletar stores no credential.** An account records *which* identity provider
vouches for a person and *what* that provider calls them — never a password, never
a session secret. That is not a stance about hashing; it is what makes the provider
swappable. Moving to Clerk or Supabase Auth is then a matter of verifying a
different token and reading a different subject claim, with no migration of secrets
and nothing in this repository that has to be re-secured.

The one credential coletar *does* issue is an API key, because a connector cannot
complete an interactive sign-in. Even that is stored as a hash: the plaintext exists
once, in the response to the call that created it, and never again.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from coletar.mcp.auth import DEFAULT_SCOPES
from coletar.schema.objects import Provider
from coletar.schema.tenancy import TenantId, tenant_id

#: How many characters of a key are shown back to the user. Enough to tell two keys
#: apart in a list; far too few to be worth guessing the rest of.
KEY_PREFIX_CHARS = 8

#: The local provider is the one that vouches for nobody: it means "this account was
#: created directly, on a machine its owner controls". Real deployments replace it.
LOCAL_IDENTITY = "local"


def _now() -> datetime:
    return datetime.now(UTC)


def hash_secret(secret: str) -> str:
    """A key's stored form.

    SHA-256 rather than a password KDF on purpose: an issued key is 256 bits of
    machine-generated entropy, so there is no dictionary to slow down, and a KDF
    here would add latency to every connector call while buying nothing. This
    reasoning does **not** transfer to anything a human chooses — which is one more
    reason no human-chosen secret is stored here at all.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


class ExternalIdentity(BaseModel):
    """What an identity provider asserts about the person in front of us.

    The shape every provider has in common: who vouched, their opaque subject id,
    and optionally an email. Clerk and Supabase both fit; so does the local
    provider, which asserts only what the operator typed.
    """

    model_config = ConfigDict(frozen=True)

    provider: str = LOCAL_IDENTITY
    subject: str
    email: str = ""
    display_name: str = ""


class Account(BaseModel):
    """One person or organisation, and the one tenant whose graph is theirs.

    The account *owns* the tenant rather than living inside it, which is why none of
    this is in the `Store` protocol: that protocol's rule is that every method names
    a tenant and none of them resolves one. Resolving one is precisely this module's
    job, so it is a separate boundary (see `Directory`).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: f"acct_{uuid.uuid4().hex[:16]}")
    tenant_id: TenantId
    email: str
    display_name: str = ""
    #: Which provider vouches for this person, and what it calls them. `external_id`
    #: is None until an identity is linked — an account provisioned from the CLI
    #: exists before anyone has signed in as it.
    identity_provider: str = LOCAL_IDENTITY
    external_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    disabled_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None

    @staticmethod
    def tenant_for(email: str) -> TenantId:
        """A stable tenant id derived from the email.

        Derived rather than random so that provisioning is idempotent and so an
        operator reading a log can tell whose graph a row belongs to. The hash keeps
        the address itself out of table names, logs and migration manifests.
        """
        digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
        return tenant_id(f"tenant_{digest[:24]}")


class ApiKey(BaseModel):
    """A credential a connector presents, bound to one account and one surface.

    `surface` is the locality gate and is fixed here, at issuance, for the reason
    `Principal` already documents: a connector that could name its own surface is
    not gated at all. Issuing a key is therefore the moment someone decides which
    assistant this credential *is*.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: f"key_{uuid.uuid4().hex[:16]}")
    account_id: str
    tenant_id: TenantId
    name: str
    surface: Provider = Provider.COLETAR
    scopes: frozenset[str] = DEFAULT_SCOPES
    #: Never the secret. See `hash_secret`.
    secret_hash: str = ""
    #: The leading characters of the plaintext, so a key is identifiable in a list
    #: without being reconstructible from one.
    prefix: str = ""
    created_at: datetime = Field(default_factory=_now)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class IssuedKey(BaseModel):
    """A key plus its plaintext, returned exactly once.

    A separate type so that handing back the secret is a deliberate act at one call
    site, rather than a field on `ApiKey` that every later read would carry and some
    future serialisation would leak.
    """

    model_config = ConfigDict(frozen=True)

    key: ApiKey
    secret: str
