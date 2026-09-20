-- Close the RLS gap that opened after 008.
--
-- Migration 008 enabled row-level security on the eight tables that existed when
-- it was written, because Supabase grants its public API roles table privileges by
-- default and all access to this data must pass through the backend's tenant and
-- locality checks instead. It was verified by hand against the real `anon` and
-- `authenticated` roles.
--
-- Three tables have been added since and none of them enabled it:
--
--   account                (010) — email, display name, and the identity provider's
--                                  subject for every user
--   api_key                (010) — `secret_hash` and `prefix` for every issued
--                                  connector credential
--   extraction_checkpoint  (011) — per-tenant import progress
--
-- `api_key` is the one that matters most. The hashes are SHA-256 of 256 bits of
-- machine entropy, so they are not guessable offline, but a readable `api_key`
-- table still discloses which accounts exist, how many credentials each holds,
-- which surface each is bound to, and when it was last used — and `account`
-- discloses the addresses to go with them.
--
-- The lesson is not "remember next time". A hand-verified invariant that new
-- tables silently fall outside of is one that will keep reopening, which is what
-- happened here. `tests/test_rls.py` now asserts the property over *every* table in
-- the schema rather than a list someone has to maintain, so the next migration that
-- adds a table without RLS fails before it ships rather than after.
--
-- As in 008: enabled with no client policies, so anon/authenticated can neither
-- read nor mutate even where table grants exist, while the migration/table owner
-- the Store connects as keeps its normal access. A future direct browser database
-- API needs explicit policies, not RLS turned back off.

ALTER TABLE account ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_key ENABLE ROW LEVEL SECURITY;
ALTER TABLE extraction_checkpoint ENABLE ROW LEVEL SECURITY;
