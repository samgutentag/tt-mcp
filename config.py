"""
Endpoint configuration for tt-mcp.

This module has one job: read three environment variables and expose them as
a typed, frozen config object the rest of the server can import.

Why env vars, and not a config file or CLI args?
    MCP servers are launched as subprocesses by the host (Claude Desktop, the
    MCP Inspector, an IDE). The host does not know about your CLI flags, and
    there is no shared config file location across hosts. Environment
    variables are the one channel every host exposes, via the `env` field in
    its MCP config block. So env-first is the path of least surprise.

Why python-dotenv?
    Production deployments (Koyeb, a Kubernetes pod, a systemd unit) set env
    vars through the orchestrator, no .env file is involved. But during local
    development it is much nicer to keep TT_ENDPOINT in a file than to re-
    export it in every shell. dotenv bridges the two: if a .env file exists
    next to this module, it is loaded; otherwise it falls back silently to the
    process environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root if present. `override=False` means real
# environment variables win over file values. Important when a deploy
# platform sets TT_ENDPOINT and a stale .env is still sitting on disk.
load_dotenv(Path(__file__).parent / ".env", override=False)


@dataclass(frozen=True)
class Config:
    """Runtime configuration, loaded once at server start."""

    endpoint: str
    """Base URL of the inference endpoint, without a trailing slash.

    Examples:
        http://localhost:11434              # Ollama
        https://my-svc.koyeb.app            # Tenstorrent/Koyeb
        http://vllm.internal:8000           # Any OpenAI-compatible vLLM
    """

    model: str
    """Default model identifier. Individual tool calls may override it."""

    api_key: str | None
    """Bearer token, or None for endpoints that do not authenticate."""

    hardware: str | None
    """Optional human-readable label for the hardware behind the endpoint,
    e.g. "Tenstorrent Wormhole n300". Set via the TT_HARDWARE env var.

    vLLM's OpenAI-compatible routes do not expose hardware details, so this
    is the server operator's declaration, not something we probe. When unset,
    `hardware_info` will say so rather than invent a label."""

    @classmethod
    def from_env(cls) -> Config:
        # Strip trailing slashes so callers can do `f"{endpoint}/v1/..."`
        # without worrying about double slashes.
        endpoint = os.environ.get("TT_ENDPOINT", "http://localhost:11434").rstrip("/")
        model = os.environ.get("TT_MODEL", "llama3.2:3b")
        # Treat empty strings as unset. Shell exports like `export TT_API_KEY=`
        # produce "" rather than None, and that distinction does not matter
        # here, anything falsy means "no auth."
        api_key = os.environ.get("TT_API_KEY") or None
        hardware = os.environ.get("TT_HARDWARE") or None
        return cls(endpoint=endpoint, model=model, api_key=api_key, hardware=hardware)

    @property
    def is_ollama(self) -> bool:
        """Heuristic: is this endpoint an Ollama instance?

        Ollama exposes extra routes the OpenAI-compatible spec does not
        cover. Most notably `/api/tags` for model discovery. vLLM uses
        `/v1/models` instead. The two are not fungible, so `list_models`
        has to branch on the backend.

        We detect Ollama by its default port (11434) or a localhost URL.
        This is imperfect but it keeps the demo honest without adding a probe
        request on every startup. Users in that edge case can set the
        endpoint explicitly and we'll be wrong gracefully (list_models
        will 404 and the tool will tell them so).
        """
        return "11434" in self.endpoint or "localhost" in self.endpoint

    @property
    def auth_headers(self) -> dict[str, str]:
        """HTTP headers for an outbound request. Empty when unauthenticated."""
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}


# Module-level singleton. Import this everywhere instead of reading env vars
# scattered across the codebase. This gives us one place to change, and makes
# tests trivially able to patch a different Config.
config = Config.from_env()
