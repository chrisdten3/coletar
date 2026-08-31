"""Webhooks on the event log (SCOPE §9, ROADMAP M7).

This is the first thing in coletar that sends data *outbound* on its own, which makes
the interesting decisions the ones about what never leaves.

**The payload carries the event, not the object.** A delivery says "an object of this
type changed, here is its id, at this time, by this actor" — never the content,
never `before`/`after`. A subscriber that needs the text calls back through the
authenticated API with its own key. So a webhook URL that leaks, or an endpoint that
is quietly compromised, leaks *metadata about change* rather than the user's
memories. The whole product is a graph of things people told an assistant in private;
posting that to an arbitrary URL because a config line said so is not a trade worth
making for convenience.

**Delivery writes no events.** A failed delivery is not a mutation of the graph, so
constraint 5 does not ask for one — and that is fortunate, because an event per
delivery would be an event that triggers a delivery, which is a loop. Delivery
outcomes live in a bounded in-process log instead.

**Destinations are operator configuration, not an API.** Nothing over the wire can
add a webhook, because an endpoint that can be added by a caller is an exfiltration
primitive wearing a feature's clothes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from coletar.schema.events import Event, EventType

#: Published retry policy. Documented in docs/CONNECTORS.md; change both together.
MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 60.0
DELIVERY_TIMEOUT = 10.0

#: Bounded so a long-running server does not accumulate delivery history forever.
DELIVERY_LOG_SIZE = 200

#: Signature version, so a receiver can tell what it is verifying and we can change
#: the scheme later without every subscriber silently accepting both.
SIGNATURE_VERSION = "v1"

#: A retry only helps if the failure might not recur. A 4xx that is not 408 or 429 is
#: the endpoint saying "this request is wrong", and sending it again is noise.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class WebhookError(Exception):
    """A misconfiguration, phrased for the operator who wrote the config."""


@dataclass(frozen=True)
class Endpoint:
    url: str
    secret: str
    #: Empty means every type. Named types are the common case: most subscribers want
    #: writes, not the retrieval traces every search emits.
    event_types: frozenset[str] = frozenset()

    def wants(self, event: Event) -> bool:
        return not self.event_types or str(event.type) in self.event_types


@dataclass
class Delivery:
    url: str
    event_id: str
    event_type: str
    attempts: int
    delivered: bool
    status: int | None
    error: str | None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


def signature(body: bytes, secret: str, timestamp: str) -> str:
    """HMAC over timestamp and body, so a receiver can verify origin *and* freshness.

    The timestamp is inside the MAC rather than beside it — signing only the body
    would let anyone who captured one delivery replay it forever.
    """
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={mac.hexdigest()}"


def verify(body: bytes, secret: str, timestamp: str, provided: str) -> bool:
    """Offered so a subscriber can verify with our implementation rather than
    reimplementing the scheme and getting the timestamp binding wrong."""
    return hmac.compare_digest(signature(body, secret, timestamp), provided)


def payload_for(event: Event, tenant_id: str) -> dict[str, Any]:
    """Metadata only. See the module docstring for why `before`/`after` and `content`
    are absent — this is the boundary, not an oversight to be filled in later."""
    return {
        "event_id": event.id,
        "type": str(event.type),
        "actor": str(event.actor),
        "provider": str(event.provider),
        "object_id": event.object_id,
        "tenant_id": tenant_id,
        "at": event.at.isoformat(),
        "is_revision": event.is_revision,
    }


def backoff_delay(attempt: int, *, jitter: bool = True) -> float:
    """Exponential with full jitter.

    Jitter matters more than the curve: without it every subscriber of a burst
    retries in lockstep and the second wave arrives as a thundering herd, which is
    how a brief outage becomes a long one.
    """
    ceiling = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
    return random.uniform(0.0, ceiling) if jitter else ceiling


#: Hostnames that mean "this machine" without needing DNS to say so.
_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def _is_public(url: str, *, allow_private: bool) -> bool:
    """Refuse the targets that are obviously the machine coletar runs on.

    Deliberately does **not** resolve the hostname. Resolving at configuration time
    reads like a stronger check than it is: DNS can answer differently a second
    later, so a name that resolves publicly now can point at the metadata service by
    the time a delivery is made. It also fails valid endpoints whenever DNS blips at
    startup, which trades a real outage for imagined safety.

    So this catches literals and the obvious names, and the residual is stated rather
    than papered over: **a hostname you control can still resolve to a private
    address.** The operator supplies these URLs, which is the boundary that actually
    holds — nothing over the wire can add one.
    """
    parsed = urlparse(url)
    if allow_private:
        return bool(parsed.hostname)
    if parsed.scheme != "https":
        return False
    host = parsed.hostname
    if host is None:
        return False
    if host.lower() in _LOCAL_HOSTS or host.lower().endswith((".local", ".localhost")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name, not a literal. See the docstring for what this does not promise.
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    )


def load_endpoints(raw: str, *, allow_private: bool = False) -> list[Endpoint]:
    """`[{"url": "...", "secret": "...", "events": ["object.created"]}]`"""
    if not raw.strip():
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebhookError(f"COLETAR_WEBHOOKS is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise WebhookError("COLETAR_WEBHOOKS should be a list of {url, secret, events?}")

    endpoints: list[Endpoint] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("url") or not entry.get("secret"):
            raise WebhookError("each webhook needs a url and a secret")
        url = str(entry["url"])
        if not _is_public(url, allow_private=allow_private):
            raise WebhookError(
                f"{url} is not an https URL resolving to a public address; a webhook "
                "is a URL this server fetches, so private and loopback targets are "
                "refused (set COLETAR_WEBHOOKS_ALLOW_PRIVATE=true for local testing)"
            )
        unknown = {str(e) for e in entry.get("events") or []} - {str(t) for t in EventType}
        if unknown:
            raise WebhookError(f"unknown event types: {', '.join(sorted(unknown))}")
        endpoints.append(
            Endpoint(
                url=url,
                secret=str(entry["secret"]),
                event_types=frozenset(str(e) for e in entry.get("events") or []),
            )
        )
    return endpoints


class Dispatcher:
    """Delivers events to endpoints, off the write path.

    Delivery is fire-and-forget from the caller's side on purpose: a write must not
    fail, or slow down, because someone else's server is down. That is the same rule
    M4 applied to extraction, for the same reason.
    """

    def __init__(
        self,
        endpoints: list[Endpoint],
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.endpoints = endpoints
        self._client = client
        self._sleep = sleep
        self.log: deque[Delivery] = deque(maxlen=DELIVERY_LOG_SIZE)

    async def _post(self, url: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, content=body, headers=headers)
        async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as client:
            return await client.post(url, content=body, headers=headers)

    async def deliver(self, endpoint: Endpoint, event: Event, tenant_id: str) -> Delivery:
        body = json.dumps(payload_for(event, tenant_id), sort_keys=True).encode()
        last_error: str | None = None
        status: int | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            timestamp = str(int(datetime.now(UTC).timestamp()))
            headers = {
                "content-type": "application/json",
                "x-coletar-timestamp": timestamp,
                "x-coletar-signature": signature(body, endpoint.secret, timestamp),
                # So a receiver can make retries idempotent without guessing.
                "x-coletar-event-id": event.id,
                "x-coletar-attempt": str(attempt),
            }
            try:
                response = await self._post(endpoint.url, body, headers)
                status = response.status_code
                if 200 <= status < 300:
                    return self._record(endpoint, event, attempt, True, status, None)
                if status not in _RETRYABLE_STATUS:
                    # The endpoint says the request is wrong. Sending it again is noise.
                    return self._record(
                        endpoint, event, attempt, False, status, f"HTTP {status}"
                    )
                last_error = f"HTTP {status}"
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < MAX_ATTEMPTS:
                await self._sleep(backoff_delay(attempt))

        return self._record(endpoint, event, MAX_ATTEMPTS, False, status, last_error)

    def _record(
        self,
        endpoint: Endpoint,
        event: Event,
        attempts: int,
        delivered: bool,
        status: int | None,
        error: str | None,
    ) -> Delivery:
        delivery = Delivery(
            url=endpoint.url,
            event_id=event.id,
            event_type=str(event.type),
            attempts=attempts,
            delivered=delivered,
            status=status,
            error=error,
        )
        self.log.append(delivery)
        return delivery

    async def dispatch(self, event: Event, tenant_id: str) -> list[Delivery]:
        targets = [e for e in self.endpoints if e.wants(event)]
        if not targets:
            return []
        return list(
            await asyncio.gather(*(self.deliver(e, event, tenant_id) for e in targets))
        )
