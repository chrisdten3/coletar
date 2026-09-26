"""Model prices, and where they are stored (SCOPE §6).

Two things are being protected. That a published price is what the provider
published -- a wrong number wearing a currency symbol is worse than no number --
and that a tenant's own rate lives on the server, because the version that lived
in browser storage was per-origin and therefore mostly absent.
"""

from __future__ import annotations

import pytest

from coletar.pricing import (
    AS_OF,
    BY_MODEL,
    DEFAULT_COMPARISON,
    DEFAULT_ROUTING,
    PUBLISHED,
    SETTING_KEY,
    resolve,
)
from coletar.schema.objects import Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT


def test_published_prices_match_their_sources():
    """A canary, not a tautology: these are the figures read off the providers'
    own pricing pages on AS_OF, and an accidental edit to the table should fail
    here rather than quietly restate someone's bill."""
    expected = {
        "claude-opus-5": (5.00, 25.00),
        "claude-opus-5-5": (4.00, 20.00),
        "claude-sonnet-5": (2.00, 10.00),
        "claude-haiku-4-5": (1.00, 5.00),
        "gpt-5.6-sol": (4.00, 20.00),
        "gpt-5.6-terra": (2.00, 12.00),
        "gpt-5.6-luna": (0.20, 1.20),
        "local": (0.0, 0.0),
    }
    assert {p.model for p in PUBLISHED} == set(expected)
    for model, (input_price, output_price) in expected.items():
        price = BY_MODEL[model]
        assert price.input_per_mtok == input_price, model
        assert price.output_per_mtok == output_price, model


def test_every_published_price_carries_its_provenance():
    """AS_OF and a source render beside the total, which is what makes a stale
    figure visibly stale rather than quietly authoritative."""
    assert AS_OF.year == 2026
    for price in PUBLISHED:
        if price.model == "local":
            continue
        assert price.source.startswith("https://"), price.model


def test_a_local_model_is_zero_and_says_why():
    local = BY_MODEL["local"]
    assert local.input_per_mtok == 0.0
    assert "not a claim" in local.note


def test_defaults_resolve_without_any_stored_setting():
    """The whole point of moving this server-side: the view has real prices
    before anyone has typed anything."""
    view = resolve(None)
    assert view.routing == DEFAULT_ROUTING
    assert view.comparison == DEFAULT_COMPARISON
    assert view.rates[str(Provider.CLAUDE)] == BY_MODEL["claude-sonnet-5"].input_per_mtok
    assert view.rates[str(Provider.CHATGPT)] == BY_MODEL["gpt-5.6-terra"].input_per_mtok
    assert view.rates[str(Provider.LOCAL)] == 0.0
    assert view.overrides == {}


def test_the_default_routing_is_mid_range():
    """A default that prices everything at the most expensive model flatters the
    counterfactual; one that picks the cheapest hides the bill."""
    claude = BY_MODEL[DEFAULT_ROUTING[str(Provider.CLAUDE)]]
    family = [p.input_per_mtok for p in PUBLISHED if p.provider is Provider.CLAUDE]
    assert min(family) < claude.input_per_mtok < max(family)


def test_a_negotiated_rate_wins_over_the_published_one():
    view = resolve({"rates": {"claude-sonnet-5": 0.75}})
    assert view.rates[str(Provider.CLAUDE)] == 0.75
    assert view.overrides == {"claude-sonnet-5": 0.75}


def test_routing_changes_which_price_applies():
    view = resolve({"routing": {str(Provider.CLAUDE): "claude-opus-5"}})
    assert view.rates[str(Provider.CLAUDE)] == 5.00


@pytest.mark.parametrize(
    "stored",
    [
        {"routing": {"claude": "a-model-that-does-not-exist"}},
        {"rates": {"not-a-model": 3.0}},
        {"rates": {"claude-sonnet-5": -4.0}},
        {"comparison": "nonsense"},
        {"rates": {"claude-sonnet-5": "free"}},
    ],
)
def test_a_drifted_setting_degrades_to_the_published_price(stored: dict):
    """Read on every render, so a settings row out of step with the catalogue
    must fall back rather than take the page down."""
    view = resolve(stored)
    assert view.rates[str(Provider.CLAUDE)] == BY_MODEL["claude-sonnet-5"].input_per_mtok
    assert view.comparison in BY_MODEL


def test_typing_the_published_price_is_not_an_override():
    """The UI deletes an override equal to the published price, so the rate keeps
    tracking the catalogue the next time it changes. `resolve` has to agree that
    an absent override means "follow the published price"."""
    assert resolve({"rates": {}}).rates[str(Provider.CLAUDE)] == 2.00


# ---------------------------------------------------------------------------
# Where it is stored.
# ---------------------------------------------------------------------------


async def test_a_setting_round_trips_per_tenant():
    store = InMemoryStore()
    assert await store.get_setting(TENANT, SETTING_KEY) is None
    await store.put_setting(TENANT, SETTING_KEY, {"rates": {"claude-sonnet-5": 1.5}})
    assert await store.get_setting(TENANT, SETTING_KEY) == {
        "rates": {"claude-sonnet-5": 1.5}
    }


async def test_a_setting_does_not_cross_a_tenant_boundary():
    store = InMemoryStore()
    from coletar.schema.tenancy import tenant_id

    other = tenant_id("tenant_other")
    await store.put_setting(TENANT, SETTING_KEY, {"rates": {"claude-sonnet-5": 1.0}})
    assert await store.get_setting(other, SETTING_KEY) is None


