-- Tenant isolation (M3.1).
--
-- Identity becomes (tenant_id, id). Ids stay globally unique as generated, so logs
-- and migration manifests remain unambiguous, but the database enforces the pair --
-- defence in depth, so a bug in application code still cannot reach across tenants.
--
-- The column arrives with a DEFAULT so pre-tenancy rows get a home, and the default
-- is dropped immediately afterwards. Every insert from here on must name its tenant.
-- The implicit path exists only for the duration of this migration.

ALTER TABLE context_object   ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'tenant_local';
ALTER TABLE context_edge     ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'tenant_local';
ALTER TABLE object_embedding ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'tenant_local';
ALTER TABLE event_log        ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'tenant_local';
ALTER TABLE compile_run      ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'tenant_local';

ALTER TABLE context_object   ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE context_edge     ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE object_embedding ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE event_log        ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE compile_run      ALTER COLUMN tenant_id DROP DEFAULT;

-- Composite identity. The foreign keys below depend on these, so they come first.
ALTER TABLE object_embedding DROP CONSTRAINT IF EXISTS object_embedding_object_id_fkey;
ALTER TABLE context_edge     DROP CONSTRAINT IF EXISTS context_edge_src_id_fkey;
ALTER TABLE context_edge     DROP CONSTRAINT IF EXISTS context_edge_dst_id_fkey;
ALTER TABLE context_object   DROP CONSTRAINT IF EXISTS context_object_supersedes_fkey;

ALTER TABLE context_object   DROP CONSTRAINT IF EXISTS context_object_pkey;
ALTER TABLE context_object   ADD  CONSTRAINT context_object_pkey PRIMARY KEY (tenant_id, id);

ALTER TABLE object_embedding DROP CONSTRAINT IF EXISTS object_embedding_pkey;
ALTER TABLE object_embedding ADD  CONSTRAINT object_embedding_pkey PRIMARY KEY (tenant_id, object_id);

ALTER TABLE context_edge     DROP CONSTRAINT IF EXISTS context_edge_pkey;
ALTER TABLE context_edge     ADD  CONSTRAINT context_edge_pkey
    PRIMARY KEY (tenant_id, src_id, dst_id, type);

-- Tenant-aware foreign keys. The database now refuses a cross-tenant edge or a
-- cross-tenant supersedes even if application code asks for one.
ALTER TABLE object_embedding ADD CONSTRAINT object_embedding_object_fkey
    FOREIGN KEY (tenant_id, object_id) REFERENCES context_object (tenant_id, id)
    ON DELETE CASCADE;
ALTER TABLE context_edge ADD CONSTRAINT context_edge_src_fkey
    FOREIGN KEY (tenant_id, src_id) REFERENCES context_object (tenant_id, id)
    ON DELETE CASCADE;
ALTER TABLE context_edge ADD CONSTRAINT context_edge_dst_fkey
    FOREIGN KEY (tenant_id, dst_id) REFERENCES context_object (tenant_id, id)
    ON DELETE CASCADE;
ALTER TABLE context_object ADD CONSTRAINT context_object_supersedes_fkey
    FOREIGN KEY (tenant_id, supersedes) REFERENCES context_object (tenant_id, id);

-- Every hot path filters on tenant first.
CREATE INDEX IF NOT EXISTS ix_object_tenant       ON context_object (tenant_id, type);
CREATE INDEX IF NOT EXISTS ix_object_tenant_scope ON context_object (tenant_id, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS ix_edge_tenant_dst     ON context_edge (tenant_id, dst_id, type);
CREATE INDEX IF NOT EXISTS ix_event_tenant_object ON event_log (tenant_id, object_id, at DESC);
CREATE INDEX IF NOT EXISTS ix_event_tenant_at     ON event_log (tenant_id, at DESC);
CREATE INDEX IF NOT EXISTS ix_embedding_tenant    ON object_embedding (tenant_id);
