"""Tenant identity (SCOPE §9, M3.1).

One rule, and everything else follows from it:

    **The Store never assumes a tenant. Application boundaries may resolve one.**

So every `Store` method takes `tenant_id` explicitly and none of them defaults it.
A default inside the data layer is how a future MCP tool or background job falls into
a shared graph without anyone noticing -- the failure is silent, and in a product
whose whole premise is that you own your context, silent is the worst kind.

`TenantId` is a `NewType` rather than a bare `str` on purpose. Store signatures read
`(tenant_id, object_id)`, and if both were `str` the type checker could not see a
swapped pair -- which is exactly the bug that reads another tenant's data while
looking entirely correct.
"""

from __future__ import annotations

import re
from typing import NewType

TenantId = NewType("TenantId", str)

#: Conservative on purpose: a tenant id lands in table keys, log lines and file
#: snapshots, so it stays boring.
_VALID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")

#: Where a version-1 snapshot's records are homed, and what migration 002 back-fills
#: pre-tenancy Postgres rows to. Not a fallback: nothing resolves to this at runtime.
#: The old store could only represent one effective tenant anyway, so naming that
#: tenant explicitly is the honest upgrade rather than an invention.
LEGACY_TENANT = TenantId("tenant_local")


class InvalidTenantId(ValueError):
    pass


class CrossTenantError(ValueError):
    """An operation tried to reach outside its tenant.

    Both backends raise *this* type, not their own. A caller catching a cross-tenant
    violation should not have to know whether it is talking to Postgres or the
    in-process store -- if the exception differs by backend then the contract is not
    actually identical, which is the one thing the shared test suite exists to prove.

    In Postgres this is an application-level check *in addition to* the composite
    foreign keys from migration 002. The check gives a readable error and a
    consistent type; the constraint is the defence in depth that still holds if
    application code is wrong.
    """


def tenant_id(raw: str) -> TenantId:
    """Validate and narrow. The one place a `str` becomes a `TenantId`."""
    if not _VALID.match(raw or ""):
        raise InvalidTenantId(
            f"{raw!r} is not a valid tenant id: 3-64 characters, lowercase letters, "
            f"digits, hyphen and underscore, starting with a letter or digit."
        )
    return TenantId(raw)
