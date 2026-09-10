"""A resumed import must not pay twice for the same turn.

Model extraction over an archive is thousands of paid calls, and an interrupted run
— a credit balance running out, a timeout, a closed laptop — used to restart at turn
one. Deduplication in `remember` stopped the *objects* duplicating, but the money
was spent again on the way there, which is the part that hurts. This pins the fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coletar.schema.tenancy import tenant_id
from coletar.store.memory import InMemoryStore

TENANT = tenant_id("tenant_resume_test")


def _archive(root: Path, turns: int) -> Path:
    root.joinpath("conversations.json").write_text(
        json.dumps(
            [
                {
                    "uuid": "c1",
                    "name": "chat",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "chat_messages": [
                        {
                            "uuid": f"m{i}",
                            "sender": "human",
                            "text": f"I prefer approach number {i}.",
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                        for i in range(turns)
                    ],
                }
            ]
        )
    )
    return root


@pytest.fixture
def model_mode(monkeypatch):  # type: ignore[no-untyped-def]
    from coletar.config import get_settings

    monkeypatch.setenv("COLETAR_EXTRACTION_MODE", "model")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_a_second_run_does_not_re_call_the_model(tmp_path, model_mode, monkeypatch) -> None:
    import coletar.extraction as extraction_pkg
    from coletar.acquisition import claude_export

    seen: list[str] = []

    async def fake(**kwargs: object) -> tuple[list[object], list[object]]:
        seen.append(str(kwargs["transcript"]))
        return [], []

    monkeypatch.setattr(extraction_pkg, "extract_with_model", fake)
    store = InMemoryStore()
    archive = _archive(tmp_path, turns=6)

    first = await claude_export.import_bundle(store, TENANT, archive)
    assert len(seen) == 6
    assert first.resumed == 0

    # The second run reads the checkpoint and calls nothing.
    seen.clear()
    second = await claude_export.import_bundle(store, TENANT, archive)
    assert seen == [], "a resumed import must not re-send turns it already paid for"
    assert second.resumed == 6


@pytest.mark.asyncio
async def test_turns_that_yielded_nothing_are_still_checkpointed(
    tmp_path, model_mode, monkeypatch
) -> None:
    """Most of an archive yields nothing.

    Checkpointing only the productive turns would leave the empty majority to be
    paid for again on every later run, which is most of the bill.
    """
    import coletar.extraction as extraction_pkg
    from coletar.acquisition import claude_export

    async def yields_nothing(**kwargs: object) -> tuple[list[object], list[object]]:
        return [], []

    monkeypatch.setattr(extraction_pkg, "extract_with_model", yields_nothing)
    store = InMemoryStore()
    await claude_export.import_bundle(store, TENANT, _archive(tmp_path, turns=5))

    assert len(await store.extracted_hashes(TENANT)) == 5


@pytest.mark.asyncio
async def test_an_interrupted_run_keeps_the_turns_it_finished(
    tmp_path, model_mode, monkeypatch
) -> None:
    """The case this exists for: credits run out partway through.

    Checkpoints are flushed per conversation, so the work already paid for survives
    the exception that ends the run.
    """
    import coletar.extraction as extraction_pkg
    from coletar.acquisition import claude_export
    from coletar.extraction.providers import ExtractionConfigurationError

    calls = 0

    async def dies_partway(**kwargs: object) -> tuple[list[object], list[object]]:
        nonlocal calls
        calls += 1
        if calls > 4:
            raise ExtractionConfigurationError("gpt: credit_balance_exhausted")
        return [], []

    monkeypatch.setattr(extraction_pkg, "extract_with_model", dies_partway)
    store = InMemoryStore()
    root = tmp_path / "a"
    root.mkdir()
    archive = _archive(root, turns=12)

    with pytest.raises(ExtractionConfigurationError):
        await claude_export.import_bundle(store, TENANT, archive)

    # Some turns were banked before the failure, so a retry is cheaper than a
    # restart even though the run itself failed.
    done = await store.extracted_hashes(TENANT)
    assert 0 < len(done) < 12


@pytest.mark.asyncio
async def test_reprocess_ignores_the_checkpoint(tmp_path, model_mode, monkeypatch) -> None:
    """An escape hatch, because a better prompt is a reason to pay again."""
    import coletar.extraction as extraction_pkg
    from coletar.acquisition import claude_export

    seen: list[str] = []

    async def fake(**kwargs: object) -> tuple[list[object], list[object]]:
        seen.append(str(kwargs["transcript"]))
        return [], []

    monkeypatch.setattr(extraction_pkg, "extract_with_model", fake)
    store = InMemoryStore()
    archive = _archive(tmp_path, turns=3)

    await claude_export.import_bundle(store, TENANT, archive)
    seen.clear()
    await claude_export.import_bundle(store, TENANT, archive, reprocess=True)
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_the_checkpoint_is_per_tenant(tmp_path, model_mode, monkeypatch) -> None:
    """One account's paid work must not silently blank another's import."""
    import coletar.extraction as extraction_pkg
    from coletar.acquisition import claude_export

    seen: list[str] = []

    async def fake(**kwargs: object) -> tuple[list[object], list[object]]:
        seen.append(str(kwargs["transcript"]))
        return [], []

    monkeypatch.setattr(extraction_pkg, "extract_with_model", fake)
    store = InMemoryStore()
    archive = _archive(tmp_path, turns=4)

    await claude_export.import_bundle(store, TENANT, archive)
    seen.clear()
    await claude_export.import_bundle(store, tenant_id("tenant_other"), archive)
    assert len(seen) == 4
    assert await store.extracted_hashes(tenant_id("tenant_other")) != set()
