"""Who gets asked to sign in, and on which host.

The rule this file pins: **the request host decides, and no environment variable
can overrule it.** Before, a deployment that shipped without
`COLETAR_IDENTITY_PROVIDER` served an open workspace behind a homepage with no
sign-in link on it -- a missing lock that looked like a missing link. The
shortcut is now unavailable off loopback by construction, which is the property
`LocalIdentityProvider` already assumed it had: it trusts whoever can reach the
port, and that is only safe when the port is loopback.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import coletar.store
from coletar.config import get_settings
from coletar.inspector.app import app as inspector_app
from coletar.store import reset_store
from coletar.store.memory import InMemoryStore

#: A host that is not the laptop. Sent as `host` the way a browser would.
REMOTE = "coletar-five.vercel.app"


@pytest.fixture
def app(monkeypatch, tmp_path):
    """No identity provider and no public URL: the misconfiguration this is about."""
    monkeypatch.delenv("COLETAR_IDENTITY_PROVIDER", raising=False)
    monkeypatch.delenv("COLETAR_PUBLIC_URL", raising=False)
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    monkeypatch.setenv("COLETAR_DEFAULT_TENANT_ID", "tenant_test")
    get_settings.cache_clear()
    monkeypatch.setattr(coletar.store, "_singleton", InMemoryStore())
    with TestClient(inspector_app) as client:
        yield client
    get_settings.cache_clear()
    reset_store()


def sign_in_config(client: TestClient, host: str) -> dict:
    response = client.get("/app", headers={"host": host})
    assert response.status_code == 200
    match = re.search(
        r'id="sign-in-config"[^>]*>(.*?)</script>', response.text, re.S
    )
    assert match, "the shell must stamp its sign-in config"
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# The host decides.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["localhost:8795", "127.0.0.1:8000", "app.localhost"])
def test_loopback_keeps_working_with_no_infrastructure(app, host):
    """AGENTS.md: the in-process store must keep working with nothing configured.
    A sign-in wall on a laptop with no provider is a wall with no door in it."""
    assert sign_in_config(app, host)["required"] is False


@pytest.mark.parametrize("host", [REMOTE, "coletar-git-main-x.vercel.app", "example.com"])
def test_any_deployed_host_always_asks_for_a_sign_in(app, host):
    """The whole point. No environment variable is consulted for this -- the
    fixture deliberately sets none, which used to produce a page with no sign-in
    link on it."""
    assert sign_in_config(app, host)["required"] is True


def test_a_proxy_forwarded_host_is_what_counts(app):
    """Behind a platform proxy `host` is an internal address; the public domain
    arrives in `x-forwarded-host`. Reading the wrong one would call a deployment
    loopback because the proxy shares its box."""
    response = app.get(
        "/app", headers={"host": "127.0.0.1:3000", "x-forwarded-host": REMOTE}
    )
    config = json.loads(
        re.search(r'id="sign-in-config"[^>]*>(.*?)</script>', response.text, re.S).group(1)
    )
    assert config["required"] is True


def test_a_forwarded_host_list_uses_the_first_entry(app):
    response = app.get(
        "/app", headers={"host": "127.0.0.1", "x-forwarded-host": f"{REMOTE}, internal"}
    )
    config = json.loads(
        re.search(r'id="sign-in-config"[^>]*>(.*?)</script>', response.text, re.S).group(1)
    )
    assert config["required"] is True


# ---------------------------------------------------------------------------
# And the API agrees with the page.
# ---------------------------------------------------------------------------


def test_a_deployed_host_refuses_the_workspace_rather_than_opening_it(app):
    """`LocalIdentityProvider.verify` returns an identity for any credential,
    including none. Requiring a sign-in in the UI while the API still answered
    anonymously would be security theatre, so the API refuses too -- loudly,
    because a deployment missing this setting should be obviously broken rather
    than quietly open."""
    response = app.get("/web-api/state", headers={"host": REMOTE})
    assert response.status_code == 503
    assert "COLETAR_IDENTITY_PROVIDER" in response.json()["detail"]


def test_loopback_still_serves_the_workspace_anonymously(app):
    response = app.get("/web-api/state", headers={"host": "localhost:8795"})
    assert response.status_code == 200


def test_the_page_and_the_api_never_disagree(app):
    """They were computed from different conditions -- the client from the
    provider name, the server from `public_url` -- so one could gate while the
    other did not."""
    for host, expect_open in ((REMOTE, False), ("localhost", True)):
        required = sign_in_config(app, host)["required"]
        served = app.get("/web-api/state", headers={"host": host}).status_code == 200
        assert served is expect_open
        assert required is not expect_open
