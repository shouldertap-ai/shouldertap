"""Spec §15 criterion 8: "mock Slack transport `--transport console` that prints DMs to
terminal and reads replies from stdin -- build this transport; it also powers demos and CI."

Two modes:
  - interactive=True (the real demo/CLI experience): prints each ask to stdout and reads
    replies from stdin in a background thread. Since a terminal is a single conversation, a
    typed line is treated as a reply to the most recently asked still-open thread.
  - interactive=False (tests/CI, per criterion 8's "it also powers... CI"): nothing reads real
    stdin; tests call `push_reply(...)` directly to simulate a reply arriving.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from shouldertap.engine.clock import utcnow
from shouldertap.engine.ids import new_id
from shouldertap.engine.transports.types import DeliveryResult, IncomingReply


class ConsoleTransport:
    name = "console"

    def __init__(self, *, interactive: bool = True) -> None:
        self._interactive = interactive
        self._handler: Callable[[IncomingReply], None] | None = None
        self._open_asks: dict[str, str] = {}  # thread_ref -> expert_id
        self._last_thread_ref: str | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # Recorded regardless of interactive mode, for test/introspection convenience.
        self.sent_asks: list[DeliveryResult] = []
        self.sent_notifications: list[tuple[str, str]] = []  # (expert_id, message)

    def register_reply_handler(self, handler: Callable[[IncomingReply], None]) -> None:
        self._handler = handler
        if self._interactive and self._reader_thread is None:
            self._reader_thread = threading.Thread(target=self._read_stdin_loop, daemon=True)
            self._reader_thread.start()

    def send_ask(self, *, expert_id: str, expert_name: str, message: str) -> DeliveryResult:
        thread_ref = new_id("consolethread")
        self._open_asks[thread_ref] = expert_id
        self._last_thread_ref = thread_ref
        result = DeliveryResult(thread_ref=thread_ref)
        self.sent_asks.append(result)
        if self._interactive:
            print(f"\n─── ShoulderTap DM to {expert_name} ({expert_id}) ───")
            print(message)
            print("─── (type your reply and press Enter) ───")
            sys.stdout.flush()
        return result

    def send_notification(self, *, expert_id: str, message: str) -> None:
        self.sent_notifications.append((expert_id, message))
        if self._interactive:
            print(f"\n─── ShoulderTap notification to {expert_id} ───\n{message}")
            sys.stdout.flush()

    def push_reply(self, text: str, *, thread_ref: str | None = None) -> None:
        """Programmatic-mode entry point (and what the interactive stdin reader calls too):
        simulate a reply arriving, defaulting to the most recently asked thread. Deliberately
        does not forget the thread after one reply -- a later line in the same thread is a
        legitimate amendment (spec §7.5), and it's capture.py's job, not the transport's, to
        decide first-reply-vs-amendment-vs-ignored.
        """
        ref = thread_ref if thread_ref is not None else self._last_thread_ref
        if ref is None or ref not in self._open_asks:
            return
        expert_id = self._open_asks[ref]
        if self._handler is not None:
            self._handler(
                IncomingReply(thread_ref=ref, expert_id=expert_id, text=text, received_at=utcnow())
            )

    def _read_stdin_loop(self) -> None:
        for line in sys.stdin:
            if self._stop_event.is_set():
                break
            text = line.strip()
            if text:
                self.push_reply(text)

    def stop(self) -> None:
        self._stop_event.set()
