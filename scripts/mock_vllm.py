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

# Static Prometheus exposition. Numbers are deliberately fixed so the smoke
# test is deterministic, the goal here is wire-format realism, not a
# simulation of a live workload. Histogram bucket boundaries match what vLLM
# emits in the wild (one of the few places we mirror real values; an agent
# computing p95 needs the buckets to be plausible).
MOCK_METRICS = """\
# HELP vllm:num_requests_running Number of requests currently running.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running 2.0
# HELP vllm:num_requests_waiting Number of requests waiting in the queue.
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting 0.0
# HELP vllm:gpu_cache_usage_perc KV cache usage as a fraction.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc 0.42
# HELP vllm:gpu_prefix_cache_hit_rate Prefix cache hit rate.
# TYPE vllm:gpu_prefix_cache_hit_rate gauge
vllm:gpu_prefix_cache_hit_rate 0.61
# HELP vllm:num_preemptions_total Cumulative request preemptions.
# TYPE vllm:num_preemptions_total counter
vllm:num_preemptions_total 3.0
# HELP vllm:prompt_tokens_total Total prompt tokens processed.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total 184320.0
# HELP vllm:generation_tokens_total Total generated tokens.
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total 41216.0
# HELP vllm:time_to_first_token_seconds Histogram of TTFT in seconds.
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{le="0.05"} 12.0
vllm:time_to_first_token_seconds_bucket{le="0.1"} 38.0
vllm:time_to_first_token_seconds_bucket{le="0.2"} 71.0
vllm:time_to_first_token_seconds_bucket{le="0.5"} 88.0
vllm:time_to_first_token_seconds_bucket{le="1.0"} 95.0
vllm:time_to_first_token_seconds_bucket{le="2.5"} 98.0
vllm:time_to_first_token_seconds_bucket{le="+Inf"} 100.0
vllm:time_to_first_token_seconds_count 100.0
vllm:time_to_first_token_seconds_sum 14.7
# HELP vllm:e2e_request_latency_seconds Histogram of end-to-end latency.
# TYPE vllm:e2e_request_latency_seconds histogram
vllm:e2e_request_latency_seconds_bucket{le="0.5"} 18.0
vllm:e2e_request_latency_seconds_bucket{le="1.0"} 47.0
vllm:e2e_request_latency_seconds_bucket{le="2.5"} 82.0
vllm:e2e_request_latency_seconds_bucket{le="5.0"} 95.0
vllm:e2e_request_latency_seconds_bucket{le="10.0"} 99.0
vllm:e2e_request_latency_seconds_bucket{le="+Inf"} 100.0
vllm:e2e_request_latency_seconds_count 100.0
vllm:e2e_request_latency_seconds_sum 168.4
"""

# Mock vLLM version. Real vLLM exposes this via `/version` so an operator
# can match metric names to the right vLLM minor (the metric namespace has
# shifted across releases).
MOCK_VERSION = "0.6.x-mock"


class MockHandler(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible responder. Implements only what tt-mcp calls."""

    # --- route table ---------------------------------------------------
    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": MOCK_MODELS})
        elif self.path == "/metrics":
            self._text(200, MOCK_METRICS, content_type="text/plain; version=0.0.4")
        elif self.path == "/health":
            # vLLM's /health returns 200 with an empty body when ready.
            self._raw(200, b"", content_type="text/plain")
        elif self.path == "/version":
            self._json(200, {"version": MOCK_VERSION})
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
        self._raw(status, json.dumps(obj).encode("utf-8"), content_type="application/json")

    def _text(self, status: int, body: str, *, content_type: str) -> None:
        self._raw(status, body.encode("utf-8"), content_type=content_type)

    def _raw(self, status: int, body: bytes, *, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

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
