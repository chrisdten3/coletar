"""M7 — webhooks on the event log.

The first thing in coletar that sends data outbound on its own, so most of these
tests are about what never leaves rather than about delivery working.
"""

from __future__ import annotations

import json

import httpx
import pytest

from coletar.schema.events import Actor, Event, EventType
from coletar.webhooks import (
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
    Dispatcher,
    Endpoint,
    WebhookError,
    backoff_delay,
    load_endpoints,
    payload_for,
    signature,
    verify,
)

SECRET = "whsec-test"
URL = "https://hooks.example.com/coletar"


def an_event(**kwargs) -> Event:
    return Event(
        type=kwargs.pop("type", EventType.OBJECT_CREATED),
        object_id=kwargs.pop("object_id", "mem_abc123"),
        actor=kwargs.pop("actor", Actor.USER),
        before={"content": "Chris works at Acme Corp"},
        after={"content": "Chris works at Globex, badge 44821"},
        **kwargs,
    )


class Receiver:
    """A subscriber, scripted to fail in the ways real endpoints fail."""

    def __init__(self, statuses: list[int] | None = None, raises: int = 0) -> None:
        self.statuses = statuses or [200]
        self.raises = raises
        self.received: list[dict] = []
        self.headers: list[dict] = []
        self.calls = 0

    async def post(self, url: str, *, content: bytes, headers: dict) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.raises:
            raise httpx.ConnectError("connection refused")
        self.received.append(json.loads(content))
        self.headers.append(headers)
        status = self.statuses[min(self.calls - 1, len(self.statuses) - 1)]
        return httpx.Response(status, request=httpx.Request("POST", url))


def dispatcher(receiver: Receiver, endpoints: list[Endpoint] | None = None) -> Dispatcher:
    async def no_sleep(_: float) -> None:
        return None

    return Dispatcher(
        endpoints or [Endpoint(url=URL, secret=SECRET)],
        client=receiver,  # type: ignore[arg-type]
        sleep=no_sleep,
    )


# --- what never leaves ------------------------------------------------------------


def test_the_payload_carries_the_event_never_the_object() -> None:
    """The decision this module is built around.

    coletar holds a graph of things people told an assistant in private. A delivery
    says something changed and names it; a subscriber that needs the text calls back
    through the authenticated API with its own key. So a leaked webhook URL leaks
    metadata about change, not memories.
    """
    payload = payload_for(an_event(), "tenant_alice")

    assert payload["object_id"] == "mem_abc123"
    assert payload["type"] == "object.created"
    assert "content" not in json.dumps(payload)
    assert "Acme" not in json.dumps(payload)
    assert "44821" not in json.dumps(payload)
    assert "before" not in payload and "after" not in payload


@pytest.mark.asyncio
async def test_no_object_content_reaches_the_wire(  # the same claim, end to end
) -> None:
    receiver = Receiver()
    await dispatcher(receiver).dispatch(an_event(), "tenant_alice")
    assert "Acme" not in json.dumps(receiver.received)


def test_a_webhook_cannot_be_added_over_the_wire() -> None:
    """An endpoint a caller can register is an exfiltration primitive wearing a
    feature's clothes, so destinations are operator configuration only."""
    from coletar.mcp import rest

    assert not any("webhook" in path for path, _, _ in rest.routes())


# --- signing ----------------------------------------------------------------------


def test_a_delivery_is_signed_over_body_and_timestamp() -> None:
    """Signing only the body would let anyone who captured one delivery replay it
    forever, so the timestamp is inside the MAC rather than beside it."""
    body = b'{"event_id": "evt_1"}'
    assert verify(body, SECRET, "1700000000", signature(body, SECRET, "1700000000"))
    # Same body, different moment: not the same signature.
    assert not verify(body, SECRET, "1700000001", signature(body, SECRET, "1700000000"))
    assert not verify(body, "wrong-secret", "1700000000", signature(body, SECRET, "1700000000"))


@pytest.mark.asyncio
async def test_the_receiver_gets_what_it_needs_to_verify_and_deduplicate() -> None:
    receiver = Receiver()
    await dispatcher(receiver).dispatch(an_event(), "tenant_alice")
    headers = receiver.headers[0]

    assert verify(
        json.dumps(receiver.received[0], sort_keys=True).encode(),
        SECRET,
        headers["x-coletar-timestamp"],
        headers["x-coletar-signature"],
    )
    # So retries can be made idempotent without guessing.
    assert headers["x-coletar-event-id"] == "evt_" or headers["x-coletar-event-id"]
    assert headers["x-coletar-attempt"] == "1"


