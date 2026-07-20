"""Spec §12: the `ask_expert` / `check_answer` MCP tools -- how the agent ecosystem taps into
ShoulderTap without an SDK integration. Runs as a stdio server (`shtap mcp`) and is an HTTP
client of an already-running `shtap serve`, exactly like the CLI (build plan's "single
deployable service... plus a CLI... and client SDKs" -- CLI and MCP are both clients).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from shouldertap.engine.registry import load_config

ASK_EXPERT_DESCRIPTION = (
    "Ask a human expert in this organization a question that no document answers. Returns "
    "immediately with a request id; the answer arrives out-of-band after human review. Use "
    "only when retrieval has failed and the answer requires organizational knowledge."
)


def _structured_answer(proposal: dict[str, Any]) -> dict[str, Any]:
    return proposal.get("structured") or {"summary": proposal["answer"]}


def build_mcp_server(
    config_path: Path, *, base_url: str | None = None, http_client: httpx.Client | None = None
) -> FastMCP:
    """`http_client` is injectable so tests can point this at a transport double instead of a
    real socket -- production callers (main(), the `shtap mcp` CLI command) never pass it.
    """
    config = load_config(config_path)
    if http_client is not None:
        client = http_client
    else:
        resolved_base_url = base_url or f"http://localhost:{config.server.port}/api/v1"
        token = config.api_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        client = httpx.Client(base_url=resolved_base_url, headers=headers, timeout=30.0)

    mcp = FastMCP(name="shouldertap")

    @mcp.tool(description=ASK_EXPERT_DESCRIPTION)
    def ask_expert(
        question: str,
        topic: str,
        kind: str = "freeform.answer",
        context: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "question": question,
            "topic": topic,
            "kind": kind,
            "consumer": "mcp",
            "context": context or {},
        }
        if dedup_key is not None:
            payload["dedup_key"] = dedup_key

        response = client.post("/requests", json=payload)
        response.raise_for_status()
        body = response.json()
        result: dict[str, Any] = {"request_id": body["id"], "status": body["status"]}

        if body["status"] == "deduped_resolved":
            detail = client.get(f"/requests/{body['id']}")
            detail.raise_for_status()
            proposal = detail.json().get("proposal")
            if proposal is not None:
                result["answer"] = _structured_answer(proposal)

        return result

    @mcp.tool(description="Poll for the answer to a previously submitted ask_expert request.")
    def check_answer(request_id: str) -> dict[str, Any]:
        response = client.get(f"/requests/{request_id}")
        response.raise_for_status()
        detail = response.json()
        result: dict[str, Any] = {"status": detail["status"]}
        if detail.get("proposal") is not None:
            result["answer"] = _structured_answer(detail["proposal"])
        return result

    return mcp


def main(config_path: Path) -> None:
    server = build_mcp_server(config_path)
    server.run(transport="stdio")
