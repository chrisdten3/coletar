-- Read receipts (M10, "who has seen this fact").
--
-- Every write has always been answerable per object: `event_log.object_id` is
-- indexed. A *read* is not shaped that way. One search returns many objects, and
-- §5.1 records one trace per search rather than one row per hit — deliberately,
-- because a row per hit floods the log the observability dashboard reads. The
-- object ids therefore live inside `detail->'returned_ids'`, and asking "which
-- assistants have seen this fact" means asking which traces contain it.
--
-- A GIN index over that array makes the containment lookup indexable instead of a
-- full log scan. `jsonb_path_ops` is the smaller, faster operator class and
-- supports exactly the operator this query uses (@>), which is the only one the
-- read-receipt path needs.
--
-- Partial, on retrieval traces alone: no other event type carries the key, and
-- restricting the index keeps it proportional to reads rather than to the log.
CREATE INDEX IF NOT EXISTS ix_event_returned_ids
    ON event_log USING gin ((detail -> 'returned_ids') jsonb_path_ops)
    WHERE type = 'retrieval.trace';
