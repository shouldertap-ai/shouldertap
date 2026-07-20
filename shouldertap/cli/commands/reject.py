"""`shtap reject` -- spec §13."""

from __future__ import annotations

import getpass
from pathlib import Path

import click

from shouldertap.cli.http_client import build_client
from shouldertap.cli.options import config_option


@click.command()
@config_option
@click.argument("proposal_id")
@click.option("--reason", required=True)
@click.option("--decided-by", default=None, help="Defaults to the OS username.")
def reject(config_path: Path, proposal_id: str, reason: str, decided_by: str | None) -> None:
    """Reject a pending proposal."""
    decided_by = decided_by or getpass.getuser()
    with build_client(config_path) as client:
        response = client.post(
            f"/proposals/{proposal_id}/reject",
            json={"decided_by": decided_by, "reason": reason},
        )
    if response.status_code == 404:
        raise click.ClickException("Proposal not found or not pending.")
    response.raise_for_status()
    click.echo(f"Rejected {proposal_id} (decided_by={decided_by}).")
