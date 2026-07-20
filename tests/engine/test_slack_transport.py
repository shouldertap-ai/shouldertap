from shouldertap.engine.transports.slack import SlackTransport
from shouldertap.engine.transports.types import IncomingReply


def _transport() -> SlackTransport:
    return SlackTransport(bot_token="xoxb-fake", signing_secret="fake-secret", verify_token=False)


def test_send_ask_posts_dm_and_returns_thread_ref(monkeypatch) -> None:
    transport = _transport()
    calls = []

    def fake_post_message(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "ts": "1721500000.123456"}

    monkeypatch.setattr(transport.app.client, "chat_postMessage", fake_post_message)

    result = transport.send_ask(expert_id="U1", expert_name="Dana", message="What is X?")

    assert result.thread_ref == "1721500000.123456"
    assert calls == [{"channel": "U1", "text": "What is X?"}]


def test_send_notification_posts_dm(monkeypatch) -> None:
    transport = _transport()
    calls = []
    monkeypatch.setattr(
        transport.app.client, "chat_postMessage", lambda **kwargs: calls.append(kwargs)
    )

    transport.send_notification(expert_id="U1", message="Thanks!")

    assert calls == [{"channel": "U1", "text": "Thanks!"}]


def test_message_event_translates_to_incoming_reply(monkeypatch) -> None:
    transport = _transport()
    monkeypatch.setattr(
        transport.app.client,
        "chat_postMessage",
        lambda **kwargs: {"ok": True, "ts": "100.001"},
    )
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    transport.send_ask(expert_id="U1", expert_name="Dana", message="What is X?")

    transport._handle_message_event(
        {
            "type": "message",
            "channel_type": "im",
            "user": "U1",
            "text": "paying accounts active in 90d",
            "thread_ts": "100.001",
        }
    )

    assert len(received) == 1
    assert received[0].thread_ref == "100.001"
    assert received[0].expert_id == "U1"
    assert received[0].text == "paying accounts active in 90d"


def test_message_event_without_thread_ts_falls_back_to_most_recent_ask(monkeypatch) -> None:
    transport = _transport()
    monkeypatch.setattr(
        transport.app.client,
        "chat_postMessage",
        lambda **kwargs: {"ok": True, "ts": "200.001"},
    )
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    transport.send_ask(expert_id="U1", expert_name="Dana", message="What is X?")

    transport._handle_message_event(
        {"type": "message", "channel_type": "im", "user": "U1", "text": "an answer"}
    )

    assert len(received) == 1
    assert received[0].thread_ref == "200.001"


def test_message_event_ignores_non_dm_channels(monkeypatch) -> None:
    transport = _transport()
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    transport._handle_message_event(
        {"type": "message", "channel_type": "channel", "user": "U1", "text": "hello"}
    )

    assert received == []


def test_message_event_ignores_bot_and_subtype_messages(monkeypatch) -> None:
    transport = _transport()
    monkeypatch.setattr(
        transport.app.client,
        "chat_postMessage",
        lambda **kwargs: {"ok": True, "ts": "300.001"},
    )
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)
    transport.send_ask(expert_id="U1", expert_name="Dana", message="What is X?")

    transport._handle_message_event(
        {
            "type": "message",
            "channel_type": "im",
            "user": "U1",
            "text": "edited",
            "subtype": "message_changed",
            "thread_ts": "300.001",
        }
    )
    transport._handle_message_event(
        {
            "type": "message",
            "channel_type": "im",
            "bot_id": "B1",
            "text": "bot echo",
            "thread_ts": "300.001",
        }
    )

    assert received == []


def test_message_event_with_no_matching_open_ask_is_ignored(monkeypatch) -> None:
    transport = _transport()
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    transport._handle_message_event(
        {
            "type": "message",
            "channel_type": "im",
            "user": "U1",
            "text": "unsolicited message",
            "thread_ts": "nonexistent",
        }
    )

    assert received == []
