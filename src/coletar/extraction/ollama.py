"""Local Ollama adapter for the provider-neutral extraction proposal boundary."""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from coletar.extraction.prompt import EXTRACTION_SYSTEM, fenced_transcript
from coletar.extraction.proposal import Proposal
from coletar.extraction.providers import ExtractionUnavailable


async def propose(
    *,
    transcript: str,
    model: str,
    base_url: str | None = None,
    timeout: float = 120.0,
    keep_alive: str = "5m",
) -> Proposal | None:
    """Ask the user's local model; malformed output is safely an empty result."""
    from coletar.config import get_settings

    endpoint = (base_url or get_settings().upstream_base_url).rstrip("/").removesuffix("/v1")
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                f"{endpoint}/api/chat",
                json={
                    "model": model,
                    "format": Proposal.model_json_schema(),
                    "stream": False,
                    "options": {"temperature": 0.0},
                    "keep_alive": keep_alive,
                    "messages": [
                        {"role": "system", "content": EXTRACTION_SYSTEM},
                        {"role": "user", "content": fenced_transcript(transcript)},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # The turn was not successfully examined. Batch callers must retain it
            # for retry rather than recording a false "found nothing" completion.
            raise ExtractionUnavailable(f"{model}: {exc.__class__.__name__}") from exc

    try:
        return Proposal.model_validate_json(payload["message"]["content"])
    except (KeyError, TypeError, ValidationError):
        return None
