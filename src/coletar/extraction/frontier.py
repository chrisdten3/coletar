"""Model-assisted extraction against a frontier model (ROADMAP M6.2).

**Why this exists rather than the local leg alone.** M6.2's premise was that
inference on the user's own machine is free, which made a hosted model look like
spend with no upside. Measurement on 2026-09-02 broke the premise twice: `llama3.1`
does not fit an 8GB M1 at all, and `qwen2.5:0.5b`, which does, scored 59.5%
precision against a 15% false-positive bar and got `kind` wrong on 13 of 22. The
judgement the extractor needs — who said this, who is it about, does it outlive the
conversation — is not reachable by a model that small, and it is not reachable by
regex at all: the pattern path measured 31.4% recall over export prose.

**Cost is an architecture question, not a per-token one.** Backfill is batched and
nobody is waiting, so it belongs on the Batches API at half price; live capture is
one turn with a user watching. Those are different constraints and may end up on
different models. This module is the shared call; the caller decides which.

Two things this module deliberately does not do. It does not cache the system
prompt — at ~180 tokens the prefix is below every model's minimum cacheable length,
and a `cache_control` marker that silently never engages is worse than none. And it
does not trust what comes back: `Proposal` is the narrowest schema that can express
an extraction (see `proposal.py`), and the caller still runs every candidate through
the same grounding and sentence guards the regex path uses.
"""

from __future__ import annotations

import logging

from coletar.extraction.prompt import EXTRACTION_SYSTEM, fenced_transcript
from coletar.extraction.proposal import Proposal
from coletar.extraction.providers import ExtractionConfigurationError, ExtractionUnavailable

logger = logging.getLogger(__name__)


#: Transient failures are worth retrying before giving up on a turn. The SDK
#: default is 2; an import is long enough that a brief overload should not cost a
#: turn, and short enough that we should not retry forever.
MAX_RETRIES = 5

#: Enough for a proposal several times larger than any real turn produces. On
#: Claude Opus 5 thinking is on by default and `max_tokens` caps thinking *and*
#: response together, so a tight budget here truncates mid-object rather than
#: returning a short answer.
MAX_TOKENS = 8_192

#: §7, at the point where it bites hardest. The transcript was written by models
#: and, transitively, by whatever those models read, and it is being handed to
#: another model. The fence makes the instruction/data boundary structural rather
#: than a polite request, and the schema (`proposal.py`) means an injected line has
#: nowhere to put a confidence or a locality even if it is believed.
async def propose(
    *,
    transcript: str,
    model: str | None = None,
    max_tokens: int = MAX_TOKENS,
) -> Proposal | None:
    """Ask Anthropic for a proposal.

    `None` means an examined response was unusable; transport failures raise
    `ExtractionUnavailable`, so a batch caller can retry instead of acknowledging
    the turn as empty.
    """
    from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic
    from pydantic import ValidationError

    from coletar.config import get_settings

    resolved = model or get_settings().anthropic_extraction_model
    try:
        client = AsyncAnthropic(max_retries=MAX_RETRIES)
    except Exception as exc:  # SDK configuration errors do not become empty turns
        raise ExtractionConfigurationError(str(exc)) from exc
    try:
        response = await client.messages.parse(
            model=resolved,
            max_tokens=max_tokens,
            system=EXTRACTION_SYSTEM,
            messages=[
                {"role": "user", "content": fenced_transcript(transcript)}
            ],
            output_format=Proposal,
        )
    except (APIStatusError, APIConnectionError) as exc:
        # Already retried MAX_RETRIES times by the SDK. One compact line, not a
        # traceback: at import scale a stack trace per turn is not a log, it is a
        # denial of service against whoever has to read it.
        raise ExtractionUnavailable(f"{resolved}: {exc.__class__.__name__}") from exc
    except ValidationError:
        # The model answered and the answer did not fit the schema. That is a turn
        # examined and discarded, which is a real result — precision over recall,
        # and an import that finds nothing is recoverable where one that invents
        # is not.
        logger.debug("frontier extraction returned an unusable shape", exc_info=True)
        return None
    finally:
        await client.close()

    parsed = response.parsed_output
    return parsed if isinstance(parsed, Proposal) else None
