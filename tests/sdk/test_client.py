from datetime import datetime

import httpx
import pytest

from shouldertap.engine.contracts import (
    ConsumerRegistration,
    ContextProposal,
    ContextRequest,
    InProcessDelivery,
    Provenance,
)
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.store.models import ConsumerRow
from shouldertap.sdk.client import ShoulderTapClient, register_in_process


def _request() -> ContextRequest:
    return ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="sdk-consumer",
    )


def test_ask_posts_request_and_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/requests"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"id": "req_1", "status": "queued"})

    client = ShoulderTapClient(base_url="http://test/api/v1", api_token="test-token")
    client._client = httpx.Client(
        base_url="http://test/api/v1",
        headers={"Authorization": "Bearer test-token"},
        transport=httpx.MockTransport(handler),
    )

    result = client.ask(_request())
    assert result == {"id": "req_1", "status": "queued"}


def test_get_request_fetches_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/requests/req_1"
        return httpx.Response(200, json={"id": "req_1", "status": "accepted"})

    client = ShoulderTapClient(base_url="http://test/api/v1")
    client._client = httpx.Client(
        base_url="http://test/api/v1", transport=httpx.MockTransport(handler)
    )

    result = client.get_request("req_1")
    assert result["status"] == "accepted"


def test_register_and_unregister_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json={"id": "my-consumer"})
        return httpx.Response(204)

    client = ShoulderTapClient(base_url="http://test/api/v1")
    client._client = httpx.Client(
        base_url="http://test/api/v1", transport=httpx.MockTransport(handler)
    )

    registration = ConsumerRegistration(
        id="my-consumer", handles_kinds=["glossary.definition"], delivery=InProcessDelivery()
    )
    result = client.register(registration)
    assert result == {"id": "my-consumer"}

    client.unregister("my-consumer")
    assert calls == [("POST", "/api/v1/consumers"), ("DELETE", "/api/v1/consumers/my-consumer")]


def test_client_supports_context_manager() -> None:
    with ShoulderTapClient(base_url="http://test/api/v1") as client:
        assert client._client.is_closed is False
    assert client._client.is_closed is True


def test_register_in_process_attaches_callbacks_to_deliverer() -> None:
    deliverer = ConsumerDeliverer()
    received = []

    class Callbacks:
        def on_proposal(self, proposal: ContextProposal) -> None:
            pass

        def on_proposal_accepted(self, proposal: ContextProposal) -> None:
            received.append(proposal)

        def on_proposal_rejected(self, proposal: ContextProposal, reason: str) -> None:
            pass

        def on_request_failed(self, request_id: str, reason: object) -> None:
            pass

    register_in_process(deliverer, "embedded-consumer", Callbacks())

    proposal = ContextProposal(
        request_id="req_1",
        kind="freeform.answer",
        answer="an answer",
        provenance=Provenance(
            expert_id="U1",
            expert_name="Dana",
            answered_via="console",
            answered_at=datetime(2026, 1, 1),
        ),
        consumer="embedded-consumer",
    )
    consumer_row = ConsumerRow(id="embedded-consumer", handles_kinds=[], delivery_type="inprocess")
    deliverer.deliver_accepted(consumer_row, proposal)

    assert received == [proposal]
