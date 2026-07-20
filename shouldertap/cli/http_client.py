"""Build plan: every CLI command except `init`/`serve` is a thin HTTP client of an already
running `shtap serve`, using the bearer token from config -- the same design as the MCP server.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from shouldertap.engine.registry import load_config


def build_client(config_path: Path) -> httpx.Client:
    config = load_config(config_path)
    base_url = f"http://localhost:{config.server.port}/api/v1"
    token = config.api_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base_url, headers=headers, timeout=30.0)
