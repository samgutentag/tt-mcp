"""Prometheus exposition parsing helpers.

Underscored module name because nothing outside the `tools/` package should
import from here. Two consumers in-tree: `tools/metrics.py` (the registered
tool that returns the parsed dict directly) and `tools/hardware_info.py`
(which folds a few key signals into its merged response).

Two functions, both pure:

  - `parse_prometheus(text)` flattens Prometheus exposition into a dict the
    rest of the server can hand to an LLM without further reshaping.
  - `histogram_quantile(hist, q)` reproduces Prometheus's own
    `histogram_quantile` with linear interpolation across buckets.

Why a parser at all, instead of forwarding the raw text? Two reasons. First,
agents reason better over a JSON-ish structure than over Prometheus's
line-oriented format. Second, vLLM emits a few hundred lines of metrics; a
flat dict lets a tool pluck out `vllm:num_requests_running` without the
caller learning the format.

Why prometheus_client's parser instead of a hand-rolled one? It already
handles the awkward parts (label-set canonicalisation, escaped strings,
NaN/Inf, stale-marker semantics) and is a dep we already have transitively
through MCP. Adding it explicitly to requirements.txt is cosmetic; the
weight on disk is the same.
"""

from __future__ import annotations

import math
from typing import Any

from prometheus_client.parser import text_string_to_metric_families


def parse_prometheus(text: str) -> dict[str, Any]:
    """Flatten a Prometheus exposition blob to a dict.

    Each metric family becomes one key. Counters and gauges resolve to a
    single float (or a `{labels: float}` map if the family carries labels).
    Histograms become `{"buckets": [(le, count), ...], "count": ..., "sum": ...}`,
    which is the shape `histogram_quantile` consumes.

    Args:
        text: Raw text body of a `/metrics` response.

    Returns:
        A dict keyed by metric family name. Empty dict for empty input,
        never raises on malformed input (returns whatever it could parse).
    """
    out: dict[str, Any] = {}

    for family in text_string_to_metric_families(text):
        if family.type == "histogram":
            # Histogram family names match the on-the-wire metric name
            # (the `_bucket`, `_count`, `_sum` suffixes live on the samples,
            # not the family). Key the result on the family name directly.
            out[family.name] = _collapse_histogram(family.samples)
            continue

        # Counters and gauges. The parser strips `_total` from counter
        # *family* names but keeps it on samples, so we key by sample name
        # instead. That way `vllm:prompt_tokens_total` round-trips as
        # written instead of becoming `vllm:prompt_tokens`. We also drop
        # `_created` metadata samples, agents don't care when a counter
        # was first registered.
        groups: dict[str, dict[str, float]] = {}
        for sample in family.samples:
            if sample.name.endswith("_created"):
                continue
            groups.setdefault(sample.name, {})[_label_key(sample.labels)] = sample.value

        for name, values in groups.items():
            if len(values) == 1 and "" in values:
                # Common case: one unlabelled sample. Surface it as a bare
                # float (e.g. `vllm:num_requests_running` -> 3.0).
                out[name] = values[""]
            else:
                # Labels in play; keep the per-label-set values so agents
                # can disambiguate per-model or per-finish-reason series.
                out[name] = values

    return out


def histogram_quantile(hist: dict[str, Any], q: float) -> float | None:
    """Estimate the q-quantile of a histogram, Prometheus-style.

    Mirrors Prometheus's own `histogram_quantile`: linearly interpolate
    inside the bucket where the cumulative count crosses `q * total`.
    Returns `None` when there are no observations yet, which is what a
    fresh server with empty histograms looks like, callers should not
    crash on that.

    Args:
        hist: A dict shaped `{"buckets": [(le, count), ...], "count": ..., "sum": ...}`,
            i.e. the value `parse_prometheus` returns for a histogram family.
        q: Quantile in `[0, 1]`. e.g. 0.95 for p95.

    Returns:
        Estimated quantile in the same unit as the histogram (e.g. seconds
        for `vllm:e2e_request_latency_seconds`), or `None` if there is
        nothing to estimate from yet.
    """
    if not 0 <= q <= 1:
        raise ValueError(f"quantile must be in [0, 1], got {q}")

    buckets = hist.get("buckets") or []
    total = hist.get("count") or 0
    if not buckets or total <= 0:
        return None

    # Buckets in Prometheus are cumulative and sorted by upper bound. The
    # last bucket is `+Inf`. We're looking for the first one whose cumulative
    # count is >= q * total.
    target = q * total
    prev_le = 0.0
    prev_count = 0.0

    for le, count in buckets:
        if count >= target:
            if math.isinf(le):
                # Target falls in the +Inf bucket. Best we can do is return
                # the previous finite bound; we have no upper limit to
                # interpolate against.
                return prev_le
            # Linear interpolation inside the bucket. Same formula as
            # Prometheus: assume observations are uniformly distributed
            # between the previous upper bound and this one.
            span = count - prev_count
            if span <= 0:
                return le
            fraction = (target - prev_count) / span
            return prev_le + fraction * (le - prev_le)
        prev_le = le if not math.isinf(le) else prev_le
        prev_count = count

    # Shouldn't get here if the histogram includes a +Inf bucket, which
    # well-formed Prometheus output always does. Fall back to the highest
    # finite bound rather than raising.
    return prev_le or None


