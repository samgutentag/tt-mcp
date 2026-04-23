"""
tt-mcp: an MCP server that wraps Tenstorrent inference endpoints.

Entry point. Responsibilities:
  1. Configure logging (stderr only, stdout is reserved for JSON-RPC).
  2. Create the FastMCP instance.
  3. Register every tool module.
  4. Run the stdio transport loop.

Every tool lives in its own file under `tools/`. To add one, create
`tools/your_tool.py` with a `register(mcp)` function, then import it and
call `your_tool.register(mcp)` below. That single list of `.register()`
calls is the authoritative answer to "which tools does this server expose?"

Why FastMCP over the low-level `mcp.server.Server`?
    The low-level API requires hand-written JSONSchema and a dispatch table.
    FastMCP introspects type hints and docstrings to produce both. For a
    server whose job is "expose a handful of tools", FastMCP is the right
    altitude.

Why stdio transport?
    Claude Desktop, the MCP Inspector, and most IDE integrations spawn
    servers as subprocesses and speak JSON-RPC over stdin/stdout. Stderr
    carries logs. That is why this module never `print()`s: anything on
    stdout that is not a JSON-RPC frame corrupts the protocol.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from config import config
from tools import generate, health, models

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [tt-mcp] %(levelname)s %(message)s",
)
log = logging.getLogger("tt-mcp")

mcp = FastMCP(
    name="tt-mcp",
    instructions=(
        "Tools for calling a Tenstorrent (or OpenAI-compatible) inference "
        "endpoint. Use `generate` for text completions, `list_models` to "
        "see what models the backend has available, and `hardware_info` "
        "to learn what hardware is serving the requests."
    ),
)

# Register every tool exposed by this build. Add a new line here when you
# add a new tool.
generate.register(mcp)
models.register(mcp)
health.register(mcp)


if __name__ == "__main__":
    log.info(
        "Starting tt-mcp (endpoint=%s, model=%s, is_ollama=%s, hardware=%s)",
        config.endpoint,
        config.model,
        config.is_ollama,
        config.hardware or "unset",
    )
    mcp.run()
