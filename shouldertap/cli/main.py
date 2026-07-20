"""`shtap` -- spec §13. Registers every subcommand: init/serve/ask/queue/accept/reject/experts/
audit/mcp.
"""

from __future__ import annotations

import click

from shouldertap.cli.commands.accept import accept
from shouldertap.cli.commands.ask import ask
from shouldertap.cli.commands.audit import audit_cmd
from shouldertap.cli.commands.experts import experts_cmd
from shouldertap.cli.commands.init import init
from shouldertap.cli.commands.mcp import mcp_cmd
from shouldertap.cli.commands.queue import queue_cmd
from shouldertap.cli.commands.reject import reject
from shouldertap.cli.commands.serve import serve


@click.group()
@click.version_option(package_name="shouldertap")
def cli() -> None:
    """ShoulderTap: the missing layer between AI agents and the humans who hold undocumented
    knowledge.
    """


cli.add_command(init)
cli.add_command(serve)
cli.add_command(ask)
cli.add_command(queue_cmd)
cli.add_command(accept)
cli.add_command(reject)
cli.add_command(experts_cmd)
cli.add_command(audit_cmd)
cli.add_command(mcp_cmd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
