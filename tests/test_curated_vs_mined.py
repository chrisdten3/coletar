"""Provider-curated imports are not export parsing, and the graph must say so.

The first real ingest stamped both with `ACCOUNT_EXPORT_PARSE` at 0.60, which made
a memory Claude had already extracted and curated indistinguishable from a regex
fragment lifted out of conversation prose. For a product whose claim is data
provenance, that is the one distinction the Context Inspector has to be able to
draw, so it is pinned here rather than left to the importer's good intentions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coletar.schema.objects import ExtractionMethod, default_confidence


def test_curated_outranks_export_parse_and_sits_under_a_live_write() -> None:
    """The ordering is the point: the judgement was already made by a model with
    the whole conversation in front of it, but it is still an import."""
    curated = default_confidence(ExtractionMethod.PROVIDER_CURATED)
    assert default_confidence(ExtractionMethod.ACCOUNT_EXPORT_PARSE) < curated
    assert curated < default_confidence(ExtractionMethod.MCP_LIVE_WRITE)


def _archive(root: Path) -> Path:
    """A minimal Claude export holding one curated memory and one mined turn."""
    (root / "memories").mkdir()
    (root / "memories" / "m.json").write_text(
        json.dumps(
            {
                "account_uuid": "acct",
                "conversations_memory": "Works at Example Corp on settlements.",
                "project_memories": [],
                "memory_files": [],
            }
        )
    )
    (root / "conversations.json").write_text(
        json.dumps(
            [
                {
                    "uuid": "c1",
                    "name": "chat",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "chat_messages": [
                        {
                            "uuid": "m1",
                            "sender": "human",
                            "text": "I prefer fixed-point arithmetic for money.",
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                    ],
                }
            ]
        )
    )
    return root


@pytest.mark.asyncio
async def test_an_import_records_which_half_each_memory_came_from(tmp_path) -> None:
    from coletar.acquisition.claude_export import import_bundle
    from coletar.schema.tenancy import tenant_id
    from coletar.store.memory import InMemoryStore

    store = InMemoryStore()
    tenant = tenant_id("tenant_curated_test")
    await import_bundle(store, tenant, _archive(tmp_path))

    objects = await store.list_objects(tenant, limit=100)
    methods = {o.extraction_method for o in objects}
    assert ExtractionMethod.PROVIDER_CURATED in methods, (
        "the memories/ file is Claude's own extraction, not prose parsing"
    )

    for obj in objects:
        # Whatever the method, confidence follows from it rather than being a
        # literal the importer happened to type.
        assert obj.confidence == default_confidence(obj.extraction_method)
        if obj.extraction_method is ExtractionMethod.PROVIDER_CURATED:
            assert obj.confidence > default_confidence(ExtractionMethod.ACCOUNT_EXPORT_PARSE)
