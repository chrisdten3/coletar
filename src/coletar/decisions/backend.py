"""The decision backend boundary (M11.3).

A decision backend answers one narrow question fast: given a tool and a situation,
which labelled outcome follows? That is what makes it cheaper than a frontier model
for the calls that never needed one -- and what makes it dangerous if it ever
answers when it should not, since something downstream acts on what it says.

So the contract is deliberately shy. `decide` returns a `DecisionResult` whose
`decided` flag is the only thing a caller may act on, and every failure -- timeout,
transport error, an ambiguous answer, no configured backend at all -- resolves to
`decided=False` with a reason. It never raises for those cases, because an exception
is something an integrator's `except` block can swallow into "carry on", and
"carry on" is precisely the wrong reading of "I could not decide".

The only implementation here answers nothing. Jev and Laya are real intentions
without a real API contract yet, and a stub that fabricated plausible answers would
be worse than no stub at all: the whole pipeline around it would look tested.
`NullDecisionBackend` lets the gate, the audit trail and the fallback path be
exercised end to end while guaranteeing no wrong call is ever made in the meantime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from coletar.schema.tenancy import TenantId

if TYPE_CHECKING:
    from coletar.store.base import Store

#: Which backend a tenant has chosen, if any. Tenant-scoped rather than a process
#: setting, because a decision vendor is the tenant's own commercial relationship --
#: unlike an embedder, which is one deployment's infrastructure choice.
BACKEND_SETTING_KEY = "decisions.backend"


class DecisionQuery(BaseModel):
    tool_name: str
    input_signature: str
    #: Optional enumeration of the outcomes the caller considers legal, as labels.
    candidate_outcomes: list[str] | None = None
    #: Only ever populated for a tool whose tenant granted `vendor_send`.
    context: dict[str, Any] = Field(default_factory=dict)


class DecisionResult(BaseModel):
    """The one thing a caller may act on is `decided`."""

    decided: bool
    outcome_signature: str | None = None
    confidence: float = 0.0
    backend: str = "none"
    #: A short label, never content: "no_backend_configured", "not_promoted",
    #: "below_confidence_floor", "backend_unavailable".
    reason: str | None = None


@runtime_checkable
class DecisionBackend(Protocol):
    name: str

    async def decide(self, query: DecisionQuery) -> DecisionResult: ...


class NullDecisionBackend:
    """Declines every decision. The honest default until a vendor API exists."""

    name = "null"

    async def decide(self, query: DecisionQuery) -> DecisionResult:
        return DecisionResult(
            decided=False, backend=self.name, reason="no_backend_configured"
        )


async def build_decision_backend(
    store: Store, tenant_id: TenantId, tool_name: str
) -> DecisionBackend:
    """The backend this tenant chose for this tool, or one that declines.

    Async, unlike `build_embedder`, because the choice is a tenant setting rather
    than process configuration -- and it is read per call rather than cached, so a
    tenant taking a vendor back out takes effect on the next request instead of
    whenever a cache happens to expire.
    """
    record = await store.get_setting(tenant_id, BACKEND_SETTING_KEY) or {}
    chosen = record.get("tool_overrides", {}).get(tool_name) or record.get("default")
    if chosen in (None, "null", "stub"):
        return NullDecisionBackend()
    raise NotImplementedError(
        f"decision backend {chosen!r} is reserved but not implemented; "
        "see docs/ROADMAP.md M11.3. Configure 'null' until a vendor contract exists."
    )
