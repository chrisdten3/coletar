-- Which turns have already been sent to an extraction model.
--
-- Model-assisted extraction over a real archive is thousands of paid API calls,
-- and an import that dies partway through — a credit balance running out, a
-- timeout, a laptop closing — currently restarts at turn one. Deduplication in
-- `remember` stops the *objects* duplicating, but the money is spent again on the
-- way there, which is the part that hurts. This table is what a resumed run reads
-- to skip the calls it has already paid for.
--
-- Keyed by a hash of the turn text rather than by the archive's own ids. Ids are
-- stable within one export but not guaranteed across a re-export, and hashing the
-- text also means the same turn appearing twice is extracted once — which is
-- correct anyway, since identical input yields identical output.
--
-- A row is written for every turn that was *examined*, including the large
-- majority that yielded nothing. Recording only the productive turns would leave
-- the empty ones to be re-paid for on every subsequent run, and they are most of
-- the archive.
CREATE TABLE IF NOT EXISTS extraction_checkpoint (
    tenant_id    TEXT NOT NULL,
    -- sha256 of the turn text, hex. Not the text itself: this table exists to
    -- avoid re-billing, and it should not become a second copy of the user's
    -- conversations sitting outside the graph's own locality rules.
    turn_hash    TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, turn_hash)
);
