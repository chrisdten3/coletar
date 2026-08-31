-- Locality (pick-and-choose context): which connected surfaces may read an object
-- back, independent of `scope_type`/`scope_id` -- those decide which *project* an
-- object belongs to, this decides which *surface* (Claude, ChatGPT, a local model,
-- ...) may see it at all.
--
-- Every existing row gets 'synced' with an empty surface list, which is exactly the
-- behavior every object already had: visible to any connected surface. No backfill
-- decision is needed, unlike tenant_id in migration 002 -- there is no prior column
-- this one supersedes, so no DEFAULT needs dropping afterwards.

ALTER TABLE context_object ADD COLUMN IF NOT EXISTS locality_mode TEXT NOT NULL DEFAULT 'synced';
ALTER TABLE context_object ADD COLUMN IF NOT EXISTS locality_surfaces JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE context_object ADD CONSTRAINT locality_surfaces_match_mode CHECK (
    (locality_mode = 'synced'     AND locality_surfaces = '[]'::jsonb) OR
    (locality_mode = 'local_only' AND jsonb_array_length(locality_surfaces) > 0)
);

-- `search`/`list_objects`/`get_object` all filter on this, same shape as the
-- existing scope index.
CREATE INDEX IF NOT EXISTS ix_object_locality ON context_object (tenant_id, locality_mode);
