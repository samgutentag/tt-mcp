"""The `generate` tool: text completion via OpenAI-compatible chat.

This module is intentionally small and single-purpose. If you add a new
completion-style tool (e.g. `embed`, `classify`), give it its own file
rather than bundling it here. One tool per module keeps the tutorial
content readable.
"""

from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

from config import config
from tools._http import DEFAULT_TIMEOUT, format_error


def register(mcp: FastMCP) -> None:
    """Attach the `generate` tool to the given FastMCP server."""

    @mcp.tool()
    async def generate(prompt: str, model: str | None = None) -> str:
        """Generate text from the configured inference endpoint.

        Sends `prompt` as a single-user-message chat completion request to
        the OpenAI-compatible `/v1/chat/completions` route. Works against
        Ollama, vLLM, and any other backend that speaks the same wire
        format, including Tenstorrent's hosted vLLM deployments.

        Args:
            prompt: The user message to send to the model. Plain text, no
                special formatting required.
            model: Optional override for the model identifier. If omitted,
                the server uses the model set in the TT_MODEL environment
                variable. Call `list_models` first if you want to see what
                the backend has available.

        Returns:
            The assistant's reply as a plain string, or a human-readable
            error message if the endpoint was unreachable, the model was
            not found, or the response was malformed.
        """
        chosen_model = model or config.model
        url = f"{config.endpoint}/v1/chat/completions"
        payload = {
            "model": chosen_model,
            "messages": [{"role": "user", "content": prompt}],
            # `stream: False` forces a single JSON response. MCP tools that
            # stream token-by-token are possible but require Context-based
            # progress reporting; we keep the tool simple and return the
            # final string. Streaming is a natural Day-N upgrade.
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    url, json=payload, headers=config.auth_headers
                )
        except httpx.ConnectError as exc:
            return format_error(
                f"Could not reach the inference endpoint at {config.endpoint}. "
                "Is the server running? For Ollama try "
                "`brew services start ollama`.",
                detail=str(exc),
            )
        except httpx.TimeoutException:
            return format_error(
                f"The endpoint at {config.endpoint} did not respond within "
                f"{DEFAULT_TIMEOUT.read}s. The model may be loading, try "
                "again."
            )

        if response.status_code == 404:
            return format_error(
                f"Model `{chosen_model}` was not found on "
                f"{config.endpoint}. Call `list_models` to see what is "
                "available."
            )
        if response.status_code >= 400:
            return format_error(
                f"Endpoint returned HTTP {response.status_code}.",
                detail=response.text[:500],
            )

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, ValueError, IndexError) as exc:
            # 2xx but the body did not match the OpenAI schema. That is a
            # real bug on the backend side. Surface it rather than
            # silently returning "".
            return format_error(
                "Endpoint returned a response we could not parse.",
                detail=f"{type(exc).__name__}: {exc}; body={response.text[:300]}",
            )
