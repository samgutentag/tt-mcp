# Building tt-mcp: Making Tenstorrent Inference a First-Class Tool for AI Agents

> A tutorial submitted as part of the Developer Relations Engineer, Tools
> interview at Tenstorrent, by [Sam Gutentag](https://samgutentag.com).

---

## Submission note

**Why I chose this topic.** The role's goal is reducing friction between
developers and Tenstorrent hardware through tools that meet developers where
they already are. AI agents using the Model Context Protocol are increasingly
where modern developer work happens, and the on-ramp from that world to
Tenstorrent inference today is thin. Building `tt-mcp` let me exercise every
part of the tech stack a Tools engineer would work across (Python, a wire
protocol, a small HTTP service, a docs artifact, a demo script, and a proposal)
while producing something genuinely useful to the ecosystem.

**Audience and takeaway.** This tutorial targets a mid-to-advanced Python
developer who has heard of MCP but has not built a server, or who has built one
against databases and APIs but not against accelerator hardware. The takeaway is
threefold:

1. a working MCP server that Claude Desktop recognises as a Tenstorrent endpoint
2. a mock vLLM backend that lets the remote code path be exercised without
   privileged access
3. a proposal doc (`PROPOSAL.md`) that lays out the ecosystem fit

## How I used AI to build

I used Claude Code collaboratively throughout the project. The code was drafted
through conversation and reviewed file by file; the prose was drafted by me and
tightened against my style rules; the judgment calls about what belongs in v0.1
are mine. I mention this because the prompt asks for transparency on AI tool
use, and because it is itself a DevRel-Tools-relevant practice: using agents
well, with clear ownership of decisions, is the kind of workflow I want to help
more developers do.

---

## Define the problem

If you want an AI agent to run SQL, read a file, search the web, or post to a
chat app, the Model Context Protocol ("MCP") gives you a single standard for
wiring external tools into any compatible host. That list is growing: Claude
Desktop, the MCP Inspector, VS Code plugins, and most of today's agent
frameworks.

If you want that same agent to call a model running on AI accelerator hardware,
the path is less worn. You can talk to hosted APIs. You can run things locally
with Ollama or llama.cpp. But reaching a _specific_ piece of silicon, say a
Tenstorrent Wormhole or Blackhole chip serving a real workload, usually means
learning a new CLI, a vendor SDK, or a bespoke gRPC client. That "friction gap"
is exactly where this project sits.

`tt-mcp` is a small MCP server that wraps an inference endpoint so any
MCP-compatible agent can call it as a first-class tool. In local dev it points
at Ollama, which is fine for iterating on the tool surface itself. In
production, the same code is ready to point at a vLLM endpoint running on
Tenstorrent hardware, with just one environment variable changed. The
abstraction that makes that swap straightforward is that all of these backends
already speak the same OpenAI-compatible format.

---

## What you will build

A Python MCP server with three tools.

> **What's a "tool" in MCP?** A named function the host (Claude Desktop, an IDE
> agent, the Inspector) can call on your server. Each tool has a description,
> typed arguments, and a return value. Your server exposes them; the host
> decides when to invoke them based on the user's prompt and the tool
> descriptions you wrote.

| Tool            | Purpose                                              |
| --------------- | ---------------------------------------------------- |
| `generate`      | Chat completion over any OpenAI-compatible endpoint. |
| `list_models`   | What the backend serves.                             |
| `hardware_info` | Endpoint description and backing accelerator label.  |

By the end you will have the server running against local Ollama, a small mock
vLLM you can start with `python scripts/mock_vllm.py` to demonstrate the remote
path without needing any real hardware, and a working Claude Desktop
configuration that shows the tools in the agent.

> **What's vLLM?** An open-source inference server that's become the de-facto
> way to serve large language models at scale. It exposes an HTTP API compatible
> with OpenAI's `/v1/chat/completions` and `/v1/models` routes, which is the
> wire format `tt-mcp` targets. Tenstorrent's `tt-inference-server` uses vLLM as
> its serving layer on top of Tenstorrent hardware.

