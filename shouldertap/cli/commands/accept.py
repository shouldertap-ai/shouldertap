"""`shtap accept` -- spec §13. No HTTP-authenticated actor exists for a local CLI operator, so
`decided_by` defaults to the OS username (`--decided-by` overrides it).
"""

from __future__ import annotations

import getpass
from pathlib import Path

import click

from shouldertap.cli.http_client import build_client
from shouldertap.cli.options import config_option


@click.command()
@config_option
@click.argument("proposal_id")
@click.option("--decided-by", default=None, help="Defaults to the OS username.")
def accept(config_path: Path, proposal_id: str, decided_by: str | None) -> None:
    """Accept a pending proposal, writing it back to consumers."""
    decided_by = decided_by or getpass.getuser()
    with build_client(config_path) as client:
        response = client.post(f"/proposals/{proposal_id}/accept", json={"decided_by": decided_by})
    if response.status_code == 404:
        raise click.ClickException("Proposal not found or not pending.")
    response.raise_for_status()
    click.echo(f"Accepted {proposal_id} (decided_by={decided_by}).")
