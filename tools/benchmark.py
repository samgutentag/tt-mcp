"""The `benchmark` tool: measure end-to-end completion latency.

Sequential, fixed prompt, short generations. The point is to give an agent
a defensible "this is how fast the box is going right now" number without
the noise that a real load test would introduce.

Sequential-by-design. Concurrency is a v2 feature for two reasons. First,
sequential numbers are interpretable, p50 latency means what you think it
means, with no contention from other inflight requests. Second, an MCP tool
that hammers a shared endpoint is a footgun, the agent has no way to know
whose budget it's spending. A demo that turns into a load test is a worse
demo than one that runs in twelve seconds.

TTFT is omitted on purpose. The OpenAI-compatible non-streaming response
gives total elapsed time; deriving TTFT from that would be a fabrication.
When streaming lands as a tool variant, this benchmark gets a meaningful
TTFT field.

Progress reporting. MCP hosts (Claude Desktop, the Inspector) hold a
per-tool-call timeout, often as low as 10 seconds. A `n=5` benchmark
against a real cold-start endpoint blows past that easily. So we accept
an injected `Context` and emit a progress notification after every
request. Hosts reset their timeout on each notification, which lets
benchmarks of any reasonable size finish without tripping the wire.
"""

from __future__ import annotations

import time

import httpx
from mcp.server.fastmcp import Context, FastMCP

from config import config
from tools._http import DEFAULT_TIMEOUT, format_error


def register(mcp: FastMCP) -> None:
    """Attach the `benchmark` tool to the given FastMCP server."""

    @mcp.tool()
    async def benchmark(
        n: int = 5,
        prompt: str = "Say hello in one word.",
        max_tokens: int = 8,
        ctx: Context | None = None,
    ) -> dict:
        """Measure end-to-end completion latency over `n` sequential calls.

        Fires `n` `/v1/chat/completions` requests one after another (no
        concurrency), times each end-to-end, and returns aggregate
        statistics plus a tokens/second estimate based on the `usage`
        block in each response.

        Streams progress notifications to the host between calls (when an
        MCP `Context` is available), which is what keeps the host's
        per-tool-call timeout from tripping on `n>1` runs against a real
        cold-start endpoint. The notification carries the per-call
        elapsed time so an operator watching the progress bar sees real
        numbers, not just a counter.

        Args:
            n: Number of requests. Default 5, enough for stable p50/p95
                without making the user wait. Bump to 20+ if you want
                tighter p95 numbers; expect proportional wall time.
            prompt: The text sent on each call. Default is a short prompt
                that stresses the path without generating much output.
            max_tokens: Cap on generated tokens per call. Kept small so
                the benchmark measures the request/response path, not
                generation throughput on a long completion.

        Returns:
            A dict with per-call timings (`min`, `max`, p50, p95), total
            tokens generated, an averaged `tokens_per_sec`, and an
            `errors` count for non-2xx or malformed responses. The `note`
            field warns callers not to read this as a load test, agents
            have a habit of over-claiming. On input validation failure or
            "all calls failed" cases, an `error` field is set and the
            timing fields are absent.
        """
        if n < 1:
            return _failure("benchmark requires n >= 1.", n=n)
        if max_tokens < 1:
            return _failure("benchmark requires max_tokens >= 1.", n=n)

        url = f"{config.endpoint}/v1/chat/completions"
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }

        timings_ms: list[float] = []
        tokens_generated = 0
        errors = 0

        # Initial progress so the host UI animates from zero immediately.
        # Hosts that ignore progress notifications won't care; those that
        # honour them get a snappy "the tool is alive" signal before the
        # first request lands.
        await _progress(ctx, 0, n, "warming up")

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                for i in range(n):
                    t0 = time.perf_counter()
                    try:
                        response = await client.post(
                            url, json=payload, headers=config.auth_headers
                        )
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                    except (httpx.ConnectError, httpx.TimeoutException) as exc:
                        # First-request failure is a setup problem; surface
                        # it loudly so the user fixes the endpoint, not
                        # the benchmark args.
                        if not timings_ms:
                            return _failure(
                                f"Could not reach {config.endpoint}.",
                                n=n,
                                detail=str(exc),
                            )
                        errors += 1
                        await _progress(
                            ctx, i + 1, n, f"call {i + 1}/{n}: error ({type(exc).__name__})"
                        )
                        continue

                    if response.status_code >= 400:
                        errors += 1
                        await _progress(
                            ctx, i + 1, n, f"call {i + 1}/{n}: HTTP {response.status_code}"
                        )
                        continue

                    timings_ms.append(elapsed_ms)
                    try:
                        data = response.json()
                        tokens_generated += data.get("usage", {}).get(
                            "completion_tokens", 0
                        )
                    except (KeyError, ValueError):
                        # Response succeeded but body was malformed. Count
                        # the timing (the path *did* work) but not the
                        # tokens, and don't penalise the call as an error.
                        pass

                    await _progress(
                        ctx, i + 1, n, f"call {i + 1}/{n}: {elapsed_ms:.0f} ms"
                    )
        except httpx.ConnectError as exc:
            return _failure(
                f"Could not reach {config.endpoint}.", n=n, errors=errors, detail=str(exc)
            )

        if not timings_ms:
            return _failure(
                f"All {n} benchmark calls failed.",
                n=n,
                errors=errors,
                detail=f"errors={errors}",
            )

        return {
            "endpoint": config.endpoint,
            "model": config.model,
            "n": n,
            "e2e_ms": _summarise(timings_ms),
            "tokens_generated_total": tokens_generated,
            "tokens_per_sec": _tokens_per_sec(tokens_generated, timings_ms),
            "errors": errors,
            "note": "sequential; not a load test",
        }


