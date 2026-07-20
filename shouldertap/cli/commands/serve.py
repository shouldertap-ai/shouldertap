"""`shtap serve` -- spec §13: run engine + API + UI (default :8776). The one process that
touches the SQLite file directly; everything else (CLI, MCP) is an HTTP client of this.
"""

from __future__ import annotations

from pathlib import Path

import click
import uvicorn

from shouldertap.cli.options import config_option
from shouldertap.engine.registry import load_config
from shouldertap.engine.transports.base import Transport
from shouldertap.engine.transports.console import ConsoleTransport
from shouldertap.engine.transports.slack import SlackTransport
from shouldertap.server.app import create_app


@click.command()
@config_option
@click.option(
    "--transport",
    type=click.Choice(["console", "slack"]),
    default="console",
    show_default=True,
    help="console prints DMs to the terminal and reads replies from stdin (spec criterion 8); "
    "slack requires a `slack:` section in the config plus SLACK_BOT_TOKEN/SLACK_SIGNING_SECRET.",
)
@click.option("--host", default="0.0.0.0", show_default=True)
def serve(config_path: Path, transport: str, host: str) -> None:
    """Run the engine, HTTP API, and approval UI."""
    config = load_config(config_path)

    resolved_transport: Transport
    if transport == "slack":
        if config.slack is None:
            raise click.ClickException(f"{config_path} has no `slack:` section configured.")
        bot_token = config.slack_bot_token()
        signing_secret = config.slack_signing_secret()
        if not bot_token or not signing_secret:
            raise click.ClickException(
                f"{config.slack.bot_token_env} / {config.slack.signing_secret_env} are not set."
            )
        resolved_transport = SlackTransport(bot_token=bot_token, signing_secret=signing_secret)
    else:
        resolved_transport = ConsoleTransport(interactive=True)

    app = create_app(config_path, transport=resolved_transport)
    click.echo(f"ShoulderTap serving on http://{host}:{config.server.port} (transport={transport})")
    uvicorn.run(app, host=host, port=config.server.port)
