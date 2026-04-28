"""The `hardware_info` tool: a real picture of the box.

Three signals merged into one response:

  - **Static spec** from `tools.hw_catalog`. Hardcoded silicon facts
    (chip family, DRAM, Tensix cores, TFLOPS) the operator can't change
    by setting an env var. Looked up by exact match on `TT_HARDWARE`.

  - **Live state** from `/metrics`. KV cache utilisation, queue depth,
    preemption count, p95 latency, lifetime token counts. The picture
    of what vLLM is doing right now.

  - **Server identity** from `/version` and `/v1/models`. Which vLLM
    minor, which models served and at what context length.

Every fetch is wrapped so one failing source doesn't kill the others, the
response carries a `sources` field saying who answered, who didn't, and
why. Partial failure is a first-class state. An agent that knows the
metrics endpoint is down but the model list came through can still
reason about what to do next.

Why split signals out into a `sources` ledger instead of just letting fields
be missing? Two reasons. Missing fields are ambiguous (was that field
unsupported, or did the fetch fail?). And agents are quietly bad at
inferring "this came back as `null` because the source was down", spelling
it out makes the failure mode legible.
"""

from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

from config import config
from tools import hw_catalog
from tools._http import DEFAULT_TIMEOUT
from tools._metrics import histogram_quantile, parse_prometheus


def register(mcp: FastMCP) -> None:
    """Attach the `hardware_info` tool to the given FastMCP server."""

    @mcp.tool()
    async def hardware_info() -> dict:
        """Describe the inference endpoint as static spec × live state × server.

        Merges three signals so an agent can answer questions like
        "is this box saturated?", "how fast is it actually going?",
        and "what's the silicon physically capable of?" in one call.

        Behaviour differs by backend:
          - Ollama (local dev): returns a short note explaining what
            the tool would return against real hardware. Ollama doesn't
            expose `/metrics` or `/version`, so the merged shape isn't
            meaningful here.
          - OpenAI-compatible (vLLM, Tenstorrent/Koyeb, mock_vllm):
            fetches `/health`, `/version`, `/v1/models`, and `/metrics`
            in turn. Each source is wrapped, a failed `/metrics` does
            not prevent `/v1/models` from returning.

        Returns:
            A dict with `hardware`, `server`, `models`, `live`, and
            `sources`. The `sources` field maps each signal name to
            either `"ok"` or `"failed: <reason>"`, so a caller can see
            at a glance which numbers to trust.
        """
        if config.is_ollama:
            return _ollama_placeholder()
        return await _vllm_hardware_info()


def _ollama_placeholder() -> dict:
    """Explain what the tool would return against a real endpoint."""
    return {
        "backend": "ollama",
        "endpoint": config.endpoint,
        "note": (
            "Ollama runs on your local CPU/GPU via llama.cpp. It does not "
            "expose Prometheus metrics, a /version route, or hardware "
            "telemetry. Point TT_ENDPOINT at a vLLM-style backend (the "
            "mock at scripts/mock_vllm.py works) to exercise the merged "
            "static x live x server shape this tool returns elsewhere."
        ),
        "hardware": {"label": config.hardware, **hw_catalog.lookup(config.hardware)},
    }


async def _vllm_hardware_info() -> dict:
    """Fetch every available source and merge into one response."""
    sources: dict[str, str] = {}
    out: dict = {
        "endpoint": config.endpoint,
        "hardware": {"label": config.hardware, **hw_catalog.lookup(config.hardware)},
        "server": {},
        "models": [],
        "live": {},
        "sources": sources,
    }

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        sources["health"] = await _fetch_health(client)
        out["server"], sources["version"] = await _fetch_version(client)
        out["models"], sources["models"] = await _fetch_models(client)
        out["live"], sources["metrics"] = await _fetch_live_metrics(client)

    return out


