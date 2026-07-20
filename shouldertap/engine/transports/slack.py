"""Spec §7.1/§7.5: Bolt-based DM delivery + reply capture. Implements the same Transport
protocol as console.py so capture.py never branches on transport.

Required Slack app scopes (spec §7.1): chat:write, im:history, im:write, users:read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from slack_bolt import App

from shouldertap.engine.clock import utcnow
from shouldertap.engine.transports.types import DeliveryResult, IncomingReply

logger = logging.getLogger(__name__)


class SlackTransport:
    name = "slack_dm"

    def __init__(self, *, bot_token: str, signing_secret: str, verify_token: bool = True) -> None:
        self.app = App(
            token=bot_token,
            signing_secret=signing_secret,
            token_verification_enabled=verify_token,
        )
        self._handler: Callable[[IncomingReply], None] | None = None
        self._open_threads: dict[str, str] = {}  # thread_ref (message ts) -> expert_id
        self._last_thread_ref_by_expert: dict[str, str] = {}
        self._register_listeners()

    def register_reply_handler(self, handler: Callable[[IncomingReply], None]) -> None:
        self._handler = handler

    def send_ask(self, *, expert_id: str, expert_name: str, message: str) -> DeliveryResult:
        response = self.app.client.chat_postMessage(channel=expert_id, text=message)
        thread_ref = str(response["ts"])
        self._open_threads[thread_ref] = expert_id
        self._last_thread_ref_by_expert[expert_id] = thread_ref
        return DeliveryResult(thread_ref=thread_ref)

    def send_notification(self, *, expert_id: str, message: str) -> None:
        self.app.client.chat_postMessage(channel=expert_id, text=message)

    def _register_listeners(self) -> None:
        @self.app.event("message")
        def _on_message(event: dict[str, Any]) -> None:
            self._handle_message_event(event)

    def _handle_message_event(self, event: dict[str, Any]) -> None:
        """Only real, human-typed DMs to the bot are replies -- ignore other channel types,
        edits/deletes (`subtype` set), and the bot's own messages.
        """
        if event.get("channel_type") != "im":
            return
        if event.get("subtype") is not None or event.get("bot_id") is not None:
            return
        user = event.get("user")
        text = event.get("text")
        if not user or not text:
            return

        thread_ref = event.get("thread_ts") or self._last_thread_ref_by_expert.get(user)
        if thread_ref is None or thread_ref not in self._open_threads:
            logger.info("ignoring Slack DM from %s with no matching open ask", user)
            return

        if self._handler is not None:
            self._handler(
                IncomingReply(
                    thread_ref=thread_ref, expert_id=user, text=text, received_at=utcnow()
                )
            )
