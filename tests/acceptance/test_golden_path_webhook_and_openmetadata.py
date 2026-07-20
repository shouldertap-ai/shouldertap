"""Closes a real coverage gap flagged by the spec-compliance audit: spec §15 criterion 1's
literal wording is "... approved via API → `on_proposal_accepted` webhook fired → OpenMetadata
adapter PATCH called with provenance footer → audit trail contains the full enumerated
sequence." The existing golden-path tests (test_facade.py, test_api.py) prove the loop end to
end using an in-process consumer callback, and the webhook POST mechanism / OpenMetadata PATCH
are each solidly unit-tested in isolation (test_delivery.py, test_openmetadata.py) -- but
nothing chained "accept -> real webhook fires -> that webhook's proposal drives a real
OpenMetadata PATCH with a provenance footer" together in one run, which is what criterion 1
actually asks for. This test closes that gap.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from shouldertap.adapters.openmetadata import OpenMetadataAdapter
from shouldertap.engine.clock import ManualClock
from shouldertap.engine.contracts import (
    ConsumerRegistration,
    ContextProposal,
    ContextRequest,
    WebhookDelivery,
)
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.facade import Facade
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.scheduler.core import make_scheduler
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import (
    list_pending_proposals,
    upsert_consumer,
    upsert_expert,
)
from shouldertap.engine.transports.console import ConsoleTransport


class FakeLLM:
    def draft_question(self, prompt: str) -> str | None:
        return "Quick one: what counts as an active customer?"

    def structure_answer(self, prompt: str):
        return {"definition": "paying accounts active in 90 days", "caveats": []}, 0.9


def test_accept_fires_webhook_which_drives_an_openmetadata_patch(tmp_path: Path) -> None:
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    session_factory = make_session_factory(make_engine(db_path))
    scheduler = make_scheduler(db_path)
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    deliverer = ConsumerDeliverer()

    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana Kim", topics=["revenue metrics"], escalation_to=None
        )
        upsert_consumer(
            session,
            ConsumerRegistration(
                id="openmetadata-sync",
                handles_kinds=["glossary.definition"],
                delivery=WebhookDelivery(url="https://sync.example.com/webhook"),
            ),
        )
        session.commit()

    config = RegistryConfig.model_validate({"org": {"name": "Test", "timezone": "UTC"}})
    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=config,
        transport=transport,
        llm_provider=FakeLLM(),
        deliverer=deliverer,
        clock=clock,
    )

    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="openmetadata-sync",
        context={"entity": "dim_customers.active_flag"},
    )
    facade.submit_request(request)
    assert len(transport.sent_asks) == 1

    transport.push_reply("paying accounts active in 90 days")
    with session_factory() as session:
        proposal_id = list_pending_proposals(session)[0].id

    # Intercept the real webhook POST delivery.py makes on accept -- this is the actual
    # "on_proposal_accepted webhook fired" step, not a stand-in in-process callback.
    captured_webhook_calls: list[dict] = []

    def fake_post(url: str, json: dict, timeout: float) -> httpx.Response:
        captured_webhook_calls.append({"url": url, "json": json})
        return httpx.Response(200, request=httpx.Request("POST", url))

    import shouldertap.engine.delivery as delivery_module

    original_post = delivery_module.httpx.post
    delivery_module.httpx.post = fake_post  # type: ignore[assignment]
    try:
        result = facade.accept_proposal(proposal_id=proposal_id, decided_by="alice")
    finally:
        delivery_module.httpx.post = original_post  # type: ignore[assignment]

    assert result is not None
    assert len(captured_webhook_calls) == 1
    webhook_call = captured_webhook_calls[0]
    assert webhook_call["url"] == "https://sync.example.com/webhook"
    assert webhook_call["json"]["event"] == "proposal_accepted"

    # This is exactly what a real webhook consumer (examples/openmetadata_sync/) does with the
    # payload it receives: reconstruct the ContextProposal and hand it to the adapter.
    accepted_proposal = ContextProposal.model_validate(webhook_call["json"]["proposal"])

    captured_patch_calls: list[dict] = []

    def fake_patch(url: str, headers: dict, json: object, timeout: float) -> httpx.Response:
        captured_patch_calls.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(200, request=httpx.Request("PATCH", url))

    import shouldertap.adapters.openmetadata as openmetadata_module

    monkeypatch_target = openmetadata_module.httpx
    original_patch = monkeypatch_target.patch
    monkeypatch_target.patch = fake_patch  # type: ignore[assignment]
    try:
        adapter = OpenMetadataAdapter(
            host="https://om.example.com",
            token="jwt-token",
            entity_fqn_resolver=lambda proposal: "dim_customers.active_flag",
        )
        write_result = adapter.on_accepted(accepted_proposal)
    finally:
        monkeypatch_target.patch = original_patch  # type: ignore[assignment]

    assert write_result.success is True
    assert len(captured_patch_calls) == 1
    patch_call = captured_patch_calls[0]
    assert (
        patch_call["url"]
        == "https://om.example.com/api/v1/glossaryTerms/name/dim_customers.active_flag"
    )
    body_value = patch_call["json"][0]["value"]
    assert "paying accounts active in 90 days" in body_value
    assert "via ShoulderTap" in body_value  # the provenance footer
    assert "Dana Kim" in body_value
