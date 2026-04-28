"""The `metrics` tool: raw vLLM Prometheus state, parsed.

The smallest tool in the repo. Hits `/metrics`, runs the body through
`tools._metrics.parse_prometheus`, returns the parsed dict as JSON.

Why ship this in addition to `hardware_info`? Two audiences. `hardware_info`
plates the data, picks a few signals, narrates them as a single coherent
response. `metrics` hands an agent the full set of counters, gauges, and
histograms so it can do its own analysis. Don't make the agent choose
between "raw numbers" and "interpretation", give both, separately.

Why JSON-encoded as a string and not a dict? MCP tools return text content
to the model. The wire shape is `{"content": [{"type": "text", "text": ...}]}`
and pretty-printed JSON is the friendliest body for an agent to reason
over. A future enhancement could return structured content blocks instead;
this is the simplest thing that works today.
"""

from __future__ import annotations

import json

import httpx
from mcp.server.fastmcp import FastMCP

from config import config
from tools._http import DEFAULT_TIMEOUT, format_error
from tools._metrics import parse_prometheus


def register(mcp: FastMCP) -> None:
    """Attach the `metrics` tool to the given FastMCP server."""

    @mcp.tool()
    async def metrics() -> str:
        """Return parsed Prometheus metrics from the vLLM endpoint.

        Hits the endpoint's `/metrics` route, parses the exposition format,
        and returns a JSON dict of `vllm:*` counters, gauges, and
        histograms. Histograms are returned as
        `{"buckets": [(le, count), ...], "count": ..., "sum": ...}`,
        which `histogram_quantile` (or the agent itself) can use to
        compute p50/p95 latencies.

        Behaviour differs by backend:
          - Ollama (local dev): returns a short note. Ollama does not
            expose Prometheus metrics; calling this tool against the
            local dev backend is a no-op.
          - OpenAI-compatible (vLLM, Tenstorrent/Koyeb, mock_vllm):
            fetches and parses `/metrics`.

        Returns:
            A JSON-encoded dict of metric families. Empty objects mean
            the endpoint is up but has no observations yet (e.g. fresh
            server, no requests served). On unreachable endpoints or
            HTTP errors, a human-readable error string instead.
        """
        if config.is_ollama:
            return (
                "Ollama does not expose Prometheus metrics. Point TT_ENDPOINT "
                "at a vLLM-style backend (the mock at scripts/mock_vllm.py "
                "works) to exercise this tool."
            )

        url = f"{config.endpoint}/metrics"
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

        parsed = parse_prometheus(response.text)
        # `default=str` is a belt-and-braces guard for any non-serializable
        # values that might sneak in (NaN floats, mostly). Better to render
        # them as strings than fail the whole tool call.
        return json.dumps(parsed, indent=2, default=str)
