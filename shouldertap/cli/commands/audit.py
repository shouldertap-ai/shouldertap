"""`shtap audit <request_id>` -- spec §13/§10.2: pretty-print the full trail of one tap."""

from __future__ import annotations

from pathlib import Path

import click

from shouldertap.cli.http_client import build_client
from shouldertap.cli.options import config_option


@click.command(name="audit")
@config_option
@click.argument("request_id")
def audit_cmd(config_path: Path, request_id: str) -> None:
    """Print the full audit trail for one tap, end to end."""
    with build_client(config_path) as client:
        response = client.get("/audit", params={"request_id": request_id})
        response.raise_for_status()
        events = response.json()

    if not events:
        click.echo(f"No audit events found for request {request_id}.")
        return

    for event in events:
        detail = f"  {event['detail']}" if event["detail"] else ""
        click.echo(f"{event['ts']}  {event['event']:<24} actor={event['actor']}{detail}")