async def test_a_stored_setting_is_handed_out_as_a_copy():
    """Same discipline as every other read: a caller mutating what it was given
    must not change stored state behind the store's back."""
    store = InMemoryStore()
    await store.put_setting(TENANT, SETTING_KEY, {"rates": {"claude-sonnet-5": 1.0}})
    handed = await store.get_setting(TENANT, SETTING_KEY)
    assert handed is not None
    handed["rates"]["claude-sonnet-5"] = 99.0
    again = await store.get_setting(TENANT, SETTING_KEY)
    assert again == {"rates": {"claude-sonnet-5": 1.0}}


async def test_settings_survive_a_snapshot_round_trip(tmp_path):
    """A preference that did not survive a restart would be indistinguishable
    from the browser storage it replaced."""
    path = tmp_path / "store.json"
    store = InMemoryStore(path)
    await store.put_setting(TENANT, SETTING_KEY, {"routing": {"claude": "claude-opus-5"}})

    reopened = InMemoryStore(path)
    assert await reopened.get_setting(TENANT, SETTING_KEY) == {
        "routing": {"claude": "claude-opus-5"}
    }


async def test_writing_a_setting_appends_no_event():
    """The log is the provenance record for the graph. Filling it with "the user
    changed a price" would make the observability feed mostly about its own
    configuration."""
    store = InMemoryStore()
    before = len(await store.list_events(TENANT, limit=100))
    await store.put_setting(TENANT, SETTING_KEY, {"rates": {"claude-sonnet-5": 1.0}})
    assert len(await store.list_events(TENANT, limit=100)) == before


# ---------------------------------------------------------------------------
# What the endpoint does when the store cannot answer.
#
# The deployment hit this: `tenant_setting` arrived in migration 014 and the
# hosted database had not been migrated, so every read of a tenant's overrides
# raised. A view whose other half is a static published table went down with it,
# and took the whole History page's error state with it.
# ---------------------------------------------------------------------------


@pytest.fixture
def local_app(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import coletar.accounts
    import coletar.store
    from coletar.config import get_settings
    from coletar.inspector.app import app as inspector_app
    from coletar.store import reset_store

    monkeypatch.delenv("COLETAR_PUBLIC_URL", raising=False)
    monkeypatch.setenv("COLETAR_IDENTITY_PROVIDER", "local")
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    monkeypatch.setenv("COLETAR_STORE_PATH", str(tmp_path / "graph.json"))
    get_settings.cache_clear()
    monkeypatch.setattr(coletar.store, "_singleton", InMemoryStore())
    coletar.accounts.reset_directory()
    try:
        # Loopback: the local shell's no-sign-in shortcut is unavailable off it.
        with TestClient(inspector_app, base_url="http://localhost") as client:
            yield client
    finally:
        get_settings.cache_clear()
        reset_store()
        coletar.accounts.reset_directory()


def test_pricing_serves_published_prices_when_overrides_cannot_be_read(
    local_app, monkeypatch
):
    """The published table is static. Losing the stored overrides must cost the
    caller their overrides and not the endpoint."""
    import coletar.store

    async def unreadable(*args, **kwargs):
        raise RuntimeError('relation "tenant_setting" does not exist')

    monkeypatch.setattr(coletar.store._singleton, "get_setting", unreadable)

    response = local_app.get("/web-api/pricing")
    assert response.status_code == 200
    body = response.json()
    # Published prices, and an honest flag saying they are all there is.
    assert body["overrides_available"] is False
    assert body["overrides"] == {}
    assert body["rates"][str(Provider.CLAUDE)] == BY_MODEL[DEFAULT_ROUTING["claude"]].input_per_mtok


def test_a_schema_that_is_behind_reads_as_unavailable_not_as_unset(
    local_app, monkeypatch
):
    """The distinction the typed error exists for. `None` already means "this
    tenant never wrote one", and a caller that cannot tell that apart from "the
    table is not there" shows published defaults as though they were the user's
    own saved settings."""
    import coletar.store
    from coletar.store.base import SchemaBehind

    async def behind(*args, **kwargs):
        raise SchemaBehind("This workspace's settings table has not been created yet.")

    monkeypatch.setattr(coletar.store._singleton, "get_setting", behind)

    body = local_app.get("/web-api/pricing").json()
    assert body["overrides_available"] is False
    # An unset setting is the other case, and it is not this one.
    assert local_app.get("/web-api/pricing").status_code == 200


def test_pricing_says_so_when_the_overrides_are_real(local_app):
    response = local_app.get("/web-api/pricing")
    assert response.status_code == 200
    assert response.json()["overrides_available"] is True


def test_saving_a_rate_onto_a_schema_that_is_behind_refuses_loudly(
    local_app, monkeypatch
):
    """The read degrades; the write must not. Telling someone their negotiated
    rate is stored when it went nowhere is the worse failure of the two."""
    import coletar.store
    from coletar.store.base import SchemaBehind

    async def behind(*args, **kwargs):
        raise SchemaBehind("This workspace's settings table has not been created yet.")

    monkeypatch.setattr(coletar.store._singleton, "put_setting", behind)

    response = local_app.put(
        "/web-api/pricing",
        json={"routing": {"claude": "claude-opus-5"}, "rates": {}},
        headers={"origin": "http://localhost"},
    )
    assert response.status_code == 503
    assert "settings table" in response.json()["detail"]
