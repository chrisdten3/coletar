"""The one place coletar talks to Jev, TypeSafe's decision model.

Jev answers typed questions about a piece of state: a `noul` is a yes/no with a
probability, a `choice` picks one label from a set we define and reports a
confidence. That is the shape of two pipeline stages — "is this turn worth
remembering" and "how does this candidate relate to that memory" — and nothing
else, which is why this module does not try to be a general client.

Everything about the wire format lives here and only here. The request and response
shapes follow TypeSafe's own Python SDK (`typesafe-sdk` 0.7.2, `POST /v1/systemone`);
it is not a dependency because it pulls in a second HTTP stack for one POST we can
make with the `httpx` already installed. If the endpoint changes shape, this file is
the edit.

Failures split the same way extraction's do (`providers.py`): a transient one
(timeout, 429, 5xx) raises `JevUnavailable`, which a caller may queue and retry; a
permanent one (no key, rejected key, a request or response that does not fit the
schema) raises `JevConfigurationError`, which must stop a run rather than be retried
per turn. `evaluate` makes one attempt. Retry policy belongs to the caller, because
the live gate and a backfill want different ones.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from coletar.config import get_settings

SYSTEM_ONE_PATH = "/v1/systemone"
REQUEST_ID_HEADER = "x-typesafe-request-id"


class JevUnavailable(Exception):
    """Jev could not answer this time. Retrying later is reasonable."""


class JevConfigurationError(Exception):
    """Jev will not answer until something is changed. Retrying is not."""


# -- questions ---------------------------------------------------------------------


class NoulCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    true: str | None = None
    false: str | None = None


class Noul(BaseModel):
    """A yes/no question. The answer is the probability of yes."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["noul"] = "noul"
    instructions: str | None = None
    criteria: NoulCriteria | None = None


class Choice(BaseModel):
    """Pick one label. `criteria` maps each label to what it means."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["choice"] = "choice"
    instructions: str | None = None
    criteria: dict[str, str]


Question = Noul | Choice


# -- answers -----------------------------------------------------------------------


class NoulAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    type: Literal["noul"]
    noul: float


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    type: Literal["choice"]
    choice: str
    confidence: float
    probabilities: dict[str, float]


Answer = Annotated[NoulAnswer | ChoiceAnswer, Field(discriminator="type")]
_ANSWER: TypeAdapter[NoulAnswer | ChoiceAnswer] = TypeAdapter(Answer)


class Usage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None


class Evaluation(BaseModel):
    """One response, plus what an evaluation run needs to account for it."""

    model_config = ConfigDict(frozen=True)

    model: str
    usage: Usage
    answers: dict[str, NoulAnswer | ChoiceAnswer]
    latency_ms: float
    request_id: str | None = None

    def noul(self, name: str) -> float:
        answer = self.answers.get(name)
        if not isinstance(answer, NoulAnswer):
            raise JevConfigurationError(f"no noul answer named {name!r}")
        return answer.noul

    def choice(self, name: str) -> ChoiceAnswer:
        answer = self.answers.get(name)
        if not isinstance(answer, ChoiceAnswer):
            raise JevConfigurationError(f"no choice answer named {name!r}")
        return answer


def _question_body(question: Question) -> dict[str, Any]:
    # Unset optionals are left off the wire rather than sent as null, as the SDK does.
    return question.model_dump(exclude_none=True)


def _parse(response: httpx.Response, latency_ms: float, asked: set[str]) -> Evaluation:
    try:
        decoded = response.json()
    except ValueError as exc:
        raise JevConfigurationError(f"response was not JSON ({response.status_code})") from exc
    if not isinstance(decoded, dict):
        raise JevConfigurationError("response was not a JSON object")
    raw_answers = decoded.get("answers")
    if not isinstance(raw_answers, dict):
        raise JevConfigurationError("response has no answers object")

    answers: dict[str, NoulAnswer | ChoiceAnswer] = {}
    for name, raw in raw_answers.items():
        # An answer type this module does not model is skipped, not fatal: the
        # endpoint is young and may add kinds we never ask for.
        if not isinstance(raw, dict) or raw.get("type") not in {"noul", "choice"}:
            continue
        try:
            answers[name] = _ANSWER.validate_python(raw)
        except ValidationError as exc:
            raise JevConfigurationError(f"answer {name!r} does not fit its schema") from exc

    missing = asked - answers.keys()
    if missing:
        # A question asked and not answered is a contract change, not a bad turn.
        raise JevConfigurationError(f"unanswered questions: {sorted(missing)}")

    try:
        usage = Usage.model_validate(decoded.get("usage") or {})
    except ValidationError as exc:
        raise JevConfigurationError("usage does not fit its schema") from exc
    return Evaluation(
        model=str(decoded.get("model", "")),
        usage=usage,
        answers=answers,
        latency_ms=latency_ms,
        request_id=response.headers.get(REQUEST_ID_HEADER),
    )


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    detail = response.text[:200]
    if status == 429 or status >= 500:
        raise JevUnavailable(f"{status}: {detail}")
    # 400/401/403/404/422: the key, the model name or the request shape is wrong,
    # and every following request will fail identically.
    raise JevConfigurationError(f"{status}: {detail}")


async def evaluate(
    state: str | dict[str, Any],
    questions: dict[str, Question],
    *,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> Evaluation:
    """Ask Jev `questions` about `state`. One attempt; see the module docstring.

    `state` is data, never instructions: it is sent in its own field, apart from the
    questions, so text inside a turn has no channel to redefine what is being asked.
    """
    settings = get_settings()
    if not settings.jev_api_key:
        raise JevConfigurationError("no Jev API key: set COLETAR_JEV_API_KEY or TYPESAFE_API_KEY")
    if not questions:
        raise JevConfigurationError("evaluate needs at least one question")

    body = {
        "state": state,
        "model": model or settings.jev_model,
        "questions": {name: _question_body(q) for name, q in questions.items()},
    }
    headers = {"Authorization": f"Bearer {settings.jev_api_key}"}
    url = settings.jev_base_url.rstrip("/") + SYSTEM_ONE_PATH

    owned = client is None
    http = client or httpx.AsyncClient(timeout=settings.jev_timeout_seconds)
    started = time.perf_counter()
    try:
        response = await http.post(url, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise JevUnavailable(f"timeout after {settings.jev_timeout_seconds}s") from exc
    except httpx.TransportError as exc:
        raise JevUnavailable(f"{exc.__class__.__name__}") from exc
    finally:
        if owned:
            await http.aclose()
    latency_ms = (time.perf_counter() - started) * 1000

    _raise_for_status(response)
    return _parse(response, latency_ms, set(questions))