async def _fetch_health(client: httpx.AsyncClient) -> str:
    """Probe `/health`. Returns the source-status string for the ledger.

    vLLM's /health returns 200 with an empty body when ready. We don't
    surface the body, only the up/down signal.
    """
    try:
        response = await client.get(
            f"{config.endpoint}/health", headers=config.auth_headers
        )
    except httpx.HTTPError as exc:
        return f"failed: {type(exc).__name__}: {exc}"
    if response.status_code >= 400:
        return f"failed: HTTP {response.status_code}"
    return "ok"


async def _fetch_version(client: httpx.AsyncClient) -> tuple[dict, str]:
    """Fetch `/version`. Returns (server-dict, source-status)."""
    try:
        response = await client.get(
            f"{config.endpoint}/version", headers=config.auth_headers
        )
    except httpx.HTTPError as exc:
        return {}, f"failed: {type(exc).__name__}: {exc}"
    if response.status_code >= 400:
        return {}, f"failed: HTTP {response.status_code}"
    try:
        body = response.json()
    except ValueError as exc:
        return {}, f"failed: invalid JSON ({exc})"
    return {"version": body.get("version")}, "ok"


async def _fetch_models(client: httpx.AsyncClient) -> tuple[list[dict], str]:
    """Fetch `/v1/models`. Returns (model-list, source-status).

    Each entry is `{"id": str, "max_model_len": int | None}`. We trim
    the OpenAI shape down to what callers actually use, the full
    response includes timestamps and ownership fields the agent does
    not need to see.
    """
    try:
        response = await client.get(
            f"{config.endpoint}/v1/models", headers=config.auth_headers
        )
    except httpx.HTTPError as exc:
        return [], f"failed: {type(exc).__name__}: {exc}"
    if response.status_code >= 400:
        return [], f"failed: HTTP {response.status_code}"
    try:
        data = response.json()
    except ValueError as exc:
        return [], f"failed: invalid JSON ({exc})"

    models = [
        {"id": m.get("id", "unknown"), "max_model_len": m.get("max_model_len")}
        for m in data.get("data", [])
    ]
    return models, "ok"


async def _fetch_live_metrics(client: httpx.AsyncClient) -> tuple[dict, str]:
    """Fetch `/metrics` and pluck the signals worth foregrounding.

    Returns (live-dict, source-status). The live-dict is a hand-picked
    subset, keyed by short human-readable names so an agent can reason
    over them without learning the `vllm:*` namespace. The full set is
    available via the dedicated `metrics` tool.
    """
    try:
        response = await client.get(
            f"{config.endpoint}/metrics", headers=config.auth_headers
        )
    except httpx.HTTPError as exc:
        return {}, f"failed: {type(exc).__name__}: {exc}"
    if response.status_code >= 400:
        return {}, f"failed: HTTP {response.status_code}"

    parsed = parse_prometheus(response.text)
    e2e = parsed.get("vllm:e2e_request_latency_seconds")
    ttft = parsed.get("vllm:time_to_first_token_seconds")

    live = {
        "running_requests": parsed.get("vllm:num_requests_running"),
        "waiting_requests": parsed.get("vllm:num_requests_waiting"),
        "kv_cache_usage": parsed.get("vllm:gpu_cache_usage_perc"),
        "prefix_cache_hit_rate": parsed.get("vllm:gpu_prefix_cache_hit_rate"),
        "preemptions_total": parsed.get("vllm:num_preemptions_total"),
        "prompt_tokens_total": parsed.get("vllm:prompt_tokens_total"),
        "generation_tokens_total": parsed.get("vllm:generation_tokens_total"),
        "p50_e2e_latency_s": _round(histogram_quantile(e2e, 0.5)) if e2e else None,
        "p95_e2e_latency_s": _round(histogram_quantile(e2e, 0.95)) if e2e else None,
        "p50_ttft_s": _round(histogram_quantile(ttft, 0.5)) if ttft else None,
        "p95_ttft_s": _round(histogram_quantile(ttft, 0.95)) if ttft else None,
    }
    return live, "ok"


def _round(value: float | None) -> float | None:
    """Round to milliseconds-of-a-second precision, or pass through None."""
    return None if value is None else round(value, 4)
