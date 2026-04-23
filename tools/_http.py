"""HTTP helpers shared across tool implementations.

Underscored module name because nothing outside the `tools/` package should
import from here. If you find yourself reaching for `tools._http` from
`server.py`, that is a signal the helper belongs in `config.py` instead.
"""

from __future__ import annotations

import logging

import httpx

# 60s total, 10s to establish the TCP connection. These are generous because
# cold-start inference on a large model can take tens of seconds. We would
# rather wait than confuse a slow model with a dead endpoint. The calling
# agent can always cancel if it wants to give up earlier.
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

log = logging.getLogger("tt-mcp")


def format_error(message: str, *, detail: str | None = None) -> str:
    """Format an error string for return from a tool.

    MCP tools can signal failure two ways: raise an exception (which becomes
    an opaque protocol-level error the calling agent cannot introspect) or
    return a descriptive string (which flows straight through to the model
    as the tool result). We pick the second: a string the model can reason
    over is more useful than a 500.

    The detail is log-only for long payloads: included in the user-facing
    message only if short enough to be helpful, never the raw 10 KB of a
    vLLM stack trace."""
    log.warning("tool error: %s%s", message, f" ({detail})" if detail else "")
    if detail:
        return f"{message}\n\nDetail: {detail}"
    return message
