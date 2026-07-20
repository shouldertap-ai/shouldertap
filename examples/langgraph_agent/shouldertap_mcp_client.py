"""A small, genuinely runnable async helper around ShoulderTap's MCP server: spawns `shtap mcp`
as a subprocess over stdio and calls its `ask_expert` / `check_answer` tools. This is the piece
any agent framework's MCP integration (LangGraph, a raw MCP client, etc.) would actually use --
agent.py in this directory shows where to plug it into a LangGraph node.

Try it directly (needs a `shtap serve` already running, and shouldertap.yaml with at least one
expert configured):

    uv run python examples/langgraph_agent/shouldertap_mcp_client.py \\
        "What does 'active customer' mean for Q2 reporting?" "revenue metrics"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def ask_expert(
    question: str,
    topic: str,
    *,
    kind: str = "freeform.answer",
    config_path: str = "shouldertap.yaml",
) -> dict[str, Any]:
    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "shouldertap.cli.main", "mcp", "--config", config_path]
    )
    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "ask_expert", {"question": question, "topic": topic, "kind": kind}
        )
        return result.structuredContent or {}


async def check_answer(request_id: str, *, config_path: str = "shouldertap.yaml") -> dict[str, Any]:
    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "shouldertap.cli.main", "mcp", "--config", config_path]
    )
    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("check_answer", {"request_id": request_id})
        return result.structuredContent or {}


if __name__ == "__main__":
    question, topic = sys.argv[1], sys.argv[2]
    outcome = asyncio.run(ask_expert(question, topic))
    print(outcome)
    if outcome.get("status") not in ("deduped_resolved",) and Path("shouldertap.yaml").exists():
        print(
            "Answer arrives out-of-band -- poll with check_answer(request_id) once a human replies."
        )