Source:
[https://github.com/samgutentag/tt-mcp](https://github.com/samgutentag/tt-mcp)

### Architecture

```text
   Developer or AI Agent
           │
           │  MCP tool call (JSON-RPC over stdio)
           ▼
    ┌─────────────────┐
    │   tt-mcp server │      Python, FastMCP, httpx
    │   (server.py)   │
    └────────┬────────┘
             │
             │  POST /v1/chat/completions
             │  GET  /v1/models
             ▼
   ┌──────────────────────┐      ┌──────────────────────┐
   │ Ollama (local dev)   │  or  │ vLLM on Tenstorrent  │
   │ http://localhost     │      │ hardware, via the    │
   │  :11434              │      │ tt-inference-server  │
   └──────────────────────┘      │ stack                │
                                 └──────────────────────┘
```

See the architecture SVG at
[`diagrams/architecture.svg`](diagrams/architecture.svg) for a rendered version
with all three backend lanes (local Ollama, a local mock vLLM for demos, and a
hosted Tenstorrent deployment).

The three backends are interchangeable at the HTTP boundary. `tt-mcp` does not
know or care which one it is talking to, with one exception: model discovery.
Ollama exposes a `/api/tags` route that is not part of the OpenAI-compatible
spec. vLLM uses `/v1/models` instead, and the two return different JSON shapes.
That single divergence is handled in `tools/models.py`, which is the only file
in the codebase that has to know what kind of backend it is talking to.
Everywhere else, the HTTP contract is uniform.

---

## Part 1: MCP fundamentals, just enough to get started

MCP defines a contract between a _host_ (e.g. Claude Desktop) and a _server_
(your code) that exposes tools, resources, and prompts. For this tutorial we
only care about tools.

Three things are worth knowing before you start writing code.

**Transports.** MCP supports stdio, Server-Sent Events, and streamable HTTP.
Stdio is what you will use for local hosts like Claude Desktop, because it gives
you zero network exposure, no port to manage, and no authentication surface to
design: the host spawns your server as a subprocess and pipes JSON-RPC over
stdin and stdout. HTTP transports are the right pick when one server needs to
serve many clients, but that is not the shape of a developer's agent talking to
a local tool. Anything you `print()` to stdout that is not a JSON-RPC frame
corrupts the protocol.

**The handshake.** Every session begins with `initialize` from the client, a
response from the server, then a `notifications/initialized` message from the
client. Only after that are `tools/list` and `tools/call` accepted. If your
server looks hung on startup, this is usually the culprit.

> **Server appears hung?** Four things to check, in order:
>
> 1. **Read stderr.** FastMCP logs to stderr and Claude Desktop buries it at
>    `~/Library/Logs/Claude/mcp*.log` on macOS. `tail -f` that file while you
>    reconnect and you will see the problem.
> 2. **Run `scripts/smoke_test.py`.** It drives the same handshake without a UI.
>    If the smoke test passes, the problem is in the host config, not your
>    server.
> 3. **Make sure nothing writes to stdout except FastMCP.** A stray `print()` or
>    an import that writes a banner (`urllib3`, some progress bars) will corrupt
>    the protocol. Route everything to `logging` with `stream=sys.stderr`.
> 4. **Confirm the client sent `notifications/initialized`.** Most hosts do this
>    automatically, but if you are writing your own client (or debugging one), a
>    missing notification freezes the server indefinitely.

**Tool descriptions are for two audiences.** Write docstrings for both. Spell
out inputs, outputs, and when to pick this tool over an alternative. Blank or
sloppy descriptions silently degrade tool choice accuracy in real agent loops,
and the failure mode is "the agent never calls it" rather than a visible error.

---

## Part 2: Setting up the project

About five minutes from a blank Mac to a working MCP server. The block below
installs Ollama, pulls a small model (~2 GB), clones the repo, creates a virtual
environment, installs three Python dependencies, and seeds the config. At the
end, you have a server that can be driven from the smoke test, the MCP
Inspector, or Claude Desktop. Nothing is irreversible; `brew uninstall ollama`
and `rm -rf` on the folder get you back to where you started.

**Prerequisites**

- **Python 3.11+** (the code uses `X | None` union types and modern async
  idioms).
- **Homebrew** for Ollama on macOS. On Linux, use the Ollama install script from
  [ollama.com/download](https://ollama.com/download).
- **Node** only if you want to run the MCP Inspector. The server and smoke test
  need nothing from Node.

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2:3b

git clone https://github.com/samgutentag/tt-mcp.git
cd tt-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # defaults work with local Ollama out of the box
```

Three runtime dependencies: `mcp`, `httpx`, `python-dotenv`. That is all.

Four environment variables control everything. The defaults match local Ollama,
so nothing is required to run against `llama3.2:3b`.

| Variable      | Description                                                                    | Required                    | Default                  |
| ------------- | ------------------------------------------------------------------------------ | --------------------------- | ------------------------ |
| `TT_ENDPOINT` | Base URL of the inference endpoint. No trailing slash.                         | No                          | `http://localhost:11434` |
| `TT_MODEL`    | Default model identifier. Individual tool calls can override it.               | No                          | `llama3.2:3b`            |
| `TT_API_KEY`  | Bearer token for authenticated endpoints.                                      | Only for hosted deployments | _(empty)_                |
| `TT_HARDWARE` | Human-readable label for the backing accelerator, surfaced by `hardware_info`. | No                          | _(empty)_                |

---

## Part 3: Building the server

I will walk through the files in the order I wrote them, because that is the
order I would read them. The whole server is under 400 lines including comments.

### `config.py`

A frozen dataclass that reads env vars once at startup. One field per concern.
`load_dotenv` pulls values from a local `.env` file if one exists; if not, the
process environment wins. Same code path for local dev and for production.

```python
@dataclass(frozen=True)
class Config:
    endpoint: str
    model: str
    api_key: str | None
    hardware: str | None

    @classmethod
    def from_env(cls) -> "Config":
        endpoint = os.environ.get(
            "TT_ENDPOINT", "http://localhost:11434"
        ).rstrip("/")
        model = os.environ.get("TT_MODEL", "llama3.2:3b")
        api_key = os.environ.get("TT_API_KEY") or None
        hardware = os.environ.get("TT_HARDWARE") or None
        return cls(endpoint=endpoint, model=model,
                   api_key=api_key, hardware=hardware)
```

Two properties earn their keep. `is_ollama` is a URL-based heuristic that
decides whether to hit `/api/tags` or `/v1/models`. It is not perfect, but it is
cheap and honest. `auth_headers` returns `{}` when there is no API key, so we
can pass it unconditionally to every request and let it no-op locally.

**env vars over a config file:** MCP hosts spawn servers as subprocesses and
pass configuration through an `env` block in their own config. That is the one
channel every host exposes, so env-first is the path of least surprise. A JSON
or YAML config file would feel natural but would need host-specific loader code,
which is exactly what MCP is trying to avoid.

### `tools/generate.py`

The headline tool. Sends a user message to `/v1/chat/completions` and returns
the assistant reply as plain text.

```python
def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def generate(prompt: str, model: str | None = None) -> str:
        """Generate text from the configured inference endpoint.

        Sends `prompt` as a single-user-message chat completion request
        to the OpenAI-compatible `/v1/chat/completions` route. Works
        against Ollama, vLLM, and any other backend that speaks the
        same wire format, which includes Tenstorrent's hosted vLLM
        deployments.
        ...
        """
```

Three details in this file are worth flagging.

**The docstring is the model's instruction manual.** Every non-trivial agent
tool-choice failure I have seen traces back to a thin description. Args,
returns, the "when to use this" sentence, all of it is seen by the LLM at
`tools/list` time. Write it like onboarding documentation for a new hire.

**Errors are returned, not raised.** If the endpoint is unreachable or the model
does not exist, we return a short string describing the problem. An agent can
read that and decide what to do. A raised exception becomes an opaque MCP-level
error the agent cannot introspect, and the user just sees "tool failed" with no
breadcrumb.

**One `AsyncClient` per call, not a module singleton.** For a short-lived stdio
subprocess this costs essentially nothing, and it avoids a class of event-loop
lifecycle bugs that show up when FastMCP users try to share a client across
requests. If this ever grew into a long-running HTTP deployment the answer would
change; for v0.1 it is the right trade.

### `tools/models.py`

The one place we handle backend-specific differences. Ollama exposes
`{"models": [...]}` at `/api/tags`. vLLM exposes `{"data": [...]}` at
`/v1/models`. Different route, different payload shape, different field names
(`name` vs `id`).

```python
if config.is_ollama:
    url = f"{config.endpoint}/api/tags"
    names = [m["name"] for m in data.get("models", [])]
else:
    url = f"{config.endpoint}/v1/models"
    names = [m["id"] for m in data.get("data", [])]
```

I chose to put the branch in one place and keep it visible, rather than hiding
it behind a "Backend" adapter class (a common pattern where each supported
backend gets its own subclass that conforms to a shared interface, letting
callers stay ignorant of which backend is actually wired up). An adapter would
look cleaner in the file tree, but it moves the divergence from one file to
three, and makes the tutorial read as a framework rather than a tool. In a
teaching codebase the branch is the better call.

### `tools/hardware_info.py`

This tool answers "what is actually serving these tokens?" which matters the
moment an agent starts choosing between cheap local inference and real
accelerator hardware.

The tool branches:

- Against Ollama it returns a teaching placeholder that explains what the tool
  would show against real hardware and how to point the server there. That is
  more useful than pretending, and an agent can still reason about the response.
- Against a vLLM endpoint it merges three signals (a static spec catalog,
  parsed `/metrics`, and `/version` plus `/v1/models`) into one response with
  a `sources` ledger so the agent knows which signals to trust. Part 6 walks
  through how that merge is built.

`TT_HARDWARE` being operator-declared rather than probed is a deliberate
honesty. vLLM's OpenAI-compatible routes do not expose hardware telemetry.
Rather than invent a label, the tool states what it knows, fills in what it
can from the static catalog, and lets the response carry partial-failure
information when a source is down.

### `server.py`

The entry point. Four responsibilities: configure logging to stderr, build the
FastMCP instance, register every tool module, and run.

```python
mcp = FastMCP(name="tt-mcp", instructions="...")

generate.register(mcp)
models.register(mcp)
hardware_info.register(mcp)
metrics.register(mcp)
benchmark.register(mcp)

if __name__ == "__main__":
    mcp.run()
```

That explicit `register(mcp)` sequence is the design decision I would defend
most firmly. It is slightly more code than decorator-based auto-discovery, and
the payoff is that "which tools does this build expose?" is a one-file question
with a five-line answer.

> For a tutorial codebase I will take explicit over clever every time.

### Adding a sixth tool

The `register(mcp)` pattern earns its keep every time you add a new tool: two
edits and you are done. Create a new file under `tools/` that exports
`register(mcp)`, then add one line to `server.py`. No registry, no loader, no
plugin hook. Going from three tools to five (adding `metrics` and `benchmark`,
and pulling `hardware_info` out of its old shared file) was three new lines.

---

## Part 4: Testing it

Two verification paths, depending on your preference.

**The smoke test.** `scripts/smoke_test.py` drives the server through a full
JSON-RPC stdio handshake without any UI. It issues `initialize`,
`notifications/initialized`, `tools/list`, and a `tools/call` for each tool,
asserting the tool descriptions are non-empty and the responses look sane. Good
for CI, good for you when you are iterating on a tool and do not want to open a
browser every time.

```bash
python scripts/smoke_test.py
```

Expected output ends with `✓ smoke test passed`.

**The MCP Inspector.** A browser UI that lists your tools, lets you call them
with arbitrary arguments, and shows the raw JSON-RPC traffic. Installed on
demand via `npx`, no lasting footprint.

```bash
npx @modelcontextprotocol/inspector \
  /absolute/path/to/tt-mcp/.venv/bin/python \
  /absolute/path/to/tt-mcp/server.py
```

Two walls I hit the first time.

> **Use the URL the CLI prints, not `localhost:6274`.** Recent versions of the
> Inspector put an authentication token on the proxy. If you browse to the bare
> URL, you will see a "Connection Error: Did you add the proxy session token in
> Configuration?" message. The CLI's stderr output includes a URL that looks
> like `http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=...`. Click that one. It
> opens a session with the token already set.

> **Use the absolute path to `.venv/bin/python`, not bare `python`.** `npx`
> spawns the server in a fresh shell with no venv activated, so `python` on its
> PATH will not have `mcp`, `httpx`, or `python-dotenv` installed. Pointing at
> `.venv/bin/python` sidesteps the problem.

Once you are in, click "Connect," then "Tools." You should see the three tools
listed with their descriptions. Click one, fill in the arguments, and watch the
JSON-RPC request and response fly by in the bottom pane. This is the fastest way
to answer "what does the host actually see from my server?"

### Wiring into Claude Desktop

Add this block to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

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

> **Both `/absolute/path/to/tt-mcp/...` strings are placeholders.** Replace them
> with the real path on your machine. Claude Desktop does not expand `~` or
> `$HOME`, and it will silently fail to start the server if the path is wrong.
> From the repo root, run `pwd` to get the absolute path, then paste it into
> both lines. Use the `.venv/bin/python` path specifically; Claude Desktop's
> default `python3` will not have the project's dependencies on it.

Restart Claude Desktop. You should see the three tools in the tools list, and a
prompt like _"Ask the local `tt-mcp` what models it has"_ will cause the agent
to call `list_models` without any further scaffolding on your part.

---

## Part 5: The remote seam

Tenstorrent's inference stack (see `tt-inference-server` in the public GitHub
org) integrates with vLLM, which exposes the OpenAI-compatible routes we target.
The exact URL and credentials for a production deployment are not something I
can demonstrate in a public tutorial, and during this build I did not have
access to a hosted Tenstorrent endpoint. Rather than hand-wave, the repo ships a
small mock backend that speaks the same wire format and lets you exercise the
exact code path production would take.

### Running the mock vLLM backend locally

```bash
python scripts/mock_vllm.py               # listens on 127.0.0.1:8000
```

Then point `tt-mcp` at it:

```bash
TT_ENDPOINT=http://127.0.0.1:8000 \
TT_API_KEY=mock-key \
TT_MODEL="meta-llama/Llama-3.1-70B-Instruct" \
TT_HARDWARE="Tenstorrent Wormhole n300 (mock)" \
python scripts/smoke_test.py
```

What you will see is that `hardware_info` now reports a 70B model with a
131,072-token context window and the Tenstorrent hardware label. The `tt-mcp`
code did not change. The endpoint did. That is the entire story of the
abstraction working.

### What production looks like

The same Claude Desktop `env` config block, with four substitutions:

| Variable      | Local mock                          | Production (example shape)                               |
| ------------- | ----------------------------------- | -------------------------------------------------------- |
| `TT_ENDPOINT` | `http://127.0.0.1:8000`             | `https://<your-hosted-deployment>`                       |
| `TT_API_KEY`  | `mock-key`                          | bearer token from the deploy platform                    |
| `TT_MODEL`    | `meta-llama/Llama-3.1-70B-Instruct` | whatever the deployed vLLM serves                        |
| `TT_HARDWARE` | `Tenstorrent Wormhole n300 (mock)`  | the accelerator label (e.g. `Tenstorrent Wormhole n300`) |

No code change, no library swap, no redeploy. You set four env vars, restart
Claude Desktop, and the agent is now calling Tenstorrent hardware.

### Where this still has room to grow

The mock is a single file, and the extensibility points in `tt-mcp` are
intentionally obvious rather than clever. If you wanted to build this out, the
seams are:

1. **Streaming.** `generate` currently returns the full response. A streaming
   variant would use MCP's `Context.report_progress` and the OpenAI SSE shape.
2. **Authentication.** Bearer token today, happy to grow into signed requests,
   short-lived credentials, or whatever Tenstorrent's hosted platform
   standardises on.
3. **A real hardware probe.** When Tenstorrent exposes an accelerator metadata
   route, the `hardware_info` tool swaps its operator-declared label for a real
   signal. Today's version degrades gracefully either way.
4. **Discovery.** Right now the operator tells the server where to point. A
   registry or discovery protocol for "available Tenstorrent endpoints this
   developer has access to" would be the obvious next step if `tt-mcp` became a
   proper ecosystem tool.

None of these are blocked on architecture. The current shape leaves room for
each of them.

---

## Part 6: Hardware-aware mode

`hardware_info` in v0.1 returned a label and a context window. Useful, but
thin. An agent calling it learned that the box was a "Tenstorrent n300" and
that the served model accepts 131,072 tokens of context, and that was it. The
agent could not answer "is the box busy?", "how fast is it actually going?",
or "what's the silicon physically capable of?" without writing more code.

v0.2 fixes that with a three-source merge: a static spec catalog, the live
Prometheus state from `/metrics`, and the server identity from `/version` and
`/v1/models`. Three new files do the work, and the existing `hardware_info`
implementation is replaced with one that fans out to all four sources at once.

### `tools/_metrics.py`: the parser

The leading underscore is a convention: nothing outside the `tools/` package
imports from it. Two functions:

```python
def parse_prometheus(text: str) -> dict[str, Any]: ...
def histogram_quantile(hist: dict, q: float) -> float | None: ...
```

The first flattens Prometheus exposition format to a dict. Counters and gauges
become floats (or `{label-set: float}` maps when there are labels). Histograms
get reassembled into `{"buckets": [(le, count), ...], "count": ..., "sum": ...}`,
which is the shape a quantile estimator wants.

The non-obvious part is the parser library's name normalisation.
`prometheus_client.parser.text_string_to_metric_families` is helpful enough
to strip the `_total` suffix from a counter's _family_ name, while keeping it
on the actual sample. So `vllm:prompt_tokens_total` arrives as a family named
`vllm:prompt_tokens` containing one sample named `vllm:prompt_tokens_total`.
If you key the output dict on family names you lose the suffix, and an agent
that knows the metric exists on the wire as `vllm:prompt_tokens_total` won't
find it. The parser keys on sample names instead, so what goes in comes out.

The second function reproduces Prometheus's own `histogram_quantile`: walk
the cumulative buckets until you cross the target percentile, then linearly
interpolate inside that bucket. Two edge cases worth calling out:

- **Empty histogram.** A fresh server hasn't observed anything yet, so
  `count` is zero. Return `None` rather than dividing by zero. Callers
  unwrap with `if p95 is not None` (in real code) or pass `None` straight
  through to the agent (in `hardware_info`'s response).
- **Target lands in the `+Inf` bucket.** The histogram has no upper bound
  to interpolate against. Return the last finite bucket boundary, which
  is what Prometheus does; it's a known approximation, not a bug.

Both functions are pure, so the file ships a `__main__` block that feeds a
known-good blob through them and asserts the parsed shape. Run it with
`python -m tools._metrics` and you should see `ok: parse_prometheus +
histogram_quantile self-test passed`.

### `tools/hw_catalog.py`: the static spec

A module-level dict keyed by the exact string an operator puts in the
`TT_HARDWARE` env var. Nine seeded entries covering n150, n300, p100, p150,
the Wormhole and Blackhole LoudBox / QuietBox systems, and the Wormhole
Galaxy. Each entry holds chip family, chip count, Tensix core count, DRAM
size and type, peak BF16 TFLOPS, TDP, form factor, interconnect, and a
`source` URL pointing at the page where the values were read.

Two design choices worth their weight:

- **Hardcoded, not fetched.** A live spec lookup over the network adds a
  hop and a failure mode for information that essentially does not change.
  The silicon doesn't move; embedding what we know lets `hardware_info`
  answer in one round trip.
- **`None` over guessing.** Every numeric field is either confirmed against
  tenstorrent.com/hardware (or a linked spec sheet) or marked `None` with
  a `# TODO verify` comment. A wrong TFLOPS number is worse than no TFLOPS
  number, an agent will reason from it. Misses on the lookup return
  `{"unknown": True, "label": <value>}` so an unrecognised box still gets
  a useful payload, the agent learns the operator's declared label even
  if the catalog doesn't.

### `tools/hardware_info.py`: the merge

The new `hardware_info` fans out to four endpoints (`/health`, `/version`,
`/v1/models`, `/metrics`) in turn. Each fetch is wrapped, so a failing
source doesn't kill the others. The response carries a `sources` ledger
telling the agent who answered and who didn't:

```json
{
  "endpoint": "https://...",
  "hardware": {"label": "Tenstorrent n300", "chip_family": "Wormhole", ...},
  "server":   {"version": "0.6.x"},
  "models":   [{"id": "meta-llama/Llama-3.1-70B-Instruct", "max_model_len": 131072}],
  "live": {
    "running_requests": 2.0,
    "kv_cache_usage": 0.42,
    "p95_e2e_latency_s": 5.0,
    "preemptions_total": 3.0,
    ...
  },
  "sources": {"health": "ok", "version": "ok", "models": "ok", "metrics": "ok"}
}
```

The `live` block is hand-picked: short human-readable names, only the
signals worth foregrounding. An agent that wants the full `vllm:*`
namespace calls the `metrics` tool instead, which returns the parsed dict
without the editorialising. Two tools, two audiences: don't make the agent
choose between raw numbers and interpretation, give it both, separately.

### Why this is the differentiator

Every other "list models" MCP wrapper stops at the first column. The
combination, **static spec × live state × measured throughput** (with
`benchmark` covering the measured part), is what makes this server worth
using over a few lines of OpenAI client code. The agent can answer not
just "what is this box?" but "is it saturated?", "has it been thrashing?",
and "how fast is it actually going right now?" through one tool call each.

---

## Part 7: A proposal

The point of this project is not the code. It is the developer experience it
improves for both humans and agents, and the question of whether Tenstorrent
wants to put its name on a tool like this.

If the answer is yes, I think `tt-mcp` has the bones of a useful ecosystem
contribution:

- A canonical way for MCP-aware agents to call Tenstorrent inference endpoints,
  complementing `tt-inference-server` as the canonical production backend.
- An adjacent surface to `tt-studio` (all-in-one managed deployment UI):
  `tt-studio` serves the operator-with-a-browser; `tt-mcp` serves the
  developer-in-an-agent.
- A local-first story that does not require hardware access to onboard, with a
  clean seam for production.
- A small enough surface (five tools, four env vars, a single mock backend
  file) that a developer can read it top to bottom before deciding whether to
  adopt it.

A v1.0 would add streaming (which unlocks real time-to-first-token measurement
in `benchmark`), authentication beyond bearer tokens, a discovery mechanism
for operators who run more than one endpoint, and a per-model performance
catalog so an agent can answer "what tokens-per-second should I expect on n300
for this model?" without running `benchmark` itself.

---

## Closing

**Why I picked this topic.** The Developer Relations Engineer, Tools role at
Tenstorrent is about meeting developers where they already are and shortening
the distance between their keyboard and the hardware. MCP is increasingly where
developers already are for agent tooling. Tenstorrent inference is not yet. This
project sits in the middle of that gap.

**Who it is for.** A mid-to-advanced Python developer who has heard of MCP but
has not built a server, or who has built one against databases and APIs but not
against accelerator hardware. I assumed familiarity with async Python and HTTP
clients, and spelled out the parts of MCP that matter for a tool author.

**What you take away.** A working MCP server in a repo you can fork, a mock
backend that demonstrates the remote path without any privileged access, and a
clear view of the abstraction and where it holds. If you follow the tutorial,
you will have written a file Claude Desktop recognises as a hardware endpoint.
That is a modest claim, and an honest one, and the start of a story that gets
more interesting the closer it gets to real silicon.

---

_Source:_ <https://github.com/samgutentag/tt-mcp> _Author:_ Sam Gutentag,
<https://samgutentag.com>
