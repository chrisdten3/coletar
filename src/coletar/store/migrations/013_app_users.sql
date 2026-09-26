-- One place to see every user: coletar's account joined to Supabase's auth record.
--
-- **This adds no second source of truth, and deliberately no password column.**
-- `account` already is coletar's user table (migration 010), and it still records
-- only which provider vouches for a person and what that provider calls them. What
-- was missing was not a table but a *join*: with Supabase Auth, `auth.users` lives
-- in this same database, so the credential side and the workspace side were already
-- one Postgres away from each other and nothing put them in one result.
--
-- So this is a view. Adding a `users` table that duplicated either half would mean
-- two rows that can disagree about whether someone exists, and the interesting
-- questions about a user -- is their email confirmed, when did they last sign in,
-- which tenant is theirs, how many connector keys have they issued -- are questions
-- about a join, not about a row somebody has to keep in step.
--
-- Where the password is: in `auth.users.encrypted_password`, hashed by GoTrue,
-- which is Supabase's job and not ours. It is in this database and it is not in
-- coletar's schema, which is exactly the arrangement migration 010 describes.
-- This view does not select it.
--
-- **Conditional on the `auth` schema.** Only a Supabase database has one, and this
-- migration also runs against the local Postgres that the test suite and a laptop
-- use. A hard reference to `auth.users` would make every migration run outside
-- Supabase fail, so the view is created where it can be and skipped where it
-- cannot -- the skip is logged in the ledger like any other applied migration.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
        -- `security_invoker` matters here. A view executes as its owner by default,
        -- which would make this a way for any role that can SELECT the view to read
        -- `account` and `auth.users` regardless of the RLS that migrations 008 and
        -- 012 turned on. With `security_invoker = true` the caller's own privileges
        -- apply, so `anon` and `authenticated` -- which have no policy on `account`
        -- and no grant here -- still see nothing.
        CREATE OR REPLACE VIEW public.app_users
        WITH (security_invoker = true) AS
        SELECT
            a.id                     AS account_id,
            a.email                  AS email,
            a.display_name           AS display_name,
            a.tenant_id              AS tenant_id,
            a.identity_provider      AS identity_provider,
            a.external_id            AS auth_user_id,
            a.created_at             AS account_created_at,
            a.disabled_at            AS disabled_at,
            (a.disabled_at IS NULL)  AS is_active,
            -- The auth side. Null for an account provisioned from the CLI that
            -- nobody has signed in as yet, which is a real and expected state --
            -- hence the LEFT JOIN rather than an inner one that would hide it.
            u.email_confirmed_at     AS email_confirmed_at,
            u.last_sign_in_at        AS last_sign_in_at,
            u.raw_user_meta_data     AS auth_metadata,
            u.banned_until           AS banned_until,
            -- Cheap enough to inline and the first thing anyone asks after "who is
            -- this": how many live connector credentials does this account hold.
            (
                SELECT count(*)
                FROM api_key k
                WHERE k.account_id = a.id AND k.revoked_at IS NULL
            )::int                   AS active_api_keys
        FROM account a
        -- Joined on the provider's subject, never on the email, for the same reason
        -- `account_for_identity` matches on it: an address is a claim a provider
        -- makes and can change, and joining the mutable one would silently pair an
        -- account with the wrong auth record after an address is reused.
        LEFT JOIN auth.users u
            ON a.identity_provider = 'supabase'
           AND a.external_id IS NOT NULL
           AND a.external_id = u.id::text;

        COMMENT ON VIEW public.app_users IS
            'Every coletar account joined to its Supabase auth record. Read-only; '
            'accounts are written through the Directory protocol and auth users by '
            'GoTrue. No password is exposed here or stored in coletar''s schema.';
    END IF;
END
$$;
