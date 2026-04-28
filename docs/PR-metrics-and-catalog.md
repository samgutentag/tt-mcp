# PR: hardware-aware metrics, benchmark, and static catalog

> Status: spec, pre-implementation. Lives next to `TUTORIAL.md` and `PROPOSAL.md` so the
> "design before code" trail is in one place.

## Goal

Turn `hardware_info` from "a string label and a context length" into a real picture of the
box: what the silicon physically is, what vLLM is doing on it right now, and how fast it's
actually going. Add two new MCP tools (`metrics`, `benchmark`) along the way.

## Why

Today an agent calling `hardware_info` gets back essentially:

```json
{ "hardware": "Tenstorrent n300", "max_model_len": 131072 }
```

That's a label, not a story. With this PR the agent can answer:

- **Is this box saturated?** — KV cache %, running/waiting queue depth
- **How fast is it right now?** — lifetime tokens / uptime, from `/metrics` counters
- **How fast can it be made to go?** — measured via `benchmark`
- **What's the silicon physically capable of?** — chip family, TFLOPS, DRAM from the catalog
- **Has it been thrashing?** — preemption count, p95 TTFT from histograms

The combination — **static spec × live state × measured throughput** — is the differentiator.
Every other "list models" MCP wrapper stops at the first column.

## Files

### New

#### `tools/_metrics.py`

Internal helper, not registered as an MCP tool. The leading underscore signals "import-only".
Two functions:

- `parse_prometheus(text: str) -> dict` — flatten `vllm:*` counters/gauges to floats; keep
  histograms as `{"buckets": [(le, count), ...], "count": ..., "sum": ...}`.
