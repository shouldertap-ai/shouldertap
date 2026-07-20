"""Spec §11: "the universal escape hatch" -- POST the accepted proposal to a URL."""

from __future__ import annotations

import httpx

from shouldertap.adapters.base import WriteResult
from shouldertap.engine.contracts import ContextProposal


class WebhookAdapter:
    def __init__(self, url: str, *, timeout: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout

    def on_accepted(self, proposal: ContextProposal) -> WriteResult:
        try:
            response = httpx.post(
                self._url, json=proposal.model_dump(mode="json"), timeout=self._timeout
            )
            response.raise_for_status()
            return WriteResult(success=True)
        except Exception as e:
            return WriteResult(success=False, detail=str(e))
