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
    async def list_models() -> dict:
        """List the models available on the configured inference endpoint.

        Automatically picks the right discovery route for the backend:
          - Ollama endpoints use `/api/tags`
          - OpenAI-compatible endpoints (vLLM, Tenstorrent/Koyeb, etc.)
            use `/v1/models`

        Returns:
            A dict shaped `{"endpoint": ..., "backend": "ollama" | "vllm",
            "models": [...]}`. On unreachable endpoints, HTTP errors, or
            empty model lists, an `error` field carries a human-readable
            explanation and `models` is `[]`. The shape stays consistent
            so MCP clients see structured JSON either way.
        """
        backend = "ollama" if config.is_ollama else "vllm"
        url = (
            f"{config.endpoint}/api/tags"
            if config.is_ollama
            else f"{config.endpoint}/v1/models"
        )

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(url, headers=config.auth_headers)
        except httpx.ConnectError as exc:
            return _failure(backend, f"Could not reach {config.endpoint}: {exc}")

        if response.status_code >= 400:
            return _failure(
                backend,
                f"Endpoint returned HTTP {response.status_code} from {url}.",
                detail=response.text[:300],
            )

        try:
            data = response.json()
        except ValueError as exc:
            return _failure(backend, f"Endpoint returned invalid JSON: {exc}")

        # Ollama's shape:  {"models": [{"name": "llama3.2:3b", ...}, ...]}
        # OpenAI's shape:  {"data":   [{"id":   "llama-3.1-8b", ...}, ...]}
        if config.is_ollama:
            names = [m["name"] for m in data.get("models", [])]
        else:
            names = [m["id"] for m in data.get("data", [])]

        if not names:
            return _failure(
                backend,
                (
                    f"The endpoint at {config.endpoint} did not return any "
                    "models. For Ollama, pull one with `ollama pull "
                    "llama3.2:3b`. For a vLLM endpoint, check the server "
                    "was started with a model path."
                ),
            )

        return {
            "endpoint": config.endpoint,
            "backend": backend,
            "models": names,
        }


def _failure(backend: str, message: str, *, detail: str | None = None) -> dict:
    """Build a uniform failure-shaped response.

    Keeps the return type stable (always a dict), folds the error string
    into the response so MCP clients still see structured content. Mirrors
    the partial-failure convention `hardware_info` uses, an embedded error
    field beats two different return shapes. `format_error` still hits the
    stderr log on every call, the operator-facing line matters even when
    the wire shape moved from string to dict.
    """
    return {
        "endpoint": config.endpoint,
        "backend": backend,
        "models": [],
        "error": format_error(message, detail=detail),
    }
