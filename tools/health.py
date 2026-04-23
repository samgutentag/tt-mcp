"""The `hardware_info` tool: surface the physical nature of the endpoint.

The point of this tool is not to return rich telemetry, vLLM's
OpenAI-compatible routes do not expose GPU utilisation, hardware type, or
anything of the sort. The point is to make it visible to an agent that the
endpoint is backed by real hardware, and to describe that hardware using
whatever signals we have:

  - `TT_HARDWARE` env var (operator-declared label like
    "Tenstorrent Wormhole n300")
  - `/v1/models` response, which vLLM enriches with `max_model_len`
    (the context window per model)
  - The endpoint URL itself (tells us whether this is a local dev
    backend or a real deployment)

If we are pointed at Ollama, the tool explains what it *would* return
against real hardware.
"""

from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

from config import config
from tools._http import DEFAULT_TIMEOUT, format_error


def register(mcp: FastMCP) -> None:
    """Attach the `hardware_info` tool to the given FastMCP server."""

    @mcp.tool()
    async def hardware_info() -> str:
        """Describe the inference endpoint's backing hardware.

        Useful when an agent needs to know *what* it is calling, e.g. to
        decide whether a request is cheap-local or hitting real accelerator
        hardware, or to include hardware details in a response to a user.

        Behaviour differs by backend:
          - Ollama (local dev): explains that this is a local backend and
            what the tool would return against real Tenstorrent hardware.
          - OpenAI-compatible (vLLM, Tenstorrent/Koyeb): queries
            `/v1/models` and returns models with their context length, plus
            the `TT_HARDWARE` label if the operator set one.

        Returns:
            A human-readable description of the endpoint and its hardware.
        """
        if config.is_ollama:
            return _ollama_placeholder()
        return await _vllm_hardware_info()


def _ollama_placeholder() -> str:
    """Explain what the tool would return against a real endpoint."""
    return (
        f"Endpoint: {config.endpoint} (Ollama -> local dev backend)\n"
        "Hardware: not a hardware accelerator. Ollama runs on your "
        "CPU/GPU via llama.cpp.\n"
        "\n"
        "When TT_ENDPOINT points at a Tenstorrent/Koyeb or other "
        "OpenAI-compatible vLLM endpoint, this tool returns:\n"
        "  - the hardware label (from the TT_HARDWARE env var)\n"
        "  - the models served, with their max context length\n"
        "  - the endpoint URL for verification\n"
        "\n"
        "To exercise this tool against real hardware, set TT_ENDPOINT to "
        "the Koyeb URL of your Tenstorrent deployment and restart the "
        "server."
    )


async def _vllm_hardware_info() -> str:
    """Query /v1/models and format the response for the calling agent."""
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
    models = data.get("data", [])

    lines = [f"Endpoint: {config.endpoint}"]
    if config.hardware:
        lines.append(f"Hardware: {config.hardware}")
    else:
        lines.append(
            "Hardware: unspecified (set TT_HARDWARE to advertise the "
            "backing accelerator, e.g. 'Tenstorrent Wormhole n300')"
        )

    if not models:
        lines.append("Models: none advertised by this endpoint.")
        return "\n".join(lines)

    lines.append("Models:")
    for m in models:
        # `max_model_len` is a vLLM-specific extension that other OpenAI-
        # compatible servers may or may not include. Use .get() and fall
        # back gracefully.
        model_id = m.get("id", "unknown")
        max_len = m.get("max_model_len")
        if max_len is not None:
            lines.append(f"  - {model_id}  (context: {max_len} tokens)")
        else:
            lines.append(f"  - {model_id}")

    return "\n".join(lines)
