-- Exclusive claims on background jobs, so two workers cannot process the same
-- captured episode.
--
-- Not a graph table and not in the event log: a lease is who is working *right
-- now*, with a lifetime of minutes, while the log is the permanent record of what
-- the work produced. `expires_at` is what makes an unattended worker safe — a
-- process killed mid-pass never releases, and without an expiry the queue would
-- stop draining silently until a human noticed.

CREATE TABLE IF NOT EXISTS job_lease (
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    owner       TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, name)
);

-- Operational reads ask "what is running and since when", never "which tenant".
CREATE INDEX IF NOT EXISTS job_lease_expires_idx ON job_lease (expires_at);
