"""`shtap ask` -- spec §13: "submit a request from the terminal (consumer id `cli`), then poll
and print the answer when accepted. This is the 60-second demo command; make its output
delightful."
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import click
import httpx

from shouldertap.cli.http_client import build_client
from shouldertap.cli.options import config_option

_TERMINAL_STATUSES = frozenset({"accepted", "rejected", "failed"})


@click.command()
@config_option
@click.argument("question")
@click.option("--topic", required=True, help="Routing key for the expert registry.")
@click.option("--kind", default="freeform.answer", show_default=True)
@click.option("--poll-interval", default=3.0, show_default=True, help="Seconds between polls.")
@click.option(
    "--timeout", default=300.0, show_default=True, help="Give up polling after this long."
)
def ask(
    config_path: Path, question: str, topic: str, kind: str, poll_interval: float, timeout: float
) -> None:
    """Ask a question and wait here for the human answer."""
    with build_client(config_path) as client:
        response = client.post(
            "/requests",
            json={"question": question, "topic": topic, "kind": kind, "consumer": "cli"},
        )
        response.raise_for_status()
        body = response.json()
        request_id = body["id"]
        click.echo(f"Submitted {request_id} (status: {body['status']})")

        if body["status"] in _TERMINAL_STATUSES:
            _print_result(client, request_id)
            return

        click.echo("Waiting for a human expert to reply...")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            detail = client.get(f"/requests/{request_id}").json()
            if detail["status"] in _TERMINAL_STATUSES:
                _print_result(client, request_id, detail=detail)
                return
            time.sleep(poll_interval)

        click.echo(
            f"Timed out after {timeout:.0f}s waiting for an answer. Check `shtap queue` / "
            f"`shtap audit {request_id}` for status."
        )


def _print_result(
    client: httpx.Client, request_id: str, detail: dict[str, Any] | None = None
) -> None:
    detail = detail or client.get(f"/requests/{request_id}").json()
    status = detail["status"]

    if status == "accepted" and detail.get("proposal"):
        proposal = detail["proposal"]
        click.echo()
        click.echo(f"✅ Answer (from {proposal['provenance']['expert_name']}):")
        click.echo(f"   {proposal['answer']}")
        if proposal.get("structured"):
            click.echo(f"   structured: {proposal['structured']}")
    elif status == "rejected":
        click.echo("The proposed answer was rejected during review.")
    elif status == "failed":
        click.echo(f"Request failed: {detail.get('failure_reason')}")
    else:
        click.echo(f"Request {request_id} is still {status}.")
