"""Transport protocol (build plan): deliberately dumb I/O, not aware of quiet-hours, rate
limits, or escalation -- that's asker.py's job. console.py and slack.py both implement this
identically so capture.py never branches on transport.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from shouldertap.engine.transports.types import DeliveryResult, IncomingReply

ReplyHandler = Callable[[IncomingReply], None]


class Transport(Protocol):
    name: str  # e.g. "console" | "slack_dm" -- recorded as Provenance.answered_via

    def send_ask(self, *, expert_id: str, expert_name: str, message: str) -> DeliveryResult: ...

    def send_notification(self, *, expert_id: str, message: str) -> None: ...

    def register_reply_handler(self, handler: ReplyHandler) -> None: ...
