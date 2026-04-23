"""The `list_models` tool: discover what the backend serves.

Ollama and vLLM disagree on how to list models. This module hides that
disagreement behind a single tool whose return value is uniform: a
newline-delimited list of model names. The leak is contained here rather
than propagated through every caller.
"""

from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

from config import config
from tools._http import DEFAULT_TIMEOUT, format_error


def register(mcp: FastMCP) -> None:
    """Attach the `list_models` tool to the given FastMCP server."""

    @mcp.tool()
    async def list_models() -> str:
        """List the models available on the configured inference endpoint.

        Automatically picks the right discovery route for the backend:
          - Ollama endpoints use `/api/tags`
          - OpenAI-compatible endpoints (vLLM, Tenstorrent/Koyeb, etc.)
            use `/v1/models`

        Returns:
            A newline-delimited list of model names, or a helpful error if
            the endpoint was unreachable or returned no models.
        """
        if config.is_ollama:
            url = f"{config.endpoint}/api/tags"
        else:
            url = f"{config.endpoint}/v1/models"

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(url, headers=config.auth_headers)
        except httpx.ConnectError as exc:
            return format_error(f"Could not reach {config.endpoint}.", detail=str(exc))

        if response.status_code >= 400:
            return format_error(
                f"Endpoint returned HTTP {response.status_code} from {url}.",
                detail=response.text[:300],
            )

        data = response.json()

        # Ollama's shape:  {"models": [{"name": "llama3.2:3b", ...}, ...]}
        # OpenAI's shape:  {"data":   [{"id":   "llama-3.1-8b", ...}, ...]}
        if config.is_ollama:
            names = [m["name"] for m in data.get("models", [])]
        else:
            names = [m["id"] for m in data.get("data", [])]

        if not names:
            return (
                f"The endpoint at {config.endpoint} did not return any "
                "models. For Ollama, pull one with "
                "`ollama pull llama3.2:3b`. For a vLLM endpoint, check "
                "the server was started with a model path."
            )

        header = f"Models available on {config.endpoint}:"
        return header + "\n  - " + "\n  - ".join(names)
