"""Async client for the coletar REST API (ROADMAP M7)."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

DEFAULT_TIMEOUT = 30.0


class ColetarError(Exception):
    """Base for every failure the API reports, so a caller can catch one thing."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Unauthorized(ColetarError):
    """The key is missing, wrong, or lacks the scope for this call."""


class NotFound(ColetarError):
    """No such object — or it belongs to another tenant, or is local to another
    surface. The three are deliberately indistinguishable."""


class RateLimited(ColetarError):
    """Too many requests for this credential. `retry_after` is in seconds."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, status=429)
        self.retry_after = retry_after


class Coletar:
    """```python
    async with Coletar("https://coletar.example", api_key="sk-...") as client:
        await client.remember("I prefer fixed-point integers for money")
        hits = await client.search("how should I represent money", explain=True)
    ```

    The tenant is not a parameter. It comes from the key, server-side, which is what
    stops a client naming someone else's graph.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ColetarError("an api_key is required; this server has no anonymous mode")
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def __aenter__(self) -> Coletar:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(
            method, f"{self.base_url}{path}", headers=self._headers, **kwargs
        )
        if response.status_code == 429:
            raise RateLimited(
                "rate limited for this key",
                retry_after=int(response.headers.get("retry-after", "1")),
            )
        if response.status_code in (401, 403):
            raise Unauthorized(_message(response), status=response.status_code)
        if response.status_code == 404:
            raise NotFound(_message(response), status=404)
        if response.status_code >= 400:
            raise ColetarError(_message(response), status=response.status_code)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"result": payload}

    # --- read ---------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        top_k: int = 12,
        explain: bool = False,
    ) -> list[dict[str, Any]]:
        """Hybrid retrieval over this key's graph.

        `explain=True` returns the component scores and the component versions behind
        each hit. A ranked list you cannot interrogate is a list you have to trust,
        and §5.1 is explicit that a measured number must stay attributable to the
        formula that produced it.
        """
        payload = await self._call(
            "POST",
            "/v1/search",
            json={
                "query": query,
                "project_id": project_id,
                "top_k": top_k,
                "explain": explain,
            },
        )
        results = payload.get("results", [])
        return list(results) if isinstance(results, list) else []

    async def inspect(self, object_id: str) -> dict[str, Any]:
        """One object exactly as the graph holds it, provenance included."""
        payload = await self._call("GET", f"/v1/objects/{object_id}")
        return dict(payload.get("object") or {})

    async def history(self, object_id: str) -> list[dict[str, Any]]:
        """What this object used to say, and when it changed (constraint 6)."""
        payload = await self._call("GET", f"/v1/objects/{object_id}/history")
        revisions = payload.get("revisions", [])
        return list(revisions) if isinstance(revisions, list) else []

    # --- write --------------------------------------------------------------------

    async def remember(
        self, content: str, *, kind: str = "fact", project_id: str | None = None
    ) -> dict[str, Any]:
        """Write a memory through the ingest boundary.

        A restatement of something already held corroborates it rather than creating
        a duplicate, and the response reports what was *stored* rather than what was
        asked for — those differ exactly when a corroboration folds.
        """
        return await self._call(
            "POST",
            "/v1/remember",
            json={"content": content, "kind": kind, "project_id": project_id},
        )

    async def supersede(
        self,
        object_id: str,
        content: str,
        *,
        kind: str = "fact",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Correct a fact by writing its replacement.

        The old object is not edited and not removed; it stops being returned and
        stays readable, which is what makes `history` able to answer.
        """
        return await self._call(
            "POST",
            f"/v1/objects/{object_id}/supersede",
            json={"content": content, "kind": kind, "project_id": project_id},
        )

    async def retire(self, object_id: str, *, reason: str) -> dict[str, Any]:
        """Exclude an object from retrieval and from compile. It stays readable.

        This is the closest thing to a delete, and it deliberately is not one. A
        reason is required because a retirement nobody can explain later is
        indistinguishable from a bug.
        """
        return await self._call(
            "POST", f"/v1/objects/{object_id}/retire", json={"reason": reason}
        )

    # --- move ---------------------------------------------------------------------

    async def compile(
        self, *, destination: str = "local", project_id: str | None = None
    ) -> dict[str, Any]:
        """Compile the graph into a destination's native containers.

        Subject to the same review gate as the CLI: a 409 means objects have not been
        reviewed since they last changed. An API that could walk around that would
        make the gate a UI courtesy.
        """
        return await self._call(
            "POST",
            "/v1/compile",
            json={"destination": destination, "project_id": project_id},
        )


def _message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload)
    return str(payload)
