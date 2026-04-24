"""
One-shot stdio smoke test for tt-mcp.

Launches the server as a subprocess, speaks JSON-RPC over stdin/stdout the
way the MCP Inspector and Claude Desktop do, and asserts every tool works.
Not a full test suite, just a fast confidence check you can run after any
change to server.py.

Run with:  python scripts/smoke_test.py

Output is deliberately chatty: each call prints a "running..." line before
the request is sent, so a slow cold-start (first `generate` against a large
model) never looks hung. Sections are separated by dashed rules to make
the output readable in slides and demos.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

RULE = "-" * 72


def section(title: str) -> None:
    """Print a dashed section header so demo output is scannable."""
    print(f"\n{RULE}\n{title}\n{RULE}", flush=True)


def running(msg: str) -> None:
    """Print a status line before a potentially slow call. Flush so it
    appears immediately, never after the response comes back."""
    print(f"  ...{msg}", flush=True)


def send(proc: subprocess.Popen, payload: dict) -> None:
    """MCP uses line-delimited JSON over stdio. One message per line."""
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def recv(proc: subprocess.Popen) -> dict:
    """Read one JSON-RPC response line from the server."""
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("server closed stdout unexpectedly")
    return json.loads(line)


def main() -> int:
    # Use the venv's Python so the subprocess has access to the MCP SDK.
    python = HERE / ".venv" / "bin" / "python"
    server = HERE / "server.py"

    section("tt-mcp smoke test")
    running("starting server subprocess")

    proc = subprocess.Popen(
        [str(python), str(server)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # let the server's own logs show up in our terminal
        text=True,
        bufsize=1,
    )

    try:
        # 1. Handshake. Every MCP session starts with `initialize`.
        section("1. initialize handshake")
        running("sending initialize")
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke-test", "version": "0.0.0"},
                },
            },
        )
        init = recv(proc)
        print("  initialize →", init.get("result", {}).get("serverInfo"))

        # The spec requires the client to send `notifications/initialized`
        # after receiving the initialize response.
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 2. List tools.
        section("2. tools/list")
        running("sending tools/list")
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = recv(proc)
        names = [t["name"] for t in tools["result"]["tools"]]
        print("  tools available →", names)
        assert set(names) == {
            "generate",
            "list_models",
            "hardware_info",
        }, f"unexpected: {names}"

        # Assert every tool shipped a non-empty description. Day 1 had a
        # silent bug where `generate`'s description was "" because its
        # docstring used `"""...""" % var` (not a string literal, so Python
        # did not capture it as __doc__). This guards against a regression.
        for t in tools["result"]["tools"]:
            assert t.get("description"), (
                f"tool {t['name']!r} has an empty description. Check that "
                "the docstring is a plain string literal, not an expression."
            )

        # 3. Call list_models.
        section("3. tools/call list_models")
        running("asking the backend what models it serves")
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_models", "arguments": {}},
            },
        )
        result = recv(proc)
        text = result["result"]["content"][0]["text"]
        print(text)
        # Backend-agnostic assertion: any response that names at least
        # one model is acceptable. The smoke test runs against Ollama,
        # the mock vLLM, or a real hosted deployment; each names
        # different models.
        assert " - " in text, "expected at least one model listed"

        # 4. Call hardware_info.
        section("4. tools/call hardware_info")
        running("asking what hardware is behind the endpoint")
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "hardware_info", "arguments": {}},
            },
        )
        result = recv(proc)
        text = result["result"]["content"][0]["text"]
        print(text)
        assert text.strip(), "hardware_info returned empty"

        # 5. Call generate. This is the slow one against a cold model.
        section("5. tools/call generate")
        running("sending a prompt (may take several seconds on cold start)")
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "generate",
                    "arguments": {"prompt": "Reply with the single word: pong"},
                },
            },
        )
        result = recv(proc)
        text = result["result"]["content"][0]["text"]
        print(f"  assistant → {text.strip()}")

        section("✓ smoke test passed")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
