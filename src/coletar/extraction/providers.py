"""Provider-neutral proposal boundary for model-assisted extraction.

Every backend returns the same deliberately narrow :class:`Proposal`. It may change
which candidates are proposed, but never the graph fields, confidence, provenance,
grounding, or guards applied after the call.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from coletar.extraction.proposal import Proposal

ExtractionProviderName = Literal["ollama", "anthropic", "openai"]


class ExtractionUnavailable(Exception):
    """The provider did not examine the turn; this is not an empty extraction."""


class ExtractionConfigurationError(Exception):
    """The selected provider cannot run with the supplied configuration."""


@runtime_checkable
class ProposalProvider(Protocol):
    async def propose(self, *, transcript: str, model: str) -> Proposal | None: ...


def configured_model(provider: ExtractionProviderName) -> str:
    from coletar.config import get_settings

    settings = get_settings()
    if provider == "anthropic":
        return settings.anthropic_extraction_model
    if provider == "openai":
        return settings.openai_extraction_model
    return settings.ollama_extraction_model


async def propose(
    *,
    transcript: str,
    provider: ExtractionProviderName,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
    keep_alive: str = "5m",
) -> Proposal | None:
    """Dispatch one proposal without changing the provider-independent contract."""
    resolved = model or configured_model(provider)
    if provider == "anthropic":
        from coletar.extraction.frontier import propose as anthropic_propose

        return await anthropic_propose(transcript=transcript, model=resolved)
    if provider == "openai":
        from coletar.extraction.openai_provider import propose as openai_propose

        return await openai_propose(transcript=transcript, model=resolved)

    from coletar.extraction.ollama import propose as ollama_propose

    return await ollama_propose(
        transcript=transcript,
        model=resolved,
        base_url=base_url,
        timeout=timeout,
        keep_alive=keep_alive,
    )
