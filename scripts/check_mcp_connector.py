#!/usr/bin/env python3
"""Drive a coleta MCP endpoint the way a desktop client does, and report what works.

The desktop surfaces — Claude Desktop, Claude Code, and any other MCP client — are
not covered by the browser extension and never will be: there is no page to inject
into. They reach the graph through MCP instead, so "does the desktop path work" is
really "does this MCP endpoint complete a handshake, list its tools, and honour
locality and scopes on a real call".

That is a tedious thing to check by hand. The transport is Streamable HTTP with
Server-Sent Events, the session id comes back as a response *header* on initialize
and must be echoed on every later request, and the local and hosted deployments
differ: local is stateful and rejects a call with no session id, while the hosted
one is stateless because a Vercel request may land on any instance. Checking it with
curl means parsing SSE frames in a shell, which is how you end up believing a
transport bug is a product bug.

    python scripts/check_mcp_connector.py http://127.0.0.1:8788/mcp --key sk-...
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

ACCEPT = "application/json, text/event-stream"


class Client:
    """The smallest Streamable-HTTP MCP client that can answer the question."""

    def __init__(self, url: str, key: str, timeout: float = 30.0) -> None:
        self._url = url
        self._headers = {
            "Authorization": f"Bearer {key}",
            "Accept": ACCEPT,
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=timeout)
        self.session_id: str | None = None

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _decode(body: str) -> dict[str, Any]:
        """One JSON-RPC response, from either a bare body or an SSE frame."""
        body = body.strip()
        if not body:
            raise ValueError("empty response")
        if not body.startswith("{"):
            # SSE: take the last `data:` line, which is the payload.
            payloads = [
                line[len("data:") :].strip()
                for line in body.splitlines()
                if line.startswith("data:")
            ]
            if not payloads:
                raise ValueError(f"no JSON and no SSE data frame in: {body[:200]}")
            body = payloads[-1]
        return json.loads(body)

    def send(self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False):
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = 1
        if params is not None:
            payload["params"] = params
        headers = dict(self._headers)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        response = self._client.post(self._url, headers=headers, json=payload)
        if response.status_code == 401:
            raise SystemExit("401 — the key was refused. Check it was issued for this server.")
        response.raise_for_status()
        # Only initialize sets it; a stateless deployment never does.
        if not self.session_id:
            self.session_id = response.headers.get("mcp-session-id")
        if notify:
            return None
        message = self._decode(response.text)
        if "error" in message:
            raise SystemExit(f"{method} failed: {message['error']}")
        return message["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="the /mcp endpoint")
    parser.add_argument("--key", required=True, help="a connector bearer key")
    parser.add_argument("--query", default="work", help="what to search for")
    parser.add_argument(
        "--write",
        action="store_true",
        help="also call write_memory — this mutates the graph",
    )
    args = parser.parse_args()

    client = Client(args.url, args.key)
    try:
        result = client.send(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "coleta-connector-check", "version": "1"},
            },
        )
        mode = "stateful" if client.session_id else "stateless"
        print(f"handshake      ok — {mode} transport")
        print(f"               server says: {result['serverInfo']['name']}")
        if result.get("instructions"):
            print(f"               instructions present ({len(result['instructions'])} chars)")
        if client.session_id:
            client.send("notifications/initialized", notify=True)

        tools = client.send("tools/list")["tools"]
        print(f"tools/list     ok — {len(tools)}: {', '.join(t['name'] for t in tools)}")

        called = client.send(
            "tools/call", {"name": "search_context", "arguments": {"query": args.query}}
        )
        if called.get("isError"):
            print(f"search_context REFUSED — {called}")
            return 1
        text = "".join(part.get("text", "") for part in called.get("content", []))
        print(f"search_context ok — {len(text)} chars returned")
        first = next((line for line in text.splitlines() if line.strip()), "")
        if first:
            print(f"               first line: {first[:88]}")

        if args.write:
            wrote = client.send(
                "tools/call",
                {
                    "name": "write_memory",
                    "arguments": {"content": "Connector check probe; safe to retire."},
                },
            )
            state = "REFUSED" if wrote.get("isError") else "ok"
            print(f"write_memory   {state}")
        else:
            print("write_memory   skipped (pass --write to exercise it)")
    finally:
        client.close()

    print("\nThe desktop path is reachable. Point a client at this URL with this key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
