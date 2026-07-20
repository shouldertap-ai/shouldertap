from datetime import datetime

import httpx
import pytest

from shouldertap.adapters.openmetadata import OpenMetadataAdapter, provenance_footer
from shouldertap.engine.contracts import ContextProposal, Provenance


def _proposal(*, kind: str = "glossary.definition", structured=None) -> ContextProposal:
    return ContextProposal(
        request_id="req_1",
        kind=kind,
        answer="paying accounts active in 90d",
        structured=structured,
        provenance=Provenance(
            expert_id="U1",
            expert_name="Dana Kim",
            answered_via="console",
            answered_at=datetime(2026, 7, 20),
        ),
        consumer="bi.assistant",
    )


def test_provenance_footer_format() -> None:
    footer = provenance_footer(_proposal())
    assert footer == "Source: Dana Kim via ShoulderTap, 2026-07-20"


def test_on_accepted_patches_glossary_term(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_patch(url, headers, json, timeout):
        calls.append((url, headers, json))
        return httpx.Response(200, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx, "patch", fake_patch)

    adapter = OpenMetadataAdapter(
        host="https://om.example.com",
        token="jwt-token",
        entity_fqn_resolver=lambda proposal: "dim_customers.active_flag",
    )
    proposal = _proposal(structured={"definition": "paying accounts active in 90d"})
    result = adapter.on_accepted(proposal)

    assert result.success is True
    assert result.detail == "dim_customers.active_flag"
    url, headers, body = calls[0]
    assert url == "https://om.example.com/api/v1/glossaryTerms/name/dim_customers.active_flag"
    assert headers["Authorization"] == "Bearer jwt-token"
    assert "paying accounts active in 90d" in body[0]["value"]
    assert "Source: Dana Kim via ShoulderTap, 2026-07-20" in body[0]["value"]


def test_on_accepted_falls_back_to_raw_answer_when_unstructured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        httpx,
        "patch",
        lambda url, headers, json, timeout: (
            calls.append(json),
            httpx.Response(200, request=httpx.Request("PATCH", url)),
        )[1],
    )

    adapter = OpenMetadataAdapter(
        host="https://om.example.com",
        token="jwt-token",
        entity_fqn_resolver=lambda proposal: "some.entity",
    )
    result = adapter.on_accepted(_proposal(structured=None))

    assert result.success is True
    assert "paying accounts active in 90d" in calls[0][0]["value"]


def test_on_accepted_rejects_non_glossary_kind() -> None:
    adapter = OpenMetadataAdapter(
        host="https://om.example.com", token="jwt-token", entity_fqn_resolver=lambda p: "x"
    )
    result = adapter.on_accepted(_proposal(kind="freeform.answer"))
    assert result.success is False
    assert "unsupported kind" in (result.detail or "")


def test_on_accepted_fails_when_resolver_returns_none() -> None:
    adapter = OpenMetadataAdapter(
        host="https://om.example.com", token="jwt-token", entity_fqn_resolver=lambda p: None
    )
    result = adapter.on_accepted(_proposal())
    assert result.success is False
    assert "no entity FQN" in (result.detail or "")


def test_on_accepted_returns_failure_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url, headers, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "patch", fake_patch)

    adapter = OpenMetadataAdapter(
        host="https://om.example.com", token="jwt-token", entity_fqn_resolver=lambda p: "x"
    )
    result = adapter.on_accepted(_proposal())
    assert result.success is False
