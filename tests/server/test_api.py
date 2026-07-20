from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shouldertap.engine.transports.console import ConsoleTransport
from shouldertap.server.app import create_app

API_TOKEN = "test-token-123"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SHOULDERTAP_API_TOKEN", API_TOKEN)
    path = tmp_path / "shouldertap.yaml"
    path.write_text(
        """
org:
  name: Test Org
  timezone: UTC
server:
  api_token_env: SHOULDERTAP_API_TOKEN
experts:
  - id: U1
    name: Dana
    topics: ["revenue metrics"]
"""
    )
    return path


@pytest.fixture
def client_and_transport(config_path: Path):
    transport = ConsoleTransport(interactive=False)
    app = create_app(config_path, transport=transport)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {API_TOKEN}"})
        yield client, transport


def test_unauthenticated_request_is_rejected(config_path: Path) -> None:
    transport = ConsoleTransport(interactive=False)
    app = create_app(config_path, transport=transport)
    with TestClient(app) as client:
        response = client.get("/api/v1/experts")
    assert response.status_code == 401


def test_approval_ui_is_served_at_root(client_and_transport) -> None:
    client, _ = client_and_transport
    response = client.get("/")
    assert response.status_code == 200
    assert "ShoulderTap" in response.text


def test_get_experts_returns_synced_registry(client_and_transport) -> None:
    client, _ = client_and_transport
    response = client.get("/api/v1/experts")
    assert response.status_code == 200
    experts = response.json()
    assert len(experts) == 1
    assert experts[0]["id"] == "U1"
    assert experts[0]["name"] == "Dana"


def test_put_experts_replaces_registry(client_and_transport) -> None:
    client, _ = client_and_transport
    response = client.put(
        "/api/v1/experts",
        json=[{"id": "U2", "name": "Marco", "topics": ["billing"], "escalation_to": None}],
    )
    assert response.status_code == 200
    experts = response.json()
    assert len(experts) == 1
    assert experts[0]["id"] == "U2"

    response = client.get("/api/v1/experts")
    assert [e["id"] for e in response.json()] == ["U2"]


def test_golden_path_over_http(client_and_transport) -> None:
    client, transport = client_and_transport

    response = client.post(
        "/api/v1/requests",
        json={
            "kind": "glossary.definition",
            "topic": "revenue metrics",
            "question": "What does active customer mean?",
            "consumer": "bi.assistant",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    request_id = body["id"]

    assert len(transport.sent_asks) == 1
    transport.push_reply("paying accounts active in 90 days")

    response = client.get("/api/v1/proposals?status=pending")
    assert response.status_code == 200
    proposals = response.json()
    assert len(proposals) == 1
    proposal_id = proposals[0]["id"]
    assert proposals[0]["request_id"] == request_id

    response = client.post(f"/api/v1/proposals/{proposal_id}/accept", json={"decided_by": "alice"})
    assert response.status_code == 200
    assert response.json()["id"] == proposal_id

    response = client.get(f"/api/v1/requests/{request_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "accepted"
    assert detail["proposal"]["id"] == proposal_id

    response = client.get(f"/api/v1/audit?request_id={request_id}")
    assert response.status_code == 200
    events = [e["event"] for e in response.json()]
    for expected in ["request.received", "ask.sent", "reply.received", "proposal.accepted"]:
        assert expected in events


def test_get_request_returns_the_full_context_request(client_and_transport) -> None:
    """Regression: GET /requests/{id} must return the *full* ContextRequest (spec §5), not a
    hand-picked subset -- context, routing_policy, dedup_key, correlation, created_at, and
    target_experts all need to survive the round trip, not just the status/proposal fields.
    """
    client, transport = client_and_transport

    response = client.post(
        "/api/v1/requests",
        json={
            "kind": "glossary.definition",
            "topic": "revenue metrics",
            "question": "What does active customer mean?",
            "consumer": "bi.assistant",
            "context": {"asked_because": "low confidence", "entity": "dim_customers.active_flag"},
            "dedup_key": "glossary:dim_customers.active_flag",
            "correlation": {"trace_id": "trace-123"},
        },
    )
    request_id = response.json()["id"]

    detail = client.get(f"/api/v1/requests/{request_id}").json()

    assert detail["context"] == {
        "asked_because": "low confidence",
        "entity": "dim_customers.active_flag",
    }
    assert detail["dedup_key"] == "glossary:dim_customers.active_flag"
    assert detail["correlation"] == {"trace_id": "trace-123"}
    assert detail["routing_policy"]["priority"] == 50
    assert "created_at" in detail
    # store/runtime fields (not part of the wire ContextRequest contract) are still present too
    assert detail["status"] == "asked"  # a real expert is registered, so routing fires inline
    assert detail["subscribers"] == ["bi.assistant"]


def test_reject_proposal_over_http(client_and_transport) -> None:
    client, transport = client_and_transport

    response = client.post(
        "/api/v1/requests",
        json={
            "kind": "glossary.definition",
            "topic": "revenue metrics",
            "question": "What does active customer mean?",
            "consumer": "bi.assistant",
        },
    )
    request_id = response.json()["id"]
    transport.push_reply("not sure, ask someone else")

    proposals = client.get("/api/v1/proposals?status=pending").json()
    proposal_id = proposals[0]["id"]

    response = client.post(
        f"/api/v1/proposals/{proposal_id}/reject",
        json={"decided_by": "alice", "reason": "not accurate"},
    )
    assert response.status_code == 200

    detail = client.get(f"/api/v1/requests/{request_id}").json()
    assert detail["status"] == "rejected"


def test_consumer_registration_round_trip(client_and_transport) -> None:
    client, _ = client_and_transport
    response = client.post(
        "/api/v1/consumers",
        json={
            "id": "webhook.consumer",
            "handles_kinds": ["freeform.answer"],
            "delivery": {"type": "webhook", "url": "https://example.com/hook"},
        },
    )
    assert response.status_code == 201

    response = client.delete("/api/v1/consumers/webhook.consumer")
    assert response.status_code == 204


def test_accept_unknown_proposal_returns_404(client_and_transport) -> None:
    client, _ = client_and_transport
    response = client.post("/api/v1/proposals/nonexistent/accept", json={"decided_by": "alice"})
    assert response.status_code == 404


def test_slack_events_route_is_unconfigured_without_slack_transport(client_and_transport) -> None:
    client, _ = client_and_transport
    response = client.post("/api/v1/slack/events", json={"type": "url_verification"})
    assert response.status_code == 501


def test_slack_events_route_is_wired_up_with_slack_transport(config_path: Path) -> None:
    from shouldertap.engine.transports.slack import SlackTransport

    transport = SlackTransport(
        bot_token="xoxb-fake", signing_secret="fake-secret", verify_token=False
    )
    app = create_app(config_path, transport=transport)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {API_TOKEN}"})
        response = client.post("/api/v1/slack/events", json={"type": "url_verification"})
    # Not 501 -- the route is wired to Bolt's own handler now, which rejects the unsigned
    # request on its own terms rather than reporting "unconfigured".
    assert response.status_code != 501