- `histogram_quantile(hist: dict, q: float) -> float | None` — linear interpolation across
  buckets, matching Prometheus's own `histogram_quantile`. Returns `None` if no observations
  yet (a fresh server has empty histograms; don't crash on it).

Use `prometheus_client.parser.text_string_to_metric_families` for the parse. Add
`prometheus-client` to `requirements.txt` explicitly even if it's a transitive dep —
the tutorial reader shouldn't have to guess where it came from.

#### `tools/hw_catalog.py`

Module-level dict, exact-match lookup on the `TT_HARDWARE` env var:

```python
HARDWARE_SPECS = {
    "Tenstorrent n150": {
        "chip_family": "Wormhole",
        "chip_count": 1,
        "tensix_cores": 108,        # TODO verify against spec sheet
        "dram_gb": 12,
        "dram_type": "GDDR6",
        "peak_bf16_tflops": None,   # TODO verify
        "form_factor": "PCIe card",
        "source": "https://tenstorrent.com/hardware/wormhole",
    },
    # n300, p100, p150, BH LoudBox, BH QuietBox, WH LoudBox/QuietBox, WH Galaxy
}

def lookup(label: str) -> dict:
    if label in HARDWARE_SPECS:
        return HARDWARE_SPECS[label]
    return {"unknown": True, "label": label}
```

Seed one entry per known Tenstorrent product. **Leave any field you can't confirm from a
primary source as `None` with a TODO comment.** A wrong TFLOPS number is worse than no
TFLOPS number — agents will reason from it.

Misses return `{"unknown": True, "label": <value>}` rather than raising, so an unknown box
still gets a useful `hardware_info` payload.

#### `tools/metrics.py`

Registered MCP tool. No inputs. Fetches `/metrics`, runs it through `parse_prometheus`,
returns the dict. Two reasons this is separate from `hardware_info`:

1. Agents that want to do their own analysis can have the raw numbers without `hardware_info`'s
   editorializing.
2. It's the smallest possible unit of "show me the live state" — useful as a tutorial example
   of "MCP tool that wraps a single endpoint with a tiny amount of parsing."

#### `tools/benchmark.py`

Registered MCP tool. Inputs:

| param        | type | default                       | notes                    |
| ------------ | ---- | ----------------------------- | ------------------------ |
| `n`          | int  | `5`                           | number of requests       |
| `prompt`     | str  | `"Say hello in one word."`    | sent unchanged each call |
| `max_tokens` | int  | `8`                           | keep generations short   |

Fires `n` **sequential** `/v1/chat/completions` calls, times each end-to-end, returns:

```json
{
  "n": 5,
  "model": "meta-llama/Llama-3.1-70B-Instruct",
  "e2e_ms": {"p50": 412, "p95": 480, "min": 401, "max": 491},
  "tokens_generated_total": 38,
  "tokens_per_sec": 18.4,
  "errors": 0,
  "note": "sequential; not a load test"
}
```

Sequential is intentional for v1. Concurrency is a v2 feature — sequential numbers are easier
to interpret in a tutorial and won't accidentally turn a demo into a DoS. The `note` field is
there so an agent reading the result doesn't over-claim what was measured.

TTFT measurement requires streaming, which we don't have yet — leave it out of v1 rather than
faking it from total latency. Add it when streaming lands.

### Modified

#### `tools/hardware_info.py`

Replace existing implementation with the merged static × live × server-version response shape.
Skeleton (full version in PR review):

```python
async def hardware_info() -> dict:
    if is_ollama():
        return {"backend": "ollama", "note": "local dev; no TT metrics path"}

    sources = {}
    out = {"hardware": {}, "server": {}, "models": [], "live": {}, "sources": sources}

    # Each fetch wrapped so one failing source doesn't kill the others.
    # sources["health"] = "ok" | "failed: <reason>"
    # ... same for "version", "models", "metrics"

    out["hardware"] = {"label": settings.hardware_label, **hw_catalog.lookup(settings.hardware_label)}
    return out
```

The `sources` field is a first-class part of the response, not a debug afterthought — agents
need to know which signals to trust. If `/metrics` is down but `/v1/models` is up, that's
recoverable information.

#### `scripts/mock_vllm.py`

Add three endpoints so the local demo path exercises every new tool without needing TT silicon:

- `GET /metrics` — Prometheus exposition with at minimum:
  - `vllm:num_requests_running` (gauge)
  - `vllm:num_requests_waiting` (gauge)
  - `vllm:gpu_cache_usage_perc` (gauge)
  - `vllm:gpu_prefix_cache_hit_rate` (gauge)
  - `vllm:num_preemptions_total` (counter)
  - `vllm:prompt_tokens_total` (counter)
  - `vllm:generation_tokens_total` (counter)
  - `vllm:time_to_first_token_seconds` (histogram)
  - `vllm:e2e_request_latency_seconds` (histogram)
- `GET /health` — returns 200 with empty body
- `GET /version` — returns `{"version": "0.6.x-mock"}`

Numbers can be static — the goal is wire-format realism, not a simulation. Fixed values make
the smoke test deterministic.

#### `server.py`

Add `metrics` and `benchmark` to the tool registration list. One line each. Keep the
explicit-list-of-imports pattern.

#### `requirements.txt`

Add `prometheus-client`.

#### `README.md`

Two updates:

1. Two new rows in the Tools table (`metrics`, `benchmark`).
2. New subsection under "Design notes": **Hardware-aware mode** — explain the static × live
   merge and the `sources` field. Should read as a continuation of the existing voice, not
   a bolt-on.
3. Update the mock-vLLM section to mention the new `/metrics`, `/health`, `/version`
   endpoints.

#### `docs/TUTORIAL.md`

Append a section walking through `_metrics.py` (the parser is the most non-obvious piece,
worth a few paragraphs), the catalog file, and the new `hardware_info` shape. Keep the
"read top-to-bottom" feel of the existing tutorial.

## Design choices to call out in the README

- **Static × live merge.** The catalog is hardcoded on purpose. A live spec lookup over the
  network would be slower and could fail; the agent shouldn't wait on a third hop to learn
  what chip it's talking to.
- **Partial failure is a first-class state.** `hardware_info` returns whatever sources
  answered, with a `sources` field saying who responded. Better than an all-or-nothing failure.
- **`metrics` is uncooked, `hardware_info` is plated.** Two tools, two audiences. Don't make
  the agent choose between "raw numbers" and "interpretation" — give it both, separately.
- **Benchmark is sequential.** Concurrency is v2. Sequential numbers are easier to interpret
  and won't turn a tutorial demo into a load test.
- **TTFT is omitted from `benchmark` until streaming lands.** Faking it from total latency
  would be misleading.

## Hardware catalog: what needs verification

For each seeded entry, check tenstorrent.com/hardware and the linked spec sheet PDFs.
Fields most likely to need filling in:

- `peak_bf16_tflops` per chip — not always on the consumer-facing page
- `tensix_cores` for Blackhole parts (different count than Wormhole)
- `interconnect` topology for multi-chip systems (Galaxy mesh especially)
- DRAM split for n300 (24 GB total board, 12 GB per chip — easy to get wrong)

**Leave `None` for anything not on a primary source.** The catalog is only useful if it's
accurate; an agent reading a fabricated TFLOPS number will reason from it.

## Test plan

1. `python scripts/mock_vllm.py` in one terminal.
2. In another:
   ```bash
   export TT_ENDPOINT=http://127.0.0.1:8000
   export TT_API_KEY=mock-key
   export TT_HARDWARE="Tenstorrent n300"
   export TT_MODEL="meta-llama/Llama-3.1-70B-Instruct"
   ```
3. Run `scripts/smoke_test.py` (extend existing or write new) to invoke each tool through
   the MCP protocol:
   - `hardware_info` → returns merged shape with `sources.metrics == "ok"` and a
     non-`None` `chip_family`
   - `metrics` → returns dict containing `vllm:num_requests_running`
   - `benchmark` with `n=3` → numeric timings, `errors == 0`
4. Manual verification through MCP Inspector:
   ```bash
   npx @modelcontextprotocol/inspector .venv/bin/python server.py
   ```
5. Optional: point at a real backend by changing `TT_ENDPOINT` only. No code path should
   differ between mock and real.

## Out of scope (file as next PR)

- Streaming variant of `generate` (unlocks real TTFT in `benchmark`)
- LoRA adapter `/load_lora_adapter` and `/unload_lora_adapter` tools
- Concurrency knob in `benchmark`
- `tokenize` / `detokenize` tools
- `embed` (`/v1/embeddings`)
- Per-model performance catalog ("Llama 3.1 70B on n300 typical tokens/sec")

## Acceptance

- [ ] All four new files created, two existing files modified
- [ ] Smoke test passes against mock_vllm with zero errors
- [ ] MCP Inspector shows `metrics` and `benchmark` registered
- [ ] README and TUTORIAL updates pass a read-aloud test (don't sound bolted on)
- [ ] No fabricated catalog values; everything is sourced or `None` with TODO
