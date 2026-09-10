"""OpenAI Responses adapter for model-assisted extraction.

Structured outputs, not JSON-in-a-string: `responses.parse` with `text_format`
constrains decoding to `Proposal`'s schema, so the model cannot return a confidence,
a locality or an object id — there is nowhere in the schema to put one. That is
AGENTS.md §7 enforced by the API rather than by a parser downstream of it.

`store=False` on every call. The transcript is the user's own conversation, and this
flow already makes OpenAI a named subprocessor; leaving it in the provider's storage
as well would widen that without saying so.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from coletar.extraction.prompt import EXTRACTION_SYSTEM, fenced_transcript
from coletar.extraction.proposal import Proposal
from coletar.extraction.providers import ExtractionConfigurationError, ExtractionUnavailable

logger = logging.getLogger(__name__)

#: Conditions that will not fix themselves. Retrying these per turn is how an import
#: of 17,881 turns becomes 17,881 doomed API calls that report as "unavailable"
#: instead of stopping, so they are raised as configuration errors: the importers
#: catch `ExtractionUnavailable` and continue, and deliberately do not catch these.
_PERMANENT_STATUS = frozenset({401, 403})
_PERMANENT_CODES = frozenset(
    {"insufficient_quota", "credit_balance_exhausted", "invalid_api_key", "account_deactivated"}
)


def _permanent_reason(exc: object, status: int | None) -> str | None:
    """Whether this failure is worth stopping the whole run for."""
    if status in _PERMANENT_STATUS:
        return f"HTTP {status}"
    body = getattr(exc, "body", None)
    code = body.get("code") if isinstance(body, dict) else None
    if code is None:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
    return str(code) if code in _PERMANENT_CODES else None


async def propose(*, transcript: str, model: str) -> Proposal | None:
    """Return one schema-constrained proposal through the OpenAI Responses API."""
    try:
        from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAIError
    except ImportError as exc:  # pragma: no cover - the package is a required dependency
        raise ExtractionConfigurationError("the openai package is not installed") from exc

    from coletar.config import get_settings

    # Read the key from settings rather than leaving it to the client's environment
    # lookup: pydantic loads `.env` into settings without exporting to `os.environ`,
    # so a key sitting in the project's own `.env` was invisible to the SDK.
    key = get_settings().openai_api_key
    try:
        client = AsyncOpenAI(max_retries=5, api_key=key) if key else AsyncOpenAI(max_retries=5)
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
    except APIStatusError as exc:
        reason = _permanent_reason(exc, getattr(exc, "status_code", None))
        if reason is not None:
            raise ExtractionConfigurationError(
                f"{model}: {reason}. Extraction stopped rather than repeating a "
                f"failing call for every remaining turn."
            ) from exc
        raise ExtractionUnavailable(f"{model}: {exc.__class__.__name__}") from exc
    except APIConnectionError as exc:
        raise ExtractionUnavailable(f"{model}: {exc.__class__.__name__}") from exc
    except ValidationError:
        logger.debug("OpenAI extraction returned an unusable shape", exc_info=True)
        return None
    finally:
        await client.close()

    parsed = response.output_parsed
    return parsed if isinstance(parsed, Proposal) else None
