"""The `metrics` tool: raw vLLM Prometheus state, parsed.

The smallest tool in the repo. Hits `/metrics`, parses the body through
`tools._metrics.parse_prometheus`, returns the parsed dict.

Why ship this in addition to `hardware_info`? Two audiences. `hardware_info`
plates the data, picks a few signals, narrates them as a single coherent
response. `metrics` hands an agent the full set of counters, gauges, and
histograms so it can do its own analysis. Don't make the agent choose
between "raw numbers" and "interpretation", give both, separately.

Why a dict and not a JSON-encoded string? FastMCP serialises a dict return
into both a text JSON block (for clients that read text) and a
`structuredContent` field (for clients that read structured output). MCP
hosts like the Inspector compare the two for consistency, returning a
JSON-encoded string sets up a mismatch they flag as a warning. The dict
return matches the shape `hardware_info` already uses.
"""

from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

from config import config
from tools._http import DEFAULT_TIMEOUT, format_error
from tools._metrics import parse_prometheus


def register(mcp: FastMCP) -> None:
    """Attach the `metrics` tool to the given FastMCP server."""

    @mcp.tool()
    async def metrics() -> dict:
        """Return parsed Prometheus metrics from the vLLM endpoint.

        Hits the endpoint's `/metrics` route, parses the exposition format,
        and returns the metric families as a dict. Histograms come back as
        `{"buckets": [(le, count), ...], "count": ..., "sum": ...}`, which
        `histogram_quantile` (or the agent itself) can use to compute
        p50/p95 latencies.

        Behaviour differs by backend:
          - Ollama (local dev): returns a `{backend, note}` shape with an
            empty `metrics` field. Ollama does not expose Prometheus
            metrics; calling this tool against the local dev backend is
            a no-op.
          - OpenAI-compatible (vLLM, Tenstorrent/Koyeb, mock_vllm):
            fetches and parses `/metrics`.

        Returns:
            A dict shaped `{"endpoint": ..., "backend": "vllm" | "ollama",
            "metrics": {...}}`. On unreachable endpoints, HTTP errors, or
            backends that don't expose `/metrics`, an `error` (or `note`)
            field carries the human-readable explanation and `metrics` is
            `{}`. The shape stays consistent so MCP clients see structured
            JSON either way.
        """
        backend = "ollama" if config.is_ollama else "vllm"

        if config.is_ollama:
            return {
                "endpoint": config.endpoint,
                "backend": backend,
                "metrics": {},
                "note": (
                    "Ollama does not expose Prometheus metrics. Point "
                    "TT_ENDPOINT at a vLLM-style backend (the mock at "
                    "scripts/mock_vllm.py works) to exercise this tool."
                ),
            }

        url = f"{config.endpoint}/metrics"
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(url, headers=config.auth_headers)
        except httpx.ConnectError as exc:
            return _failure(backend, f"Could not reach {config.endpoint}.", detail=str(exc))

        if response.status_code >= 400:
            return _failure(
                backend,
                f"Endpoint returned HTTP {response.status_code} from {url}.",
                detail=response.text[:300],
            )

        parsed = parse_prometheus(response.text)
        return {
            "endpoint": config.endpoint,
            "backend": backend,
            "metrics": parsed,
        }


def _failure(backend: str, message: str, *, detail: str | None = None) -> dict:
    """Build a uniform failure-shaped response.

    Mirrors the partial-failure convention used by `list_models` and
    `hardware_info`, the wire shape stays stable across success and
    failure with an embedded `error` string instead of a different return
    type. `format_error` still hits stderr the same way it did when this
    tool returned strings, the operator-facing log line matters.
    """
    return {
        "endpoint": config.endpoint,
        "backend": backend,
        "metrics": {},
        "error": format_error(message, detail=detail),
    }
