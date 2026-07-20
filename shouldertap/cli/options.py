"""Shared click options across command modules."""

from __future__ import annotations

from pathlib import Path

import click

config_option = click.option(
    "--config",
    "config_path",
    default="shouldertap.yaml",
    type=click.Path(path_type=Path),
    show_default=True,
    help="Path to shouldertap.yaml.",
)
