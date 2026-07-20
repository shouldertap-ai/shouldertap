"""Shared shapes both console.py and slack.py produce/consume, so capture.py never branches on
which transport delivered a reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class DeliveryResult:
    """`thread_ref` is an opaque per-transport correlation token (a Slack thread_ts, or a
    synthetic id for the console transport) -- asker.py stores it on the request row so a
    later inbound reply can be matched back to the request that's waiting on it.
    """

    thread_ref: str


@dataclass
class IncomingReply:
    thread_ref: str
    expert_id: str
    text: str
    received_at: datetime