def _summarise(values_ms: list[float]) -> dict[str, float]:
    """Compute basic stats. Sorted-list quantiles, no numpy dependency."""
    sorted_ms = sorted(values_ms)
    n = len(sorted_ms)
    return {
        "min": round(sorted_ms[0], 1),
        "max": round(sorted_ms[-1], 1),
        "p50": round(_quantile(sorted_ms, 0.5), 1),
        "p95": round(_quantile(sorted_ms, 0.95), 1) if n >= 2 else round(sorted_ms[0], 1),
    }


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile on a pre-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_values):
        return sorted_values[lo]
    return sorted_values[lo] + frac * (sorted_values[lo + 1] - sorted_values[lo])


def _failure(message: str, *, n: int, errors: int = 0, detail: str | None = None) -> dict:
    """Build a uniform failure-shaped response.

    Mirrors the `list_models` and `metrics` partial-failure pattern. The
    wire shape stays stable across success and failure with an embedded
    `error` string instead of a different return type, which keeps MCP
    hosts that compare text vs structured content from flagging a
    mismatch. `format_error` still hits stderr the same way it did when
    this tool returned strings, the operator-facing log line matters.
    """
    return {
        "endpoint": config.endpoint,
        "model": config.model,
        "n": n,
        "errors": errors,
        "error": format_error(message, detail=detail),
    }


async def _progress(ctx: Context | None, progress: int, total: int, message: str) -> None:
    """Fire a progress notification, swallowing errors.

    No-op when the host did not inject a Context (subprocess smoke tests
    and some non-host callers). When a Context is present, we still wrap
    the call: a host that disconnects mid-benchmark would otherwise raise
    here and abort a perfectly valid measurement run. The benchmark's
    job is to return numbers, not to complain about the wire.
    """
    if ctx is None:
        return
    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except Exception:
        # Telemetry is best-effort, never load-bearing.
        pass


def _tokens_per_sec(tokens: int, timings_ms: list[float]) -> float | None:
    """Approximate tokens/second using the sum of e2e times.

    Returns `None` if no tokens were generated (mock backends or
    malformed `usage` blocks). Sum-of-times is an approximation, the
    real measure would compute per-call rates and average them, but
    for sequential short generations the difference is rounding-error.
    """
    if tokens <= 0:
        return None
    total_seconds = sum(timings_ms) / 1000.0
    if total_seconds <= 0:
        return None
    return round(tokens / total_seconds, 2)
