"""Accounts: who owns a graph, and what may reach it on their behalf.

Separate from `coletar.store` on purpose. The Store protocol's rule is that every
method names a tenant and none of them resolves one; an account *owns* a tenant, so
resolving one is this package's job and could not live behind that protocol without
weakening the invariant it exists to hold.
"""

from __future__ import annotations

from coletar.accounts.directory import Directory, DirectoryError
from coletar.accounts.identity import (
    IdentityError,
    IdentityProvider,
    LocalIdentityProvider,
    build_identity_provider,
)
from coletar.accounts.models import Account, ApiKey, ExternalIdentity, IssuedKey

_singleton: Directory | None = None


def build_directory() -> Directory:
    """The configured directory, following the store backend.

    Deliberately follows `store_backend` rather than taking a setting of its own:
    an account in Postgres whose graph is in a local file (or the reverse) is a
    workspace that half exists, and there is no deployment where that is wanted.
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    from pathlib import Path

    from coletar.config import get_settings

    settings = get_settings()
    if settings.store_backend == "postgres":
        from coletar.accounts.postgres import PostgresDirectory

        _singleton = PostgresDirectory(settings.database_url)
    else:
        from coletar.accounts.memory import InMemoryDirectory

        # Beside the graph snapshot rather than inside it: the store's format is
        # versioned and replayed, and accounts are not graph objects.
        _singleton = InMemoryDirectory(Path(settings.store_path).with_suffix(".accounts.json"))
    return _singleton


def reset_directory() -> None:
    """Drop the process-wide directory. For tests and for settings changes."""
    global _singleton
    _singleton = None


__all__ = [
    "Account",
    "ApiKey",
    "Directory",
    "DirectoryError",
    "ExternalIdentity",
    "IdentityError",
    "IdentityProvider",
    "IssuedKey",
    "LocalIdentityProvider",
    "build_directory",
    "build_identity_provider",
    "reset_directory",
]