def _collapse_histogram(samples) -> dict[str, Any]:
    """Reassemble bucket / sum / count samples into one dict.

    The text parser yields one sample per bucket plus `_sum` and `_count`
    siblings. We need them as one structure to compute quantiles, so this
    helper walks the sample stream and groups them.
    """
    buckets: list[tuple[float, float]] = []
    total_count = 0.0
    total_sum = 0.0

    for sample in samples:
        if sample.name.endswith("_bucket"):
            le_label = sample.labels.get("le")
            if le_label is None:
                continue
            le = float("inf") if le_label == "+Inf" else float(le_label)
            buckets.append((le, sample.value))
        elif sample.name.endswith("_count"):
            total_count = sample.value
        elif sample.name.endswith("_sum"):
            total_sum = sample.value
        # Silently drop `_created` and any other metadata samples.

    buckets.sort(key=lambda b: b[0])
    return {"buckets": buckets, "count": total_count, "sum": total_sum}


def _label_key(labels: dict[str, str]) -> str:
    """Canonicalise a label set to a stable string key.

    Empty dict becomes `""` (the unlabelled case). Otherwise keys are
    sorted and joined as `k=v,k=v`. Sorting matters: Prometheus does not
    guarantee insertion order, and we want `{model="x",finished="stop"}`
    to hash identically to `{finished="stop",model="x"}`.
    """
    if not labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


if __name__ == "__main__":
    # Self-test: feed in a known-good blob and assert the parsed shape.
    # Run with: python -m tools._metrics
    sample = """\
# HELP vllm:num_requests_running Number of requests currently running.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running 3.0
# HELP vllm:gpu_cache_usage_perc KV cache usage as a fraction.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc 0.42
# HELP vllm:prompt_tokens_total Total prompt tokens processed.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total 12345.0
# HELP vllm:e2e_request_latency_seconds End to end request latency.
# TYPE vllm:e2e_request_latency_seconds histogram
vllm:e2e_request_latency_seconds_bucket{le="0.1"} 1.0
vllm:e2e_request_latency_seconds_bucket{le="0.5"} 4.0
vllm:e2e_request_latency_seconds_bucket{le="1.0"} 8.0
vllm:e2e_request_latency_seconds_bucket{le="+Inf"} 10.0
vllm:e2e_request_latency_seconds_count 10.0
vllm:e2e_request_latency_seconds_sum 5.5
"""

    parsed = parse_prometheus(sample)
    assert parsed["vllm:num_requests_running"] == 3.0, parsed["vllm:num_requests_running"]
    assert parsed["vllm:gpu_cache_usage_perc"] == 0.42
    # Counters are emitted as `<name>_total` samples but the family name keeps
    # the suffix in vLLM's case. The parser collapses to a single bare float.
    assert parsed["vllm:prompt_tokens_total"] == 12345.0

    hist = parsed["vllm:e2e_request_latency_seconds"]
    assert hist["count"] == 10.0
    assert hist["sum"] == 5.5
    assert len(hist["buckets"]) == 4

    # p50: half of 10 = 5 obs. Cumulative hits 8 at le=1.0 (between 0.5 and 1.0).
    # Linear interp: at 0.5 we have 4, at 1.0 we have 8, target 5 -> 0.5 + (1/4)*0.5 = 0.625
    p50 = histogram_quantile(hist, 0.5)
    assert p50 is not None and abs(p50 - 0.625) < 1e-9, p50

    # Empty histogram returns None rather than raising.
    empty = {"buckets": [], "count": 0, "sum": 0}
    assert histogram_quantile(empty, 0.95) is None

    print("ok: parse_prometheus + histogram_quantile self-test passed")
