"""PracticePanther MCP MCP server.

Exposes PracticePanther MCP as MCP tools so Claude / Cursor / any MCP client can
read and write data through the PracticePanther MCP API.

Quick start:
    pip install -e .
    export PRACTICEPANTHER_USERNAME=...
    export PRACTICEPANTHER_PASSWORD=...
    practicepanther_mcp
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import PracticepantherAPIError, PracticepantherAuthError, PracticepantherClient, PracticepantherError


def _format_error(e: Exception) -> str:
    if isinstance(e, PracticepantherAuthError):
        return f"Authentication failed against PracticePanther MCP. Check credentials. Details: {e}"
    if isinstance(e, PracticepantherAPIError):
        return f"PracticePanther MCP API error (HTTP {e.status_code}): {e}"
    if isinstance(e, PracticepantherError):
        return f"PracticePanther MCP error: {e}"
    return f"Unexpected error: {e!r}"


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


mcp = FastMCP(
    "practicepanther_mcp",
    instructions=(
        "Tools for interacting with PracticePanther MCP via its API. "
        "Use these to read and write data for any account the user has authorized."
    ),
)


def _client() -> PracticepantherClient:
    return PracticepantherClient()


# ----- Tools ----------------------------------------------------------------


@mcp.tool()
async def health_check() -> str:
    """Verify credentials are valid by hitting a known endpoint."""
    try:
        # TODO: replace with a real endpoint, e.g. self._request("GET", "/me")
        await _client()._request("GET", "/health")  # adjust to your SaaS
        return _json({"status": "ok"})
    except PracticepantherError as e:
        return _format_error(e)


# TODO: add your vertical-specific tools here. Example:
#
# @mcp.tool()
# async def list_things(limit: int = 25) -> str:
#     """List the user's things. Useful for browsing or finding IDs."""
#     try:
#         result = await _client().list_things()
#         return _json({"count": len(result), "items": result[:limit]})
#     except PracticepantherError as e:
#         return _format_error(e)


def main() -> None:
    """Run the MCP server over stdio."""
    try:
        mcp.run()
    except PracticepantherAuthError as e:
        print(f"[practicepanther_mcp-mcp] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()