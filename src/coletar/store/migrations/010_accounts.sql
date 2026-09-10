-- Accounts and issued API keys.
--
-- Two deliberate absences, both load-bearing.
--
-- **No password column, anywhere.** coletar records which identity provider
-- vouches for a person and what that provider calls them; it never holds a
-- credential a human chose. That is what keeps the provider swappable — moving to
-- Clerk or Supabase Auth means verifying a different token and reading a different
-- subject claim, with no secret to migrate and nothing here to re-secure.
--
-- **No plaintext key.** An issued key is stored as a SHA-256 hash plus a short
-- prefix for identification. The plaintext exists once, in the response that
-- created it. A prefix is enough to tell two keys apart in a list and far too
-- little to reconstruct one.
--
-- Neither table is tenant-scoped, which is the point: an account *owns* a tenant
-- rather than living inside one, and resolving "whose graph is this" is exactly
-- the lookup the Store protocol refuses to do.
CREATE TABLE IF NOT EXISTS account (
    id                TEXT PRIMARY KEY,
    -- One tenant per account, enforced here rather than by convention: two
    -- accounts sharing a tenant would silently merge two people's graphs.
    tenant_id         TEXT NOT NULL UNIQUE,
    email             TEXT NOT NULL UNIQUE,
    display_name      TEXT NOT NULL DEFAULT '',
    identity_provider TEXT NOT NULL DEFAULT 'local',
    -- Null until someone signs in as this account. An account provisioned from
    -- the CLI exists before any identity has claimed it.
    external_id       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at       TIMESTAMPTZ
);

-- Identity match is on (provider, subject), never on email: an address is a claim
-- a provider makes and can change, and matching the mutable one is how account
-- takeover by address reuse happens. Partial, because many rows are legitimately
-- unclaimed and NULLs must not collide with each other.
CREATE UNIQUE INDEX IF NOT EXISTS ux_account_identity
    ON account (identity_provider, external_id)
    WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS api_key (
    id           TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES account (id),
    -- Denormalised from the account so authenticating a request is one lookup.
    -- A key's tenant is fixed at issuance and an account's tenant never changes,
    -- so there is no update path for these to drift apart.
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    -- The locality gate, fixed at issuance for the reason Principal documents: a
    -- connector that could name its own surface is not gated at all.
    surface      TEXT NOT NULL DEFAULT 'coletar',
    scopes       JSONB NOT NULL DEFAULT '["read","write"]'::jsonb,
    secret_hash  TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_api_key_account ON api_key (account_id, created_at DESC);