# --- the published retry policy ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_until_it_succeeds() -> None:
    receiver = Receiver(statuses=[503, 503, 200])
    result = await dispatcher(receiver).dispatch(an_event(), "tenant_alice")
    assert result[0].delivered
    assert result[0].attempts == 3


@pytest.mark.asyncio
async def test_a_network_error_is_retried_too() -> None:
    receiver = Receiver(raises=2)
    result = await dispatcher(receiver).dispatch(an_event(), "tenant_alice")
    assert result[0].delivered and result[0].attempts == 3


@pytest.mark.asyncio
async def test_a_client_error_is_not_retried() -> None:
    """A 400 is the endpoint saying the request is wrong. Sending it again is noise."""
    receiver = Receiver(statuses=[400])
    result = await dispatcher(receiver).dispatch(an_event(), "tenant_alice")
    assert not result[0].delivered
    assert result[0].attempts == 1


@pytest.mark.asyncio
async def test_429_and_408_are_retried_because_they_might_not_recur() -> None:
    for status in (408, 429):
        receiver = Receiver(statuses=[status, 200])
        result = await dispatcher(receiver).dispatch(an_event(), "tenant_alice")
        assert result[0].delivered, status


@pytest.mark.asyncio
async def test_delivery_gives_up_and_says_so() -> None:
    receiver = Receiver(statuses=[503])
    result = await dispatcher(receiver).dispatch(an_event(), "tenant_alice")
    assert not result[0].delivered
    assert result[0].attempts == MAX_ATTEMPTS
    assert result[0].error == "HTTP 503"


def test_backoff_is_exponential_bounded_and_jittered() -> None:
    """Jitter matters more than the curve: without it every subscriber of a burst
    retries in lockstep, and a brief outage becomes a long one."""
    assert backoff_delay(1, jitter=False) == 1.0
    assert backoff_delay(4, jitter=False) == 8.0
    assert backoff_delay(20, jitter=False) == MAX_DELAY_SECONDS
    samples = {backoff_delay(5) for _ in range(30)}
    assert len(samples) > 1
    assert all(0.0 <= s <= 16.0 for s in samples)


# --- routing and configuration -----------------------------------------------------


@pytest.mark.asyncio
async def test_an_endpoint_only_hears_the_types_it_asked_for() -> None:
    """Most subscribers want writes, not the retrieval trace every search emits."""
    receiver = Receiver()
    endpoints = [Endpoint(url=URL, secret=SECRET, event_types=frozenset({"object.created"}))]
    dispatch = dispatcher(receiver, endpoints)

    assert await dispatch.dispatch(an_event(type=EventType.OBJECT_CREATED), "t") != []
    assert await dispatch.dispatch(an_event(type=EventType.RETRIEVAL_TRACE), "t") == []


def test_configuration_refuses_a_private_or_loopback_target() -> None:
    """A webhook is an operator-supplied URL that *this server* fetches, which is the
    shape of every SSRF — including the one that reaches a cloud metadata service."""
    for url in ("http://localhost:9000/hook", "https://127.0.0.1/hook", "https://169.254.169.254/"):
        with pytest.raises(WebhookError, match="public address"):
            load_endpoints(json.dumps([{"url": url, "secret": SECRET}]))


def test_a_name_that_resolves_privately_is_not_caught_and_that_is_documented() -> None:
    """The limit of the check, pinned so it is never described as stronger.

    Resolving at configuration time reads like a stronger guard than it is: DNS can
    answer differently a second later, so a name that resolves publicly now can point
    somewhere else by delivery time. What actually holds is that nothing over the
    wire can add an endpoint — an operator supplies them.
    """
    raw = json.dumps([{"url": "https://internal.example.com/hook", "secret": SECRET}])
    assert load_endpoints(raw)  # accepted; the hostname is not resolved


def test_configuration_allows_private_targets_only_when_asked() -> None:
    raw = json.dumps([{"url": "http://localhost:9000/hook", "secret": SECRET}])
    assert load_endpoints(raw, allow_private=True)[0].url.endswith("/hook")


def test_configuration_rejects_an_unknown_event_type() -> None:
    raw = json.dumps([{"url": URL, "secret": SECRET, "events": ["object.exploded"]}])
    with pytest.raises(WebhookError, match="unknown event types"):
        load_endpoints(raw)


def test_configuration_requires_a_secret() -> None:
    with pytest.raises(WebhookError, match="needs a url and a secret"):
        load_endpoints(json.dumps([{"url": URL}]))


def test_no_webhooks_configured_is_not_an_error() -> None:
    assert load_endpoints("") == []
    assert load_endpoints("  ") == []
