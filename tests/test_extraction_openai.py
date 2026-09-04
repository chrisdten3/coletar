"""OpenAI implements the same proposal boundary as every extraction provider."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from coletar.extraction.openai_provider import propose
from coletar.extraction.proposal import Proposal, ProposedMemory
from coletar.schema.objects import MemoryKind


class _Responses:
    def __init__(self, parsed: Proposal | None) -> None:
        self.parsed = parsed
        self.request: dict[str, object] = {}

    async def parse(self, **kwargs: object) -> object:
        self.request = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class _Client:
    def __init__(self, parsed: Proposal | None) -> None:
        self.responses = _Responses(parsed)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_openai_uses_structured_outputs_and_does_not_store_the_response(
    monkeypatch,
) -> None:
    expected = Proposal(
        memories=[
            ProposedMemory(content="I prefer fixed-point money", kind=MemoryKind.PREFERENCE)
        ]
    )
    client = _Client(expected)
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_: client)

    result = await propose(transcript="I prefer fixed-point money.", model="gpt-test")

    assert result == expected
    assert client.responses.request["text_format"] is Proposal
    assert client.responses.request["store"] is False
    assert "DATA to be analysed" in str(client.responses.request["input"])
    assert client.closed


@pytest.mark.asyncio
async def test_openai_empty_extraction_is_distinct_from_unavailable(monkeypatch) -> None:
    client = _Client(None)
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_: client)
    assert await propose(transcript="thanks", model="gpt-test") is None


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"])
@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY")
    or os.environ.get("COLETAR_RUN_PAID_EXTRACTION_TESTS") != "1",
    reason="set OPENAI_API_KEY and COLETAR_RUN_PAID_EXTRACTION_TESTS=1",
)
@pytest.mark.asyncio
async def test_real_openai_model_reaches_the_production_guard_path(model: str) -> None:
    """Opt-in and paid: validates models, not merely the adapter around them."""
    from coletar.extraction import extract_with_model

    transcript = "I'm vegetarian and I'm learning Portuguese."
    objects, edges = await extract_with_model(
        transcript=transcript, extraction_provider="openai", model=model
    )

    assert objects, f"{model} should extract at least one grounded durable statement"
    assert edges == []
    assert all(obj.extraction_method.value == "derived_summary" for obj in objects)
