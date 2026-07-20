from shouldertap.engine.clock import utcnow
from shouldertap.engine.contracts import (
    ConsumerRegistration,
    ContextProposal,
    Provenance,
    Reason,
    ReasonCode,
    WebhookDelivery,
)
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.store.models import ConsumerRow


def _webhook_consumer(url: str) -> ConsumerRow:
    registration = ConsumerRegistration(
        id="webhook.consumer", handles_kinds=["freeform.answer"], delivery=WebhookDelivery(url=url)
    )
    return ConsumerRow(
        id=registration.id,
        handles_kinds=registration.handles_kinds,
        delivery_type="webhook",
        webhook_url=url,
    )


def _proposal() -> ContextProposal:
    return ContextProposal(
        request_id="req_1",
        kind="freeform.answer",
        answer="paying accounts active in 90d",
        provenance=Provenance(
            expert_id="U1", expert_name="Dana", answered_via="console", answered_at=utcnow()
        ),
        consumer="webhook.consumer",
    )


def test_in_process_delivery_calls_registered_callback() -> None:
    deliverer = ConsumerDeliverer()
    calls = []

    class Callbacks:
        def on_proposal(self, proposal):
            pass

        def on_proposal_accepted(self, proposal):
            calls.append(proposal)

        def on_proposal_rejected(self, proposal, reason):
            pass

        def on_request_failed(self, request_id, reason):
            pass

    deliverer.register_in_process("c1", Callbacks())
    consumer_row = ConsumerRow(id="c1", handles_kinds=[], delivery_type="inprocess")
    proposal = _proposal()
    deliverer.deliver_accepted(consumer_row, proposal)

    assert calls == [proposal]


def test_webhook_delivery_posts_json(monkeypatch) -> None:
    import shouldertap.engine.delivery as delivery_module

    posted = {}

    def fake_post(url, json, timeout):
        posted["url"] = url
        posted["json"] = json
        posted["timeout"] = timeout

    monkeypatch.setattr(delivery_module.httpx, "post", fake_post)

    deliverer = ConsumerDeliverer()
    consumer_row = _webhook_consumer("https://example.com/hook")
    proposal = _proposal()
    deliverer.deliver_accepted(consumer_row, proposal)

    assert posted["url"] == "https://example.com/hook"
    assert posted["json"]["event"] == "proposal_accepted"


def test_webhook_delivery_failure_does_not_raise(monkeypatch) -> None:
    import shouldertap.engine.delivery as delivery_module

    def fake_post(url, json, timeout):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(delivery_module.httpx, "post", fake_post)

    deliverer = ConsumerDeliverer()
    consumer_row = _webhook_consumer("https://example.com/hook")
    deliverer.deliver_accepted(consumer_row, _proposal())  # must not raise


def test_no_webhook_url_and_no_in_process_registration_is_a_silent_no_op() -> None:
    deliverer = ConsumerDeliverer()
    consumer_row = ConsumerRow(
        id="ghost", handles_kinds=[], delivery_type="webhook", webhook_url=None
    )
    deliverer.deliver_accepted(consumer_row, _proposal())  # must not raise


def test_deliver_request_failed_via_in_process() -> None:
    deliverer = ConsumerDeliverer()
    calls = []

    class Callbacks:
        def on_proposal(self, proposal):
            pass

        def on_proposal_accepted(self, proposal):
            pass

        def on_proposal_rejected(self, proposal, reason):
            pass

        def on_request_failed(self, request_id, reason):
            calls.append((request_id, reason))

    deliverer.register_in_process("c1", Callbacks())
    consumer_row = ConsumerRow(id="c1", handles_kinds=[], delivery_type="inprocess")
    reason = Reason(code=ReasonCode.TIMEOUT)
    deliverer.deliver_request_failed(consumer_row, "req_1", reason)

    assert calls == [("req_1", reason)]
