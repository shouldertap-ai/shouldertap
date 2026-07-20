import json
from pathlib import Path

import httpx
import pytest

from shouldertap.mcp.server import build_mcp_server


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "shouldertap.yaml"
    path.write_text("org:\n  name: Test\nserver:\n  port: 8776\n")
    return path


def _extract_json(result) -> dict:
    """FastMCP's call_tool() returns a (content_blocks, structured_dict) tuple by default;
    take the structured dict directly.
    """
    if isinstance(result, tuple):
        _, structured = result
        return structured
    if isinstance(result, dict):
        return result.get("structuredContent", result)
    for block in result:
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    raise AssertionError(f"no text content block in {result!r}")


@pytest.mark.asyncio
async def test_ask_expert_returns_queued_status(config_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/requests"
        body = json.loads(request.content)
        assert body["question"] == "What does active customer mean?"
        assert body["topic"] == "revenue metrics"
        assert body["consumer"] == "mcp"
        return httpx.Response(200, json={"id": "req_1", "status": "queued"})

    client = httpx.Client(base_url="http://test/api/v1", transport=httpx.MockTransport(handler))
    mcp = build_mcp_server(config_path, http_client=client)

    result = await mcp.call_tool(
        "ask_expert",
        {"question": "What does active customer mean?", "topic": "revenue metrics"},
    )
    payload = _extract_json(result)
    assert payload == {"request_id": "req_1", "status": "queued"}


@pytest.mark.asyncio
async def test_ask_expert_includes_answer_on_deduped_resolved(config_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/requests":
            return httpx.Response(200, json={"id": "req_1", "status": "deduped_resolved"})
        if request.method == "GET" and request.url.path == "/api/v1/requests/req_1":
            return httpx.Response(
                200,
                json={
                    "id": "req_1",
                    "status": "accepted",
                    "proposal": {
                        "id": "prop_1",
                        "answer": "paying accounts active in 90d",
                        "structured": {"definition": "paying accounts active in 90d"},
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.Client(base_url="http://test/api/v1", transport=httpx.MockTransport(handler))
    mcp = build_mcp_server(config_path, http_client=client)

    result = await mcp.call_tool(
        "ask_expert",
        {"question": "What does active customer mean?", "topic": "revenue metrics"},
    )
    payload = _extract_json(result)
    assert payload["status"] == "deduped_resolved"
    assert payload["answer"] == {"definition": "paying accounts active in 90d"}


@pytest.mark.asyncio
async def test_check_answer_reports_pending_status(config_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/requests/req_1"
        return httpx.Response(200, json={"id": "req_1", "status": "asked", "proposal": None})

    client = httpx.Client(base_url="http://test/api/v1", transport=httpx.MockTransport(handler))
    mcp = build_mcp_server(config_path, http_client=client)

    result = await mcp.call_tool("check_answer", {"request_id": "req_1"})
    payload = _extract_json(result)
    assert payload == {"status": "asked"}


@pytest.mark.asyncio
async def test_check_answer_includes_answer_once_accepted(config_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "req_1",
                "status": "accepted",
                "proposal": {"id": "prop_1", "answer": "raw answer", "structured": None},
            },
        )

    client = httpx.Client(base_url="http://test/api/v1", transport=httpx.MockTransport(handler))
    mcp = build_mcp_server(config_path, http_client=client)

    result = await mcp.call_tool("check_answer", {"request_id": "req_1"})
    payload = _extract_json(result)
    assert payload["status"] == "accepted"
    assert payload["answer"] == {"summary": "raw answer"}
