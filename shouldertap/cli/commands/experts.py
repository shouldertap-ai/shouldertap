"""`shtap experts` -- spec §13: registry + live state (open asks, muted)."""

from __future__ import annotations

from pathlib import Path

import click

from shouldertap.cli.http_client import build_client
from shouldertap.cli.options import config_option


@click.command(name="experts")
@config_option
def experts_cmd(config_path: Path) -> None:
    """List the expert registry with live state."""
    with build_client(config_path) as client:
        response = client.get("/experts")
        response.raise_for_status()
        rows = response.json()

    if not rows:
        click.echo("No experts configured.")
        return

    for expert in rows:
        muted = " [MUTED]" if expert["muted"] else ""
        click.echo(
            f"{expert['id']}  {expert['name']}{muted}  "
            f"open_asks={expert['open_asks']} asks_today={expert['asks_today']}"
        )
        topics = ", ".join(expert["topics"]) or "(none)"
        click.echo(f"    topics: {topics}")
