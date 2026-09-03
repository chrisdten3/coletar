-- Entity identity is a name, and a name needs an index.
--
-- Entities are deduplicated by name: a recruiter, colleague or company mentioned in
-- ten conversations must be one node, not ten. Without an index that lookup is a
-- sequential scan of every object in the tenant on every extracted entity, which is
-- fine for an import of four and untenable for one of four thousand.
--
-- Partial on `type = 'entity'` because no other subtype carries a name in payload,
-- and lowercased because matching is casefolded — the index has to see the same
-- expression the query does or it will not be used at all.
--
-- Crude on purpose: two different Amandas merge. That is the conservative
-- direction, since a merged entity is visible and separable in the Inspector while
-- a thousand duplicates are neither.

CREATE INDEX IF NOT EXISTS ix_object_entity_name
    ON context_object (tenant_id, lower(payload->>'name'))
    WHERE type = 'entity';
