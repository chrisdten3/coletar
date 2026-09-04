"""The library view and its surface switcher.

The switcher is the demo, so the tests that matter are the ones that would make a
demo a lie: a memory restricted to Claude appearing in ChatGPT's view, or a withheld
count that does not match what was actually withheld. Everything else here is
scaffolding around those two.

The leak test asserts on the object's *content*, not on its id or a CSS class. A
page could drop the row and still leave the sentence in a title attribute, a data
attribute, or a debug comment, and every id-based assertion would pass while the
demo showed the audience a fact it had just promised to withhold.
"""

from __future__ import annotations

import pytest

from coletar.inspector.library import parse_surface, render_library
from coletar.schema.objects import (
    Locality,
    LocalityMode,
    Memory,
    Provider,
)
from coletar.store.memory import InMemoryStore
from conftest import TENANT

CLAUDE_ONLY = Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.CLAUDE}))
LOCAL_ONLY = Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.LOCAL}))

SHARED = "Prefers fixed-point arithmetic for money."
PRIVILEGED = "Handling the Northwind litigation matter."
SALARY = "Salary band for the new hire is 68-74k."


@pytest.fixture
async def store() -> InMemoryStore:
    backing = InMemoryStore()
    await backing.put_object(TENANT, Memory.from_write(SHARED))
    await backing.put_object(TENANT, Memory.from_write(PRIVILEGED, locality=CLAUDE_ONLY))
    await backing.put_object(TENANT, Memory.from_write(SALARY, locality=LOCAL_ONLY))
    return backing


# -- parsing ------------------------------------------------------------------
@pytest.mark.parametrize("raw", [None, "", "owner"])
def test_the_owner_view_is_the_absence_of_a_surface(raw: str | None):
    assert parse_surface(raw) is None


def test_a_known_surface_parses():
    assert parse_surface("claude") is Provider.CLAUDE


def test_an_unknown_surface_is_refused_rather_than_defaulted():
    """Falling back to the owner view would quietly show *more* than was asked for,
    which is the wrong direction to fail on a page about withholding."""
    with pytest.raises(ValueError, match="unknown surface"):
        parse_surface("bogus")


def test_a_provider_with_no_connector_is_not_switchable():
    """`gemini` is a legal Provider and has no confirmed connector. Offering it
    would demo a surface that cannot exist."""
    with pytest.raises(ValueError, match="not a switchable surface"):
        parse_surface("gemini")


# -- what each surface sees ---------------------------------------------------
async def test_the_owner_sees_everything_including_what_is_restricted(store: InMemoryStore):
    html = await render_library(store, TENANT, surface=None)

    assert SHARED in html
    assert PRIVILEGED in html
    assert SALARY in html
    assert "nothing withheld" in html


async def test_a_surface_never_receives_a_memory_restricted_from_it(store: InMemoryStore):
    """The demo's whole claim, asserted on content rather than on markup."""
    html = await render_library(store, TENANT, surface=Provider.CHATGPT)

    assert SHARED in html
    assert PRIVILEGED not in html, "a Claude-only memory reached ChatGPT's view"
    assert SALARY not in html, "a local-only memory reached ChatGPT's view"


async def test_each_surface_sees_its_own_restricted_memory(store: InMemoryStore):
    claude = await render_library(store, TENANT, surface=Provider.CLAUDE)
    local = await render_library(store, TENANT, surface=Provider.LOCAL)

    assert PRIVILEGED in claude and SALARY not in claude
    assert SALARY in local and PRIVILEGED not in local


async def test_the_withheld_count_matches_what_was_withheld(store: InMemoryStore):
    """It is the difference between two store answers, so a wrong number here means
    the store disagreed with itself — not that the template miscounted."""
    chatgpt = await render_library(store, TENANT, surface=Provider.CHATGPT)
    claude = await render_library(store, TENANT, surface=Provider.CLAUDE)

    assert "2 withheld" in chatgpt
    assert "1 withheld" in claude
    assert "1 object withheld from Claude" in claude


async def test_the_withheld_notice_names_the_surface_not_the_enum(store: InMemoryStore):
    html = await render_library(store, TENANT, surface=Provider.LOCAL)
    assert "withheld from the local model" in html


async def test_no_notice_when_a_surface_can_see_everything():
    empty = InMemoryStore()
    await empty.put_object(TENANT, Memory.from_write(SHARED))

    html = await render_library(empty, TENANT, surface=Provider.CHATGPT)
    assert "withheld from" not in html
    assert "0 withheld" in html


async def test_an_empty_graph_says_how_to_fill_it():
    html = await render_library(InMemoryStore(), TENANT, surface=None)
    assert "demo-seed" in html and "import-claude" in html


async def test_restriction_is_marked_only_in_the_owners_view(store: InMemoryStore):
    """A surface cannot be shown a restricted object at all, so a `restricted` row
    while viewing as one would mean the store had handed over something it should
    not have."""
    owner = await render_library(store, TENANT, surface=None)
    claude = await render_library(store, TENANT, surface=Provider.CLAUDE)

    assert "row restricted" in owner
    assert "row restricted" not in claude


async def test_content_is_escaped(store: InMemoryStore):
    await store.put_object(TENANT, Memory.from_write("<script>alert(1)</script> is not markup"))
    html = await render_library(store, TENANT, surface=None)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# -- the route ----------------------------------------------------------------
def _get(path: str) -> str:
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app

    response = TestClient(app).get(path)
    assert response.status_code == 200
    return response.text


@pytest.fixture
def live(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from coletar.config import get_settings
    from coletar.store import build_store, reset_store

    monkeypatch.setenv("COLETAR_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    monkeypatch.setenv("COLETAR_DEFAULT_TENANT_ID", str(TENANT))
    get_settings.cache_clear()
    reset_store()
    yield build_store()
    get_settings.cache_clear()
    reset_store()


async def test_the_route_filters_by_the_requested_surface(live):  # type: ignore[no-untyped-def]
    await live.put_object(TENANT, Memory.from_write(SHARED))
    await live.put_object(TENANT, Memory.from_write(PRIVILEGED, locality=CLAUDE_ONLY))

    assert PRIVILEGED in _get("/")
    assert PRIVILEGED in _get("/?surface=claude")
    assert PRIVILEGED not in _get("/?surface=chatgpt")


async def test_a_bad_surface_shows_an_error_and_the_owner_view(live):  # type: ignore[no-untyped-def]
    await live.put_object(TENANT, Memory.from_write(SHARED))
    html = _get("/?surface=bogus")

    assert "unknown surface" in html
    assert SHARED in html, "the page still has to render something"


async def test_the_review_gate_moved_off_the_library(live):  # type: ignore[no-untyped-def]
    await live.put_object(TENANT, Memory.from_write(SHARED))
    assert "Canonical Context Graph" in _get("/review")
