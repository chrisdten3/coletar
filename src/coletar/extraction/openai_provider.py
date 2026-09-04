"""OpenAI Responses adapter for model-assisted extraction."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from coletar.extraction.prompt import EXTRACTION_SYSTEM, fenced_transcript
from coletar.extraction.proposal import Proposal
from coletar.extraction.providers import ExtractionConfigurationError, ExtractionUnavailable

logger = logging.getLogger(__name__)


async def propose(*, transcript: str, model: str) -> Proposal | None:
    """Return one schema-constrained proposal through the OpenAI Responses API."""
    try:
        from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAIError
    except ImportError as exc:  # pragma: no cover - the package is a required dependency
        raise ExtractionConfigurationError("the openai package is not installed") from exc

    try:
        client = AsyncOpenAI(max_retries=5)
    except OpenAIError as exc:
        raise ExtractionConfigurationError(str(exc)) from exc

    try:
        response = await client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": fenced_transcript(transcript)},
            ],
            text_format=Proposal,
            store=False,
        )
    except (APIStatusError, APIConnectionError) as exc:
        raise ExtractionUnavailable(f"{model}: {exc.__class__.__name__}") from exc
    except ValidationError:
        logger.debug("OpenAI extraction returned an unusable shape", exc_info=True)
        return None
    finally:
        await client.close()

    parsed = response.output_parsed
    return parsed if isinstance(parsed, Proposal) else None
