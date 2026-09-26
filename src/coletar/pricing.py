"""What a model costs, per million tokens (SCOPE §6).

The Cost view multiplies measured tokens by a rate. Until now that rate lived in
browser `localStorage`, which had two problems worth separating. The small one:
prefs are per-origin, so the same workspace served on two ports showed prices on
one and an empty state on the other. The real one: a number a user has to type in
before the feature works is a number most users never type in, and a cost view
that is blank by default is a cost view nobody sees.

So the defaults here are real published prices, and they are **dated and
attributed**. That is the trade this module makes and it is worth stating: a
hardcoded price table is wrong the moment a provider changes one, and a wrong
number wearing a currency symbol is worse than no number. What makes it
defensible is that `AS_OF` and `SOURCES` ship with the table and render beside
the total, so a stale figure is visibly stale rather than quietly authoritative.

Three rules follow from that:

  * **Input prices only.** coleta measures *context served* -- tokens it handed
    to a model, which are that model's input. Output tokens are the model's
    reply, which coleta never sees and must not guess at. Pricing them would be
    inventing the larger half of a bill.
  * **A local model is zero, and says so.** Running one is not free; it is not
    billed per token, and a rate card is the wrong instrument for it.
  * **The tenant's own rate always wins.** Negotiated pricing, a different
    provider, a currency -- these are the user's facts about their own account,
    stored per tenant, and the published default is only what they see first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from coletar.schema.objects import Provider

#: When the published prices below were last checked against the sources. Shown
#: in the UI next to any total, because the honest claim is "this is what it
#: cost on this date", not "this is what it costs".
AS_OF = date(2026, 9, 26)

SOURCES: dict[str, str] = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing",
}


@dataclass(frozen=True)
class ModelPrice:
    """One model's published input price.

    `provider` is what maps a price to measured traffic: retrieval traces record
    which assistant asked, not which model answered, so a workspace's Claude
    tokens are priced at whichever Claude model the user says they route to.
    """

    model: str
    label: str
    provider: Provider
    #: USD per million input tokens.
    input_per_mtok: float
    #: USD per million output tokens. Recorded for completeness and deliberately
    #: not used by the Cost view -- see the module docstring.
    output_per_mtok: float
    source: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "label": self.label,
            "provider": str(self.provider),
            "input_per_mtok": self.input_per_mtok,
            "output_per_mtok": self.output_per_mtok,
            "source": self.source,
            "note": self.note,
        }


#: Published list prices as of `AS_OF`. Ordered most to least expensive within a
#: provider, because the counterfactual row ("all of it at X") reads better
#: against the top of a range.
PUBLISHED: tuple[ModelPrice, ...] = (
    ModelPrice(
        model="claude-opus-5",
        label="Claude Opus 5",
        provider=Provider.CLAUDE,
        input_per_mtok=5.00,
        output_per_mtok=25.00,
        source=SOURCES["anthropic"],
    ),
    ModelPrice(
        model="claude-opus-5-5",
        label="Claude Opus 5.5",
        provider=Provider.CLAUDE,
        input_per_mtok=4.00,
        output_per_mtok=20.00,
        source=SOURCES["anthropic"],
    ),
    ModelPrice(
        model="claude-sonnet-5",
        label="Claude Sonnet 5",
        provider=Provider.CLAUDE,
        input_per_mtok=2.00,
        output_per_mtok=10.00,
        source=SOURCES["anthropic"],
        note="Introductory $2/$10 became the standard price on 1 September 2026.",
    ),
    ModelPrice(
        model="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        provider=Provider.CLAUDE,
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        source=SOURCES["anthropic"],
    ),
    ModelPrice(
        model="gpt-5.6-sol",
        label="GPT-5.6 Sol",
        provider=Provider.CHATGPT,
        input_per_mtok=4.00,
        output_per_mtok=20.00,
        source=SOURCES["openai"],
        note="Promotional pricing, published as holding at least to 21 November 2026.",
    ),
    ModelPrice(
        model="gpt-5.6-terra",
        label="GPT-5.6 Terra",
        provider=Provider.CHATGPT,
        input_per_mtok=2.00,
        output_per_mtok=12.00,
        source=SOURCES["openai"],
    ),
    ModelPrice(
        model="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        provider=Provider.CHATGPT,
        input_per_mtok=0.20,
        output_per_mtok=1.20,
        source=SOURCES["openai"],
    ),
    ModelPrice(
        model="local",
        label="Local model",
        provider=Provider.LOCAL,
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        source="",
        note="Not billed per token. Zero here is a modelling choice, not a claim "
        "that running a model on your own hardware is free.",
    ),
)

BY_MODEL: dict[str, ModelPrice] = {price.model: price for price in PUBLISHED}

#: Which model a provider's measured traffic is priced at until the tenant says
#: otherwise. The mid-range option in each family, because a default that picks
#: the most expensive model flatters the counterfactual and one that picks the
#: cheapest hides the bill.
DEFAULT_ROUTING: dict[str, str] = {
    str(Provider.CLAUDE): "claude-sonnet-5",
    str(Provider.CHATGPT): "gpt-5.6-terra",
    str(Provider.LOCAL): "local",
    str(Provider.COLETAR): "local",
    str(Provider.GEMINI): "gpt-5.6-terra",
}

#: The model the "all of it at ..." row prices against.
DEFAULT_COMPARISON = "claude-opus-5"

#: Settings key holding one tenant's overrides.
SETTING_KEY = "pricing"


@dataclass(frozen=True)
class PricingView:
    """What the Cost view needs: a catalogue, a routing, and their provenance."""

    routing: dict[str, str]
    comparison: str
    rates: dict[str, float]
    overrides: dict[str, float]
    as_of: date

    def as_dict(self) -> dict[str, Any]:
        return {
            "catalogue": [p.as_dict() for p in PUBLISHED],
            "routing": self.routing,
            "comparison": self.comparison,
            # Resolved USD per million input tokens, per provider. The client
            # multiplies; it never has to know how the rate was arrived at.
            "rates": self.rates,
            "overrides": self.overrides,
            "as_of": self.as_of.isoformat(),
            "sources": SOURCES,
        }


def resolve(stored: dict[str, Any] | None) -> PricingView:
    """Merge a tenant's stored overrides over the published defaults.

    Unknown models and negative rates are dropped rather than raising: this is
    read on every Cost view render, and a settings row that has drifted out of
    step with the catalogue should degrade to the published price rather than
    take the page down.
    """
    stored = stored or {}

    routing = dict(DEFAULT_ROUTING)
    for provider, model in (stored.get("routing") or {}).items():
        if isinstance(model, str) and model in BY_MODEL:
            routing[str(provider)] = model

    comparison = stored.get("comparison")
    if not isinstance(comparison, str) or comparison not in BY_MODEL:
        comparison = DEFAULT_COMPARISON

    overrides: dict[str, float] = {}
    for model, rate in (stored.get("rates") or {}).items():
        if model in BY_MODEL and isinstance(rate, int | float) and rate >= 0:
            overrides[str(model)] = float(rate)

    rates = {
        provider: overrides.get(model, BY_MODEL[model].input_per_mtok)
        for provider, model in routing.items()
    }
    return PricingView(
        routing=routing,
        comparison=comparison,
        rates=rates,
        overrides=overrides,
        as_of=AS_OF,
    )


def rate_for_model(model: str, overrides: dict[str, float]) -> float:
    if model in overrides:
        return overrides[model]
    price = BY_MODEL.get(model)
    return price.input_per_mtok if price else 0.0
