"""Spec §11: adapters are consumer-side helpers, not engine internals -- a consumer's own
`on_proposal_accepted` callback calls one of these. This is the whole documented interface
community adapters need to implement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shouldertap.engine.contracts import ContextProposal


@dataclass
class WriteResult:
    success: bool
    detail: str | None = None


class Adapter(Protocol):
    def on_accepted(self, proposal: ContextProposal) -> WriteResult: ...
