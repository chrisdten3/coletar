-- Per-tenant workspace settings that are not graph objects.
--
-- The first of these is pricing: which model a tenant's measured traffic is
-- costed at, and any negotiated rate that differs from the published one. That
-- lived in browser `localStorage`, which made it per-origin -- the same
-- workspace served on two ports priced on one and showed an empty state on the
-- other -- and unreachable from the server, so nothing but the browser that
-- typed it could ever use the number.
--
-- Deliberately *not* a ContextObject. §2 says memory is a subtype and not a
-- special case, and the corollary is that things which are not memory do not go
-- in the graph: a rate card has no provenance, no locality, no confidence and
-- nothing to compile to another surface, and putting it in `context_object`
-- would mean it showed up in retrieval, review and export.
--
-- Small and generic on purpose. A settings table per concern is how you end up
-- with six of them; a key-value row per tenant is enough for anything shaped
-- like a preference, and anything that outgrows it has earned its own table.
CREATE TABLE IF NOT EXISTS tenant_setting (
    tenant_id  TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);

-- Settings are read on every render of the view that uses them, and written
-- rarely. Row-level security follows the same backend-only posture as every
-- other table here (see 008): the application connects as its own role and
-- scopes by tenant in the query, and no end-user credential reaches Postgres.
ALTER TABLE tenant_setting ENABLE ROW LEVEL SECURITY;
