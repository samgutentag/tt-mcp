"""Tool implementations for tt-mcp.

Each tool lives in its own module and exposes a `register(mcp)` function
that attaches the tool to a FastMCP server. `server.py` imports these and
calls them in order.

Why not decorator-based auto-registration? Because the explicit calls in
server.py make it trivial to answer "which tools are active in this build?"
by reading one file. The import-side-effect alternative hides that answer
inside module import order, which is a poor trade for a teaching codebase.
"""
