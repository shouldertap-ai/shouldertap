from shouldertap.engine.transports.console import ConsoleTransport
from shouldertap.engine.transports.types import IncomingReply


def test_send_ask_returns_delivery_result_with_thread_ref() -> None:
    transport = ConsoleTransport(interactive=False)
    result = transport.send_ask(expert_id="U1", expert_name="Dana", message="What is X?")
    assert result.thread_ref
    assert transport.sent_asks == [result]


def test_push_reply_invokes_registered_handler() -> None:
    transport = ConsoleTransport(interactive=False)
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    result = transport.send_ask(expert_id="U1", expert_name="Dana", message="What is X?")
    transport.push_reply("paying accounts with activity in 90d")

    assert len(received) == 1
    assert received[0].thread_ref == result.thread_ref
    assert received[0].expert_id == "U1"
    assert received[0].text == "paying accounts with activity in 90d"


def test_push_reply_without_explicit_thread_ref_targets_most_recent_ask() -> None:
    transport = ConsoleTransport(interactive=False)
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    transport.send_ask(expert_id="U1", expert_name="Dana", message="first?")
    second = transport.send_ask(expert_id="U2", expert_name="Marco", message="second?")
    transport.push_reply("answering the latest one")

    assert len(received) == 1
    assert received[0].thread_ref == second.thread_ref
    assert received[0].expert_id == "U2"


def test_amendment_reply_on_same_thread_is_still_delivered() -> None:
    """A thread must stay deliverable after the first reply -- spec §7.5 amendments."""
    transport = ConsoleTransport(interactive=False)
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    result = transport.send_ask(expert_id="U1", expert_name="Dana", message="What is X?")
    transport.push_reply("first answer")
    transport.push_reply("actually, one more caveat", thread_ref=result.thread_ref)

    assert len(received) == 2
    assert received[1].text == "actually, one more caveat"


def test_unknown_thread_ref_is_silently_ignored() -> None:
    transport = ConsoleTransport(interactive=False)
    received: list[IncomingReply] = []
    transport.register_reply_handler(received.append)

    transport.push_reply("reply to nothing", thread_ref="nonexistent")

    assert received == []


def test_send_notification_is_recorded() -> None:
    transport = ConsoleTransport(interactive=False)
    transport.send_notification(expert_id="U1", message="Your answer was accepted!")
    assert transport.sent_notifications == [("U1", "Your answer was accepted!")]
