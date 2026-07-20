"""`shtap queue` -- spec §13: list pending proposals."""

from __future__ import annotations

from pathlib import Path

import click

from shouldertap.cli.http_client import build_client
from shouldertap.cli.options import config_option


@click.command(name="queue")
@config_option
def queue_cmd(config_path: Path) -> None:
    """List pending proposals awaiting human review."""
    with build_client(config_path) as client:
        response = client.get("/proposals", params={"status": "pending"})
        response.raise_for_status()
        proposals = response.json()

    if not proposals:
        click.echo("Nothing pending review.")
        return

    for proposal in proposals:
        click.echo(f"{proposal['id']}  [{proposal['kind']}]  request={proposal['request_id']}")
        expert_name = proposal["provenance"]["expert_name"]
        answer_preview = proposal["answer"][:100]
        click.echo(f"    from {expert_name}: {answer_preview}")
