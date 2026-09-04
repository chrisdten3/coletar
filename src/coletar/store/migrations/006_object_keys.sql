-- Disposable keys for encrypted raw episodes.
--
-- The graph and append-only event log contain ciphertext. Erasure DELETEs only this
-- key row, making that ciphertext unrecoverable while object identity, hashes and
-- provenance survive. No foreign key on purpose: capture writes the random key first,
-- then atomically writes object+event; a crash may orphan meaningless random bytes
-- but can never commit ciphertext without its key.

CREATE TABLE IF NOT EXISTS object_content_key (
    tenant_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    key_bytes BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_id)
);
