"""Opt-in hosted propagation smoke against the configured Supabase test tenant.

Run with COLETAR_HOSTED_SMOKE=1 and the ignored deployment/test env loaded. This
creates two clearly named test memories, then retires them with events. It never
hard-deletes graph data and never prints credentials. CI skips it by default.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest

from coletar.accounts.postgres import PostgresDirectory
from coletar.mcp.auth import SCOPE_READ, SCOPE_WRITE
from coletar.retrieval.trace import query_digest
from coletar.schema.events import EventType
from coletar.schema.objects import Locality, LocalityMode, Memory, Provider
from coletar.schema.tenancy import tenant_id
from coletar.store.postgres import PostgresStore


@pytest.mark.asyncio
async def test_hosted_write_propagates_and_locality_is_enforced() -> None:
    if os.environ.get("COLETAR_HOSTED_SMOKE") != "1":
        pytest.skip("Set COLETAR_HOSTED_SMOKE=1 to test the configured hosted tenant")

    required = ("API_URL", "TEST_USER_TENANT", "COLETAR_DATABASE_URL")
    missing = [name for name in required if not os.environ.get(name)]
    assert not missing, f"Missing hosted smoke configuration: {', '.join(missing)}"

    owner = tenant_id(os.environ["TEST_USER_TENANT"])
    directory = PostgresDirectory(os.environ["COLETAR_DATABASE_URL"])
    store = PostgresStore(os.environ["COLETAR_DATABASE_URL"])
    issued = []
    created_ids: list[str] = []
    nonce = uuid.uuid4().hex[:12]
    restricted_nonce = uuid.uuid4().hex[:12]
    query = f"launch validation phrase {nonce}"
    restricted_query = f"private validation phrase {restricted_nonce}"
    started = datetime.now(UTC)

    async def search(
        client: httpx.AsyncClient, secret: str, surface: Provider, text: str
    ) -> dict[str, Any]:
        headers = {"X-API-Key": secret}
        if surface is Provider.CLAUDE:
            headers["Origin"] = "https://claude.ai"
        elif surface is Provider.CHATGPT:
            headers["Origin"] = "https://chatgpt.com"
        response = await client.post(
            "/v1/search",
            json={"query": text, "top_k": 25, "style": "full", "surface": f"smoke-{surface}"},
            headers=headers,
        )
        assert response.status_code == 200, (surface, response.status_code, response.text[:200])
        return cast(dict[str, Any], response.json())

    try:
        account = await directory.account_by_tenant(owner)
        assert account is not None and account.is_active, "Test tenant has no active account"
        for surface in (Provider.CLAUDE, Provider.CHATGPT, Provider.LOCAL):
            key = await directory.issue_key(
                account.id,
                name=f"launch-smoke-{surface}-{nonce}",
                surface=surface,
                scopes=frozenset({SCOPE_READ, SCOPE_WRITE})
                if surface is Provider.CLAUDE
                else frozenset({SCOPE_READ}),
            )
            issued.append(key)

        async with httpx.AsyncClient(
            base_url=os.environ["API_URL"].rstrip("/"), timeout=30, follow_redirects=False
        ) as client:
            for key in issued:
                await search(client, key.secret, key.key.surface, query)

            content = (
                f"My launch validation phrase is {nonce}; "
                "it identifies this cross-surface smoke run."
            )
            write = await client.post(
                "/v1/remember",
                json={"content": content, "surface": "smoke-claude"},
                headers={"X-API-Key": issued[0].secret, "Origin": "https://claude.ai"},
            )
            assert write.status_code == 200, (write.status_code, write.text[:200])
            assert write.json()["created"] is True, {
                "returned_id": write.json().get("object_id"),
                "nonce": nonce,
            }
            shared_id = str(write.json()["object_id"])
            created_ids.append(shared_id)

            stored = await store.get_object(owner, shared_id)
            assert stored is not None
            assert stored.provenance.provider is Provider.CLAUDE
            assert any(
                event.type is EventType.CONNECTOR_WRITE
                for event in await store.list_events(owner, object_id=shared_id, limit=20)
            )

            results: dict[str, str] = {}
            for key in issued:
                surface = key.key.surface
                body = await search(client, key.secret, surface, query)
                assert shared_id in {row["id"] for row in body["results"]}
                assert content in str(body["prompt_block"])
                traces = await store.list_events(owner, limit=100)
                matching = [
                    event
                    for event in traces
                    if event.type is EventType.RETRIEVAL_TRACE
                    and event.at >= started
                    and event.detail.get("query_digest") == query_digest(query)
                    and event.detail.get("principal") == key.key.id
                    and shared_id in event.detail.get("returned_ids", [])
                ]
                assert matching, f"No retrieval trace for {surface}"
                results[str(surface)] = matching[0].id

            restricted = Memory.from_write(
                f"My private validation phrase is {restricted_nonce}; "
                "it is visible only to Claude.",
                locality=Locality(
                    mode=LocalityMode.LOCAL_ONLY,
                    surfaces=frozenset({Provider.CLAUDE}),
                ),
                provider=Provider.CLAUDE,
            )
            await store.put_object(owner, restricted)
            created_ids.append(restricted.id)
            for key in issued:
                body = await search(client, key.secret, key.key.surface, restricted_query)
                ids = {row["id"] for row in body["results"]}
                assert (restricted.id in ids) is (key.key.surface is Provider.CLAUDE)

            print(
                {"write_id": shared_id, "trace_ids": results, "restricted_id": restricted.id}
            )
    finally:
        for object_id in created_ids:
            await store.retire_object(owner, object_id, reason="Hosted launch smoke complete")
        for key in issued:
            await directory.revoke_key(key.key.id)
        await store.close()
        await directory.close()
