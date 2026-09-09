-- Supabase grants its public API roles table privileges by default. All access to
-- this graph must instead pass through the backend's tenant and locality checks.
-- With RLS enabled and no client policies, anon/authenticated cannot read or mutate
-- rows even when table grants exist. The migration/table owner used by Store keeps
-- its normal access. In particular, content keys must never be a public REST table.
-- A future direct browser database API needs explicit policies, not disabled RLS.

ALTER TABLE context_object ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_edge ENABLE ROW LEVEL SECURITY;
ALTER TABLE object_embedding ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE compile_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE object_content_key ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_lease ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migration ENABLE ROW LEVEL SECURITY;
