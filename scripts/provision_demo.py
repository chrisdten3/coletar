#!/usr/bin/env python
"""Provision the demo accounts: Supabase auth users, coletar accounts, and graphs.

One operator command for the whole demo, because the three halves have to agree.
An auth user with no account signs in to a 401; an account with no graph opens an
empty workspace; a graph with no account is unreachable. Doing them separately is
how a demo breaks five minutes before it is given.

What it does, per persona:

  1. Creates the Supabase auth user with `email_confirm: true`, so there is no
     inbox to check. This is the one step that needs the **service role key**, and
     the only place in this repository that reads one. It is passed in the
     environment and never stored: `coletar.config` deliberately has no setting for
     it, because no request path needs it — verifying a session is a signature check
     against a public JWKS.
  2. Creates the coletar account, linked to that auth user's id as `external_id`.
     Linked at creation rather than claimed on first sign-in, so the demo does not
     depend on the email-claim path working.
  3. Builds the persona's graph into the account's tenant.

**Passwords are generated here and written to a file, never printed.** A password
echoed into a terminal is a password in scrollback, in a screen recording of the
demo, and in whatever ships terminal logs. `--credentials-file` is gitignored.

Chris's own account is different in one way and it is the reason `create_account`
takes a `tenant_id`: his graph already exists and was built before accounts did, so
the account adopts `--owner-tenant` rather than deriving a fresh empty one. Use
`scripts/copy_tenant.py` first if that graph is not in this database yet.

Usage:

    export SUPABASE_SERVICE_ROLE_KEY=...       # Supabase dashboard → API keys
    export COLETAR_SUPABASE_URL=https://<ref>.supabase.co
    export COLETAR_DATABASE_URL=...            # where the accounts and graphs go
    export COLETAR_STORE_BACKEND=postgres

    uv run python scripts/provision_demo.py \
        --owner-email you@example.com --owner-name "Your Name" \
        --owner-tenant tenant_v1

    uv run python scripts/provision_demo.py --dry-run     # plan only, writes nothing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import string
import sys
import urllib.error
import urllib.request
from pathlib import Path

from coletar.accounts import build_directory
from coletar.accounts.directory import DirectoryError
from coletar.accounts.supabase import SUPABASE_IDENTITY
from coletar.demo import PERSONAS, build_persona
from coletar.schema.tenancy import TenantId
from coletar.schema.tenancy import tenant_id as parse_tenant
from coletar.store import build_store

#: Long enough that it does not matter that it is written down, short enough to be
#: typed at a podium if the password manager fails. Ambiguous characters are left
#: out for the same reason: these get read off a screen.
PASSWORD_ALPHABET = (
    "".join(c for c in string.ascii_letters if c not in "lIO")
    + "".join(c for c in string.digits if c not in "01")
    + "-_."
)
PASSWORD_LENGTH = 20


def generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


class SupabaseAdmin:
    """The GoTrue admin API, over three calls. Deliberately not a client library.

    Adding a dependency to make three HTTP requests in a script that runs once per
    demo does not survive being said out loud, which is this repository's test for
    a new dependency.
    """

    def __init__(self, url: str, service_role_key: str) -> None:
        if not url:
            raise SystemExit("Set COLETAR_SUPABASE_URL to your project URL.")
        if not service_role_key:
            raise SystemExit(
                "Set SUPABASE_SERVICE_ROLE_KEY. It is in the Supabase dashboard under "
                "Project Settings → API keys → service_role. It bypasses row-level "
                "security, so keep it out of the repository and out of Vercel's "
                "client-side environment."
            )
        self._base = f"{url.rstrip('/')}/auth/v1"
        self._key = service_role_key

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"{self._base}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:400]
            raise SystemExit(f"Supabase admin API said {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Could not reach {self._base}: {exc.reason}") from exc

    def find_user(self, email: str) -> dict | None:
        """The auth user for this address, or None.

        Paginated rather than "list 200 and hope". GoTrue's admin list endpoint has
        no exact-address lookup, so the match happens here — and stopping after one
        page would make this quietly return None on a project with more users than
        the page size, which reads as "does not exist" and leads to a create call
        that then fails on the unique constraint.
        """
        wanted = email.strip().lower()
        page = 1
        while page <= 50:  # 5,000 users at this page size; a demo project has four.
            found = self._request("GET", f"/admin/users?page={page}&per_page=100")
            users = found.get("users", [])
            for user in users:
                if str(user.get("email", "")).strip().lower() == wanted:
                    return user
            if len(users) < 100:
                return None
            page += 1
        return None

    def create_user(self, email: str, password: str, full_name: str) -> dict:
        """Create a user whose address is already confirmed.

        `email_confirm: true` is what makes this usable for a demo: the accounts are
        signed in to immediately, with no inbox anywhere in the loop. It also sets
        `email_confirmed_at`, which is what `SupabaseIdentityProvider` reads as
        `email_verified` and what `accounts.session` requires before it will let an
        identity claim an existing graph by address.
        """
        return self._request(
            "POST",
            "/admin/users",
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name, "email_verified": True},
            },
        )

    def set_password(self, user_id: str, password: str) -> dict:
        return self._request("PUT", f"/admin/users/{user_id}", {"password": password})


async def provision_one(
    admin: SupabaseAdmin,
    *,
    email: str,
    name: str,
    title: str,
    password: str,
    adopt_tenant: TenantId | None,
    build,
    dry_run: bool,
) -> dict[str, str]:
    """One account end to end. Returns what to write to the credentials file."""
    directory = build_directory()
    existing_account = await directory.account_by_email(email)

    if dry_run:
        user = admin.find_user(email)
        return {
            "email": email,
            "name": name,
            "title": title,
            "password": "(dry run — not generated)",
            "tenant": str(adopt_tenant or "derived from email"),
            "status": (
                f"auth user {'exists' if user else 'would be created'}; "
                f"account {'exists' if existing_account else 'would be created'}"
            ),
        }

    # --- 1. The auth user. ------------------------------------------------
    user = admin.find_user(email)
    if user is None:
        user = admin.create_user(email, password, name)
        auth_status = "auth user created"
    else:
        # Re-running must leave a working password rather than an unknown one: the
        # first run's password is in a file the operator may no longer have.
        admin.set_password(str(user["id"]), password)
        auth_status = "auth user existed, password reset"
    subject = str(user["id"])

    # --- 2. The coletar account. -----------------------------------------
    if existing_account is not None:
        account = existing_account
        account_status = f"account existed ({account.tenant_id})"
    else:
        try:
            account = await directory.create_account(
                email,
                display_name=name,
                identity_provider=SUPABASE_IDENTITY,
                external_id=subject,
                tenant_id=adopt_tenant,
            )
            account_status = f"account created ({account.tenant_id})"
        except DirectoryError as exc:
            return {
                "email": email,
                "name": name,
                "title": title,
                "password": password,
                "tenant": "—",
                "status": f"{auth_status}; account refused: {exc}",
            }

    # --- 3. The graph. ----------------------------------------------------
    graph_status = "no graph to build (owner account)"
    if build is not None:
        store = build_store()
        existing = await store.list_objects(
            account.tenant_id, include_retired=True, include_superseded=True, limit=1
        )
        if existing:
            # `build_persona` mints fresh ids every call, so running it twice into
            # one tenant gives two of everything. Refuse rather than double it.
            graph_status = "graph left alone — tenant already has objects"
        else:
            result = await build_persona(store, account.tenant_id, build)
            graph_status = "graph built: " + ", ".join(
                f"{k}={v}" for k, v in sorted(result.counts.items())
            )

    return {
        "email": email,
        "name": name,
        "title": title,
        "password": password,
        "tenant": str(account.tenant_id),
        "status": f"{auth_status}; {account_status}; {graph_status}",
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-email", required=True, help="Your own sign-in address.")
    parser.add_argument("--owner-name", default="", help="Your display name.")
    parser.add_argument(
        "--owner-tenant",
        default="",
        help=(
            "An existing tenant for your account to adopt, e.g. tenant_v1. Omit to "
            "derive a fresh empty one from the address."
        ),
    )
    parser.add_argument(
        "--credentials-file",
        default="demo-credentials.txt",
        help="Where the generated passwords are written. Gitignored.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the plan, generate nothing, write nothing.",
    )
    parser.add_argument(
        "--personas-only",
        action="store_true",
        help="Skip the owner account and provision only the three demo personas.",
    )
    args = parser.parse_args()

    admin = SupabaseAdmin(
        os.environ.get("COLETAR_SUPABASE_URL", ""),
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
    )

    rows: list[dict[str, str]] = []

    if not args.personas_only:
        rows.append(
            await provision_one(
                admin,
                email=args.owner_email,
                name=args.owner_name or args.owner_email,
                title="Owner — your real workspace",
                password=generate_password(),
                adopt_tenant=parse_tenant(args.owner_tenant) if args.owner_tenant else None,
                # No persona: this account's graph is the real one, already there.
                build=None,
                dry_run=args.dry_run,
            )
        )

    for persona in PERSONAS:
        rows.append(
            await provision_one(
                admin,
                email=persona.email,
                name=persona.name,
                title=persona.title,
                password=generate_password(),
                adopt_tenant=None,
                build=persona,
                dry_run=args.dry_run,
            )
        )

    for row in rows:
        print(f"\n{row['name']} — {row['title']}")
        print(f"  email   {row['email']}")
        print(f"  tenant  {row['tenant']}")
        print(f"  {row['status']}")

    if args.dry_run:
        print("\nDry run. Nothing was created and no password was generated.")
        return 0

    path = Path(args.credentials_file)
    path.write_text(
        "coleta demo accounts\n"
        "Generated by scripts/provision_demo.py. Not in version control.\n"
        "These are demo accounts on a demo Supabase project. Rotate or delete them\n"
        "when the demo is over.\n\n"
        + "\n".join(
            f"{row['name']}\n"
            f"  {row['title']}\n"
            f"  email     {row['email']}\n"
            f"  password  {row['password']}\n"
            f"  tenant    {row['tenant']}\n"
            for row in rows
        )
    )
    path.chmod(0o600)
    print(f"\nPasswords written to {path} (mode 600). They are not printed here on purpose.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
