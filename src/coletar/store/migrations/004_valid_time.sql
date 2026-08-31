-- Valid time, which is a different axis from transaction time.
--
-- `created_at` records when coletar learned a fact. These record when the fact is
-- true in the world. A policy effective 1 April, recorded on 15 March, differs on
-- both axes — and an audit that conflates them answers "what did we know" when it
-- was asked "what was in force".
--
-- Both nullable, meaning "as far as we know, always". That is the honest reading of
-- an undated preference and leaves every existing row behaving exactly as before,
-- so no backfill decision is needed.

ALTER TABLE context_object ADD COLUMN IF NOT EXISTS valid_from  TIMESTAMPTZ;
ALTER TABLE context_object ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ;

-- An interval that ends before it starts is a data-entry error, not a fact with a
-- strange shape. Refused at the boundary rather than discovered by an auditor.
ALTER TABLE context_object ADD CONSTRAINT valid_interval_is_ordered CHECK (
    valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until
);

-- "What was in force on date X" is the query this exists to serve.
CREATE INDEX IF NOT EXISTS ix_object_valid_time
    ON context_object (tenant_id, valid_from, valid_until);
