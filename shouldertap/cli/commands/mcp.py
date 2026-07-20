"""`shtap mcp` -- spec §12/§13: run the ShoulderTap MCP server over stdio."""

from __future__ import annotations

from pathlib import Path

import click

from shouldertap.cli.options import config_option
from shouldertap.mcp.server import main as run_mcp_server


@click.command(name="mcp")
@config_option
def mcp_cmd(config_path: Path) -> None:
    """Run the ask_expert / check_answer MCP server over stdio."""
    run_mcp_server(config_path)
