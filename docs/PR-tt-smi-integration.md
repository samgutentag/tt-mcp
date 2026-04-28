# PR: tt-smi integration — local hardware visibility

> Status: spec, pre-implementation. Depends on `feat/metrics-and-catalog` (PR #?) being
> merged first; the `_metrics.py` partial-failure pattern and `sources` field on
> `hardware_info` are reused here.

## Goal

Add a second view of Tenstorrent silicon: not "what is the inference server saying about
itself" but "what is physically plugged into this machine right now." Wraps
[`tenstorrent/tt-smi`](https://github.com/tenstorrent/tt-smi), the canonical TT system
management CLI, and exposes its snapshot output as MCP tools.

## Why

The current `hardware_info` describes silicon **indirectly**, through whatever the inference
server reports. That's the right shape when wrapping a hosted endpoint, but it leaves a gap
when an agent is running on the same box as the hardware — which is the typical TT developer
setup.

tt-smi fills that gap. It talks to the boards over PCIe via the kernel driver, dumps
**device list, telemetry (voltage, temp, power, AICLK, ASIC temp), firmware versions, and PCI
topology** as JSON. Wrapping it gives the agent:

- "What boards are in this machine?" — `tt_smi_devices`
- "How hot is the chip right now?" — `tt_smi_telemetry`
- "What firmware is on board 0?" — `tt_smi_firmware`
- "Give me everything as raw JSON" — `tt_smi_snapshot`

Combined with the existing inference-server tools, this puts tt-mcp in a unique position: the
only MCP server that gives an agent both **what's serving** (vLLM endpoint) and **what's
installed** (tt-smi). For a developer debugging "is my Wormhole hot, is my model loaded, is
the server responding" — that's all three answers from one MCP server.

## Why this is a different shape from the existing tools

Worth being explicit about, because the implementation pattern changes:

| existing tools                | tt-smi tools                       |
| ----------------------------- | ---------------------------------- |
| HTTP to a configurable URL    | local subprocess, fixed binary     |
| Network errors, timeouts      | binary missing, exit codes, stderr |
| Mock = mini HTTP server       | Mock = fixture JSON files          |
| Backend swap via env var      | Backend detection at call time     |

This is the second MCP pattern in the repo. The TUTORIAL.md update should call this out — "a
subprocess-wrapper tool looks like this" is genuinely useful tutorial content alongside the
existing HTTP-wrapper pattern.

## Files

### New

#### `tools/tt_smi.py`

All four tools live in one file (instead of one-per-file like the HTTP tools) because they
share a snapshot helper and the parsing logic for each section is tiny. Splitting would cost
more in indirection than it would buy in tutorial clarity.

```python
async def tt_smi_devices() -> dict:        # list of boards
async def tt_smi_telemetry() -> dict:      # per-device live numbers
async def tt_smi_firmware() -> dict:       # per-device firmware versions
async def tt_smi_snapshot() -> dict:       # raw JSON, no parsing
```

Internal helper, not registered:

```python
async def _get_snapshot() -> dict:
    """Return tt-smi snapshot as a dict.

    In production: runs `tt-smi -s -f <tmpfile> --snapshot_no_tty`, reads the JSON,
    deletes the tmpfile, returns the dict.

    In dev: if TT_SMI_FIXTURE is set to a path, reads that file directly. Skips the
    subprocess entirely. Lets the smoke test run without TT hardware.
    """
```

Snapshot caching: in-memory cache with a 5-second TTL inside the module. Multiple back-to-back
tool calls (`tt_smi_devices` then `tt_smi_telemetry` from the same agent turn) shouldn't fork
tt-smi twice. Make the TTL configurable via `TT_SMI_CACHE_TTL` env var; default 5 seconds.

Error handling matches the existing pattern: errors are returned as `{"error": "<message>"}`,
never raised. The cases to handle:

- tt-smi binary not on PATH → `{"error": "tt-smi not installed", "install_hint": "pip install tt-smi"}`
- tt-smi exits non-zero → include exit code and stderr tail
- Snapshot JSON malformed → return raw text in the error so a future Claude can debug it
- No TT devices found → return `{"devices": [], "note": "no tt devices detected on host"}`
  (not an error — agent can reason about an empty host)

#### `tools/_tt_smi_parse.py`

Pure parsing, no I/O. Takes the snapshot dict, returns the per-tool shapes. Easier to unit
test in isolation, and keeps `tt_smi.py` focused on subprocess management.

```python
def parse_devices(snapshot: dict) -> list[dict]: ...
def parse_telemetry(snapshot: dict) -> dict: ...     # keyed by device index
def parse_firmware(snapshot: dict) -> dict: ...      # keyed by device index
```

Snapshot key paths to verify against a real run: `host_info`, `device_info`, `device_info_list`,
`firmwares` (or `firmware`?). The key names have shifted across tt-smi releases — pin the
fixtures to a specific tt-smi version and note it in a comment at the top of the parser file.

#### `fixtures/tt_smi_snapshots/`

Sample JSON snapshots, one per representative hardware config:

- `n150.json`
- `n300.json`
- `wh_quietbox.json` (4× n300 in one box)
- `bh_p150.json` (Blackhole representative)
- `empty.json` (host with no TT devices)

Source these from real `tt-smi -s` runs against actual hardware where possible. For
configurations without access, hand-write a minimal-but-realistic JSON keyed off the published
tt-smi snapshot schema and mark with `"_synthetic": true` at the top level so smoke tests can
flag synthetic fixtures.

**TODO before merge:** Sam to capture at least one real snapshot during DevRel hardware access
and replace the corresponding synthetic file. Open a follow-up issue tracking which fixtures
are still synthetic.

### Modified

#### `config.py`

New env vars:

| Variable               | Default | Notes                                          |
| ---------------------- | ------- | ---------------------------------------------- |
| `TT_SMI_PATH`          | `tt-smi` | Override binary location; default uses PATH    |
| `TT_SMI_FIXTURE`       | *(empty)* | If set, read this JSON file instead of shelling out |
| `TT_SMI_CACHE_TTL`     | `5`     | Snapshot cache TTL in seconds                  |

`TT_SMI_FIXTURE` is the dev-mode escape hatch; never set in production.

#### `server.py`

Register the four new tools. One line each, follow existing pattern.

#### `tools/hardware_info.py`

Do **not** auto-merge tt-smi data into `hardware_info`. The two tools describe potentially
different machines: `hardware_info` describes whatever box is at `TT_ENDPOINT`, which may be a
hosted deployment hundreds of miles away; `tt_smi_*` describes the box the MCP server is
running on. Merging silently would conflate them.

Do add a note to the `hardware_info` response when tt-smi is available locally:

```json
{
  "hardware": {...},
  "server": {...},
  "live": {...},
  "local_hint": "tt-smi detected on host; call tt_smi_devices for local hardware view"
}
```

The agent decides whether to follow up. Sets up the right mental model — the inference server
and the local box are separate things.

#### `requirements.txt`

No new Python deps; `subprocess` and `json` are stdlib. Optionally add `tt-smi` as an extras
group:

```
[project.optional-dependencies]
local = ["tt-smi"]
```

So `pip install -e .[local]` pulls it in, but the default install stays light. Document this
in the README.

#### `README.md`

1. Four new rows in the Tools table.
2. New subsection under "Design notes": **Two views of hardware** — explain inference-server
   vs local-host distinction and why they're not auto-merged.
3. New subsection: **Local hardware mode** — installation (tt-smi from pip), required
   permissions (PCIe access via `/dev/tenstorrent/*`), the `TT_SMI_FIXTURE` dev escape hatch.
4. Update the "Connecting to Claude Desktop" example to mention `TT_SMI_FIXTURE` for users
   who want to demo without TT hardware.

#### `docs/TUTORIAL.md`

Add a section: **Pattern 2 — wrapping a CLI**. Walks through:

- Why subprocess-based tools look different from HTTP-based ones
- The fixture-as-mock pattern (vs the mini-HTTP-server pattern from the inference path)
- Snapshot caching: why 5 seconds, why in-memory, when to invalidate
- Error shape: subprocess errors look different from HTTP errors and the agent should be able
  to tell them apart

Keep it the same read-top-to-bottom voice as the existing tutorial.

## Design choices to call out in the README

- **Two views, not merged.** `hardware_info` and `tt_smi_*` describe different machines in the
  general case. Auto-merging would lie when the agent is talking to a hosted endpoint.
- **Subprocess, not Python import.** tt-smi is a Python package and could be imported directly,
  but shelling out to the CLI keeps the wrapper version-independent — tt-smi internals shift
  release-to-release, the CLI surface is more stable.
- **Snapshot cache with a short TTL.** Telemetry changes second-to-second; a 5-second cache
  is short enough to feel live, long enough to coalesce a multi-tool agent turn into one
  PCIe round trip.
- **Fixtures as mock.** A mock tt-smi binary would be possible but messy. Fixture files are
  smaller, easier to read in PR review, and trivially version-controlled.
- **No `-r` reset tool.** Destructive operations are a foot-gun behind an LLM agent, even with
  confirmations. Out of scope, indefinitely.

## Test plan

1. Smoke test path **without** TT hardware:

   ```bash
   export TT_SMI_FIXTURE=fixtures/tt_smi_snapshots/n300.json
   python scripts/smoke_test_tt_smi.py
   ```

   Asserts each of the four tools returns expected shape against the fixture. Repeat for
   `empty.json` to verify the empty-host path.

2. Real hardware path (Sam, when accessible):

   ```bash
   unset TT_SMI_FIXTURE
   python scripts/smoke_test_tt_smi.py
   ```

   Same assertions, against live tt-smi.

3. Failure modes:
   - Unset `PATH` so tt-smi isn't found → tools return `error` field with `install_hint`
   - Set `TT_SMI_PATH=/nonexistent` → same error path
   - Point `TT_SMI_FIXTURE` at a malformed JSON → tools return parse error with raw text

4. Cache verification: call `tt_smi_devices` then `tt_smi_telemetry` within 5 seconds, verify
   only one subprocess invocation (instrument with a counter in the helper for the test).

5. MCP Inspector manual verification: all four tools appear in the registered list, each
   returns sensibly-shaped data against the n300 fixture.

## Out of scope (do not do in this PR)

- `-r` reset tool — destructive, indefinitely out
- Galaxy-specific reset tools (`-glx_reset_tray` etc.) — same reason
- Watching telemetry over time / streaming — agents don't need a stream, they can poll
- Parsing the tt-smi GUI keyboard shortcut surface — irrelevant to programmatic use
- Auto-merging tt-smi data into `hardware_info` — explicitly chosen against
- Wrapping `tt-flash` (firmware updates) — destructive, separate consideration

## Acceptance

- [ ] Four tools (`tt_smi_devices`, `tt_smi_telemetry`, `tt_smi_firmware`, `tt_smi_snapshot`)
      registered in `server.py`
- [ ] Fixture-based smoke test passes for at least n150, n300, and empty configurations
- [ ] All four tools handle the "tt-smi not installed" case gracefully (return error dict,
      don't crash MCP)
- [ ] Snapshot cache verified to coalesce back-to-back calls
- [ ] `hardware_info` includes `local_hint` when tt-smi is detected locally
- [ ] README and TUTORIAL pass read-aloud test
- [ ] Synthetic fixtures clearly marked; follow-up issue filed for replacing with real captures

## For the GitHub issue

Suggested title: **Add tt-smi integration: local hardware visibility tools**

Suggested labels: `enhancement`, `tools`, `tutorial`

Suggested body (one paragraph + link, not the whole spec):

> Adds four MCP tools wrapping `tenstorrent/tt-smi` so agents can see what TT hardware is
> physically installed on the host (devices, telemetry, firmware) — complementing the existing
> inference-server tools that describe what's *serving*. Introduces a second tool pattern to
> the repo: subprocess-wrapping with fixture-based mocking, alongside the existing
> HTTP-endpoint pattern. Full spec in `docs/PR-tt-smi-integration.md`. Depends on
> `feat/metrics-and-catalog` landing first.
