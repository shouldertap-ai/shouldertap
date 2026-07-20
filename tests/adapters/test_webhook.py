from datetime import datetime

import httpx
import pytest

from shouldertap.adapters.webhook import WebhookAdapter
from shouldertap.engine.contracts import ContextProposal, Provenance


def _proposal() -> ContextProposal:
    return ContextProposal(
        request_id="req_1",
        kind="freeform.answer",
        answer="paying accounts active in 90d",
        provenance=Provenance(
            expert_id="U1",
            expert_name="Dana",
            answered_via="console",
            answered_at=datetime(2026, 7, 20),
        ),
        consumer="test-consumer",
    )


def test_on_accepted_posts_proposal_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    adapter = WebhookAdapter("https://example.com/hook")
    result = adapter.on_accepted(_proposal())

    assert result.success is True
    assert calls[0][0] == "https://example.com/hook"
    assert calls[0][1]["request_id"] == "req_1"


def test_on_accepted_returns_failure_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)

    adapter = WebhookAdapter("https://example.com/hook")
    result = adapter.on_accepted(_proposal())

    assert result.success is False
    assert "connection refused" in (result.detail or "")


def test_on_accepted_returns_failure_on_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, json, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(500, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    adapter = WebhookAdapter("https://example.com/hook")
    result = adapter.on_accepted(_proposal())

    assert result.success is False
