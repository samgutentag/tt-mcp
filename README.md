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

| Tool            | Purpose                                                                                                   | Inputs                             |
| --------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| `generate`      | Send a prompt, get a completion. Uses OpenAI-compatible `/v1/chat/completions`.                           | `prompt: string`, `model?: string` |
| `list_models`   | List available models. Branches on backend: `/api/tags` for Ollama, `/v1/models` for vLLM/Tenstorrent.    | (none)                             |
| `hardware_info` | Describe the backing hardware and models served. Surfaces `TT_HARDWARE` label and vLLM's `max_model_len`. | (none)                             |

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
TT_HARDWARE="Tenstorrent Wormhole n300 (mock)" \
python scripts/smoke_test.py
```

`hardware_info` will report a 70B model with a 131,072-token context window and
the `TT_HARDWARE` label. The tt-mcp code is identical to the production path;
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

## Companion documents

| File                                                               | Purpose                                                          |
| ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| [`docs/TUTORIAL.md`](docs/TUTORIAL.md)                             | Long-form walkthrough of every file and design choice.           |
| [`docs/PROPOSAL.md`](docs/PROPOSAL.md)                             | One-pager proposing `tt-mcp` as a Tenstorrent ecosystem project. |
| [`docs/diagrams/architecture.svg`](docs/diagrams/architecture.svg) | Architecture diagram (agents, `tt-mcp`, backends).               |
