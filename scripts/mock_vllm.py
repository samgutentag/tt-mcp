"""
Mock vLLM server for local demos of the remote-endpoint path.

tt-mcp is backend-agnostic: it speaks the OpenAI-compatible wire format,
and any server that honours that format works. In the real deployment
path, that server is vLLM running on Tenstorrent hardware (hosted on
Koyeb or similar). While we develop locally, Ollama fills the same slot.

This file is a third backend. It is a minimal HTTP server that exposes
`/v1/models` and `/v1/chat/completions` with Tenstorrent-flavored
metadata: a 70B-class model name, a 131K context window, and a
`system_fingerprint` that advertises the accelerator. Pointing tt-mcp
at this mock is how you demo the remote code path without Koyeb access.

Run it:
    python scripts/mock_vllm.py                 # listens on 127.0.0.1:8000
    python scripts/mock_vllm.py --port 9000     # custom port

Use it:
    TT_ENDPOINT=http://127.0.0.1:8000 \\
    TT_HARDWARE="Tenstorrent Wormhole n300 (mock)" \\
    python scripts/smoke_test.py

Why stdlib only, no FastAPI?
    Zero new deps means you can copy this single file into any tutorial
    or hand-roll a variant in ten minutes. FastAPI would give us pydantic
    validation we do not need, and a second install step we do not want.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# A single advertised model. Real vLLM endpoints commonly serve one model
# per process; the list here is what tt-mcp's `list_models` and
# `hardware_info` tools will surface to the calling agent.
MOCK_MODELS = [
    {
        "id": "meta-llama/Llama-3.1-70B-Instruct",
        "object": "model",
        "created": 0,
        "owned_by": "mock-vllm",
        # `max_model_len` is a vLLM-specific extension to the OpenAI shape.
        # tt-mcp's `hardware_info` tool reads this field and surfaces it as
        # the context window.
        "max_model_len": 131072,
    }
]

# Free-form string the mock advertises as the accelerator. An agent sees
# this through `hardware_info` once the operator sets TT_HARDWARE to match.
MOCK_HARDWARE = "Tenstorrent Wormhole n300"


class MockHandler(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible responder. Implements only what tt-mcp calls."""

    # --- route table ---------------------------------------------------
    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": MOCK_MODELS})
        else:
            self._json(404, {"error": {"message": f"unknown route {self.path}"}})

    def do_POST(self) -> None:
        if self.path == "/v1/chat/completions":
            self._chat_completion()
        else:
            self._json(404, {"error": {"message": f"unknown route {self.path}"}})

    # --- handlers ------------------------------------------------------
    def _chat_completion(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "invalid JSON body"}})
            return

        # We only look at the last user message. Good enough for a demo.
        messages = request.get("messages", [])
        prompt = messages[-1].get("content", "") if messages else ""
        model = request.get("model", MOCK_MODELS[0]["id"])

        # Craft an obviously-mock response. The calling agent should be
        # able to tell this is not a real completion, while the wire shape
        # is identical to what vLLM would produce.
        reply = (
            f"[mock-vllm on {MOCK_HARDWARE}] I am the mock backend used "
            f"by tt-mcp for local demos. You asked: {prompt!r}. In a real "
            f"deployment, this response would come from {model} running "
            "on Tenstorrent hardware via vLLM."
        )

        self._json(
            200,
            {
                "id": "mock-cmpl-0",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "system_fingerprint": f"mock-vllm-{MOCK_HARDWARE}",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": reply},
                    }
                ],
                "usage": {
                    "prompt_tokens": len(prompt),
                    "completion_tokens": len(reply),
                    "total_tokens": len(prompt) + len(reply),
                },
            },
        )

    # --- helpers -------------------------------------------------------
    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        # Route access logs to stderr with a prefix so they never collide
        # with anything a caller's stdout pipeline might be doing.
        sys.stderr.write(f"[mock-vllm] {fmt % args}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock vLLM for tt-mcp demos")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MockHandler)
    sys.stderr.write(
        f"[mock-vllm] listening on http://{args.host}:{args.port} "
        f"(hardware={MOCK_HARDWARE})\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[mock-vllm] shutting down\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
