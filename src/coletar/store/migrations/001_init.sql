-- coletar canonical store, initial schema (SCOPE §2, §5).
--
-- Three things live here, and the architecture in §5 requires all three:
--   1. context_object + context_edge  -> the Canonical Context Graph
--   2. object_embedding               -> the Search/Retrieval index (pgvector)
--   3. event_log                      -> the append-only Event/Revision Log
--
-- One table for every object type. Memory is a subtype, not a special case.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS context_object (
    id                TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    content           TEXT NOT NULL,
    scope_type        TEXT NOT NULL DEFAULT 'global',
    scope_id          TEXT,
    kind              TEXT,                      -- MemoryKind, when type = 'memory'
    confidence        REAL NOT NULL DEFAULT 1.0
                          CHECK (confidence >= 0.0 AND confidence <= 1.0),
    extraction_method TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal',
    supersedes        TEXT REFERENCES context_object (id),
    provenance        JSONB NOT NULL,
    provider_mappings JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at        TIMESTAMPTZ,               -- soft retire; never hard-delete
    ttl_days          INTEGER,
    CONSTRAINT scope_id_matches_type CHECK (
        (scope_type = 'global'  AND scope_id IS NULL) OR
        (scope_type = 'project' AND scope_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_object_type    ON context_object (type);
CREATE INDEX IF NOT EXISTS ix_object_scope   ON context_object (scope_type, scope_id);
CREATE INDEX IF NOT EXISTS ix_object_active  ON context_object (retired_at)
    WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_object_content_trgm
    ON context_object USING gin (content gin_trgm_ops);

CREATE TABLE IF NOT EXISTS context_edge (
    src_id     TEXT NOT NULL REFERENCES context_object (id) ON DELETE CASCADE,
    dst_id     TEXT NOT NULL REFERENCES context_object (id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (src_id, dst_id, type)
);

CREATE INDEX IF NOT EXISTS ix_edge_dst ON context_edge (dst_id, type);

-- Retrieval index. Dimension must match COLETAR_EMBEDDING_DIM; the default (768)
-- is nomic-embed-text, which runs locally under Ollama alongside the wedge model.
CREATE TABLE IF NOT EXISTS object_embedding (
    object_id  TEXT PRIMARY KEY REFERENCES context_object (id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    embedding  vector(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_embedding_cosine
    ON object_embedding USING hnsw (embedding vector_cosine_ops);

-- Append-only. No UPDATE or DELETE is ever issued against this table.
CREATE TABLE IF NOT EXISTS event_log (
    id        TEXT PRIMARY KEY,
    type      TEXT NOT NULL,
    object_id TEXT,
    actor     TEXT NOT NULL DEFAULT 'system',
    provider  TEXT NOT NULL DEFAULT 'coletar',
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_event_object ON event_log (object_id, at DESC);
CREATE INDEX IF NOT EXISTS ix_event_at     ON event_log (at DESC);

-- Compile runs, for the Migration Manifest and the staleness term of the
-- Continuity Score (§7).
CREATE TABLE IF NOT EXISTS compile_run (
    id               TEXT PRIMARY KEY,
    destination      TEXT NOT NULL,
    scope_type       TEXT NOT NULL,
    scope_id         TEXT,
    manifest         JSONB NOT NULL,
    continuity_score REAL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
