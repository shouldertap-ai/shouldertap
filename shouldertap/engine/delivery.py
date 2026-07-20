"""Reaches a consumer with one of the four callback events (spec §4.3: on_proposal,
on_proposal_accepted, on_proposal_rejected, on_request_failed) via whichever delivery mode that
consumer registered with (§4.3, explicit): a webhook URL, POSTed to; or an SDK in-process
callback, invoked directly. Webhook failures are best-effort -- logged, never allowed to crash
the approval/failure flow that triggered them.
"""

from __future__ import annotations

import logging

import httpx

from shouldertap.engine.contracts import ConsumerCallbacks, ContextProposal, Reason
from shouldertap.engine.store.models import ConsumerRow

logger = logging.getLogger(__name__)

_WEBHOOK_TIMEOUT_SECONDS = 10.0


class ConsumerDeliverer:
    def __init__(self) -> None:
        self._in_process: dict[str, ConsumerCallbacks] = {}

    def register_in_process(self, consumer_id: str, callbacks: ConsumerCallbacks) -> None:
        self._in_process[consumer_id] = callbacks

    def unregister_in_process(self, consumer_id: str) -> None:
        self._in_process.pop(consumer_id, None)

    def deliver_proposal(self, consumer: ConsumerRow, proposal: ContextProposal) -> None:
        """The informational, pre-approval on_proposal callback (spec §4.3)."""
        callbacks = self._in_process.get(consumer.id)
        if callbacks is not None:
            callbacks.on_proposal(proposal)
            return
        self._post_webhook(
            consumer, {"event": "proposal", "proposal": proposal.model_dump(mode="json")}
        )

    def deliver_accepted(self, consumer: ConsumerRow, proposal: ContextProposal) -> None:
        callbacks = self._in_process.get(consumer.id)
        if callbacks is not None:
            callbacks.on_proposal_accepted(proposal)
            return
        self._post_webhook(
            consumer,
            {"event": "proposal_accepted", "proposal": proposal.model_dump(mode="json")},
        )

    def deliver_rejected(
        self, consumer: ConsumerRow, proposal: ContextProposal, reason: str
    ) -> None:
        callbacks = self._in_process.get(consumer.id)
        if callbacks is not None:
            callbacks.on_proposal_rejected(proposal, reason)
            return
        self._post_webhook(
            consumer,
            {
                "event": "proposal_rejected",
                "proposal": proposal.model_dump(mode="json"),
                "reason": reason,
            },
        )

    def deliver_request_failed(
        self, consumer: ConsumerRow, request_id: str, reason: Reason
    ) -> None:
        callbacks = self._in_process.get(consumer.id)
        if callbacks is not None:
            callbacks.on_request_failed(request_id, reason)
            return
        self._post_webhook(
            consumer,
            {
                "event": "request_failed",
                "request_id": request_id,
                "reason": reason.model_dump(mode="json"),
            },
        )

    def _post_webhook(self, consumer: ConsumerRow, payload: dict[str, object]) -> None:
        if not consumer.webhook_url:
            return
        try:
            httpx.post(consumer.webhook_url, json=payload, timeout=_WEBHOOK_TIMEOUT_SECONDS)
        except Exception:
            logger.warning("webhook delivery to consumer %s failed", consumer.id, exc_info=True)
