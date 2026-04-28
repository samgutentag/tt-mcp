# tt-mcp

An MCP (Model Context Protocol) server that wraps Tenstorrent inference
endpoints, so any MCP-compatible agent (Claude Desktop, the MCP Inspector, an
IDE plugin) can call Tenstorrent hardware as a first-class tool. Works against a
local Ollama install for development, then flips to a hosted Tenstorrent
endpoint running `tt-inference-server` by changing a single environment
variable.

> Status: v0.1, built as a Tenstorrent DevRel tutorial project by
> [Sam Gutentag](https://samgutentag.com).

## Why this exists

Most MCP servers connect agents to databases, SaaS APIs, or filesystems.
`tt-mcp` connects an agent to **AI accelerator hardware** (Tenstorrent hardware
behind their hosted vLLM deployments) through the same standard protocol. The
point is not novelty; it is that a developer who already knows how to wire up an
MCP tool can now reach real Tenstorrent silicon without learning a new SDK.

## Quickstart

```bash
# 1. Install Ollama and pull a model (macOS)
brew install ollama
brew services start ollama
ollama pull llama3.2:3b

# 2. Clone and install
git clone https://github.com/samgutentag/tt-mcp.git
cd tt-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Copy the example config (defaults already point at local Ollama)
cp .env.example .env

# 4. Run it standalone to verify
python server.py
# ... then Ctrl-C; the server waits for an MCP host to talk to it over stdio.

# 5. Test through the MCP Inspector (requires Node)
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

## Tools

| Tool            | Purpose                                                                                                                         | Inputs                                                          |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `generate`      | Send a prompt, get a completion. Uses OpenAI-compatible `/v1/chat/completions`.                                                 | `prompt: string`, `model?: string`                              |
| `list_models`   | List available models. Branches on backend: `/api/tags` for Ollama, `/v1/models` for vLLM/Tenstorrent.                          | (none)                                                          |
| `hardware_info` | Static spec × live state × server. Merges the hardware catalog, parsed `/metrics`, and `/version` into one dict with a `sources` ledger. | (none)                                                          |
| `metrics`       | Raw vLLM Prometheus state, parsed. Returns the full `vllm:*` family as JSON; histograms come back as `{buckets, count, sum}`.   | (none)                                                          |
| `benchmark`     | Sequential end-to-end completion timing. Returns min/max/p50/p95 latency and an averaged tokens/second.                          | `n?: int=5`, `prompt?: string`, `max_tokens?: int=8`            |

Each tool lives in its own file under `tools/`. `server.py` imports and
registers them in an explicit list. Adding a new tool is one file plus one line
in `server.py`.

## Configuration

All runtime config lives in four environment variables, loaded from `.env` via
`python-dotenv` in local dev or from the orchestrator in production.

| Variable      | Default                  | Notes                                                                  |
| ------------- | ------------------------ | ---------------------------------------------------------------------- |
| `TT_ENDPOINT` | `http://localhost:11434` | Base URL. No trailing slash needed.                                    |
| `TT_MODEL`    | `llama3.2:3b`            | Default model when a tool call omits it.                               |
| `TT_API_KEY`  | _(empty)_                | Bearer token; leave blank for Ollama.                                  |
| `TT_HARDWARE` | _(empty)_                | Label surfaced by `hardware_info`, e.g. `"Tenstorrent Wormhole n300"`. |

### Swapping backends

| Target                        | `TT_ENDPOINT`                      | `TT_API_KEY` |
| ----------------------------- | ---------------------------------- | ------------ |
| Ollama (local dev)            | `http://localhost:11434`           | _empty_      |
| Mock vLLM (see below)         | `http://127.0.0.1:8000`            | _any value_  |
| Hosted Tenstorrent deployment | `https://<your-hosted-deployment>` | required     |
| Any OpenAI-compatible vLLM    | `http://<host>:<port>`             | as needed    |

### Demoing the remote path locally

`scripts/mock_vllm.py` is a zero-dependency mock that speaks vLLM's wire format
with Tenstorrent-flavored metadata. Use it to exercise the OpenAI-compatible
code path without access to real hardware.

```bash
# Terminal 1
python scripts/mock_vllm.py                          # listens on :8000

# Terminal 2
TT_ENDPOINT=http://127.0.0.1:8000 \
TT_API_KEY=mock-key \
TT_MODEL="meta-llama/Llama-3.1-70B-Instruct" \
TT_HARDWARE="Tenstorrent n300" \
python scripts/smoke_test.py
```

The mock implements the routes every tool needs:

| Route                     | Used by                              | Behaviour                                              |
| ------------------------- | ------------------------------------ | ------------------------------------------------------ |
| `GET /v1/models`          | `list_models`, `hardware_info`       | Returns one 70B model with `max_model_len: 131072`.    |
| `POST /v1/chat/completions` | `generate`, `benchmark`            | Returns an obvious mock completion, OpenAI-shaped.     |
| `GET /metrics`            | `metrics`, `hardware_info`           | Static Prometheus exposition (gauges + histograms).    |
| `GET /health`             | `hardware_info`                      | 200 with empty body (vLLM's ready signal).             |
| `GET /version`            | `hardware_info`                      | `{"version": "0.6.x-mock"}`.                           |

Numbers in `/metrics` are deliberately fixed so the smoke test is deterministic.
`hardware_info` will report all sources as `"ok"`, the `n300` catalog entry,
the model and context window, and live numbers like `kv_cache_usage: 0.42` and
`p95_e2e_latency_s: 5.0`. The tt-mcp code is identical to the production path;
only `TT_ENDPOINT` changes.

## Connecting to Claude Desktop

Add this block to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

> Use the `.venv` Python explicitly, because Claude Desktop's environment may
> not have the project's dependencies on its default `python3`.

```json
{
  "mcpServers": {
    "tt-mcp": {
      "command": "/absolute/path/to/tt-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/tt-mcp/server.py"],
      "env": {
        "TT_ENDPOINT": "http://localhost:11434",
        "TT_MODEL": "llama3.2:3b"
      }
    }
  }
}
```

<details>
<summary>One-liner to generate this with your path filled in (run from inside the repo)</summary>

```bash
sed "s|/absolute/path/to/tt-mcp|$(pwd)|g" <<'EOF' | pbcopy
{
  "mcpServers": {
    "tt-mcp": {
      "command": "/absolute/path/to/tt-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/tt-mcp/server.py"],
      "env": {
        "TT_ENDPOINT": "http://localhost:11434",
        "TT_MODEL": "llama3.2:3b"
      }
    }
  }
}
```

The corrected JSON is now on your clipboard. Paste it into
`~/Library/Application Support/Claude/claude_desktop_config.json`.

</details>

## Design notes

The code is intended to be read top-to-bottom as tutorial content. A few choices
worth calling out:

- **One endpoint, three targets.** The OpenAI-compatible wire format means the
  same `generate` implementation works against Ollama, vLLM, and Tenstorrent's
  hosted deployments with no code changes.
- **The abstraction leaks at `list_models`.** Ollama uses `/api/tags`, vLLM uses
  `/v1/models`, and the payload shapes differ. That is handled explicitly rather
  than hidden. See `config.is_ollama` and the branch in `list_models`.
- **Errors are returned, not raised.** A string response flows through to the
  calling model, which can reason about the failure. A raised exception becomes
  an opaque MCP protocol error that the agent cannot recover from cleanly.
- **Stdout is sacred.** MCP stdio servers must not print anything to stdout that
  is not a JSON-RPC frame. All diagnostics go through the `logging` module,
  which writes to stderr.

### Hardware-aware mode

`hardware_info` doesn't query a single endpoint; it merges three signals so an
agent gets a real picture of the box in one call:

- **Static spec** from `tools/hw_catalog.py`. Hardcoded silicon facts (chip
  family, DRAM, Tensix cores) keyed by exact match on `TT_HARDWARE`. The
  catalog is intentionally cautious, anything not on a primary source is
  `None` with a `# TODO verify` comment. A wrong TFLOPS number is worse than
  no TFLOPS number, an agent will reason from it.
- **Live state** from `/metrics`. KV cache utilisation, queue depth,
  preemption count, p95 latency, lifetime token counts. Parsed by
  `tools/_metrics.py`, which also provides a Prometheus-compatible
  `histogram_quantile`.
- **Server identity** from `/version` and `/v1/models`. vLLM minor and the
  models being served, with their context windows.

Every fetch is wrapped, so one failing source doesn't kill the others. The
response carries a `sources` ledger telling the caller who answered and who
didn't:

```json
"sources": {
  "health": "ok",
  "version": "ok",
  "models": "ok",
  "metrics": "failed: HTTP 503"
}
```

Partial failure is a first-class state. An agent that sees `metrics` is down
but `models` came through can still reason about what to do next, and won't
silently treat a missing `kv_cache_usage` as "unknown" when the real cause is
"the source was unreachable".

Two tools, two audiences:

- `metrics` is uncooked. Full `vllm:*` family, parsed but not interpreted.
  For agents that want to do their own analysis or cherry-pick gauges.
- `hardware_info` is plated. Hand-picked signals with short human-readable
  names (`running_requests`, not `vllm:num_requests_running`) merged with
  the static catalog and server version.

Don't make the agent choose between "raw numbers" and "interpretation", give
it both, separately.

## Companion documents

| File                                                               | Purpose                                                          |
| ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| [`docs/TUTORIAL.md`](docs/TUTORIAL.md)                             | Long-form walkthrough of every file and design choice.           |
| [`docs/PROPOSAL.md`](docs/PROPOSAL.md)                             | One-pager proposing `tt-mcp` as a Tenstorrent ecosystem project. |
| [`docs/diagrams/architecture.svg`](docs/diagrams/architecture.svg) | Architecture diagram (agents, `tt-mcp`, backends).               |
