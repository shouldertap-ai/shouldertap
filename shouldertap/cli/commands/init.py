"""`shtap init` -- spec §13: writes shouldertap.yaml, prints Slack app setup instructions."""

from __future__ import annotations

from pathlib import Path

import click

from shouldertap.cli.options import config_option

# A URL, not a repo-relative path: someone who ran `pip install shouldertap` has no checkout,
# so pointing them at "slack/manifest.yaml" would be a dead end.
SLACK_MANIFEST_URL = "https://github.com/shouldertap-ai/shouldertap/blob/main/slack/manifest.yaml"

_CONFIG_TEMPLATE = """org:
  name: "{org_name}"
  timezone: "UTC"

server:
  port: 8776
  api_token_env: "SHOULDERTAP_API_TOKEN"

defaults:
  escalation_after: 2h
  give_up_after: 24h
  max_open_asks_per_expert: 3
  max_asks_per_expert_per_day: 5

experts: []

topics: {{}}
"""

_ENV_EXAMPLE = """ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
SHOULDERTAP_API_TOKEN=
"""


@click.command()
@config_option
@click.option("--org-name", prompt="Organization name", default="My Org", show_default=True)
def init(config_path: Path, org_name: str) -> None:
    """Write a starting shouldertap.yaml and .env.example."""
    if config_path.exists():
        click.confirm(f"{config_path} already exists. Overwrite?", abort=True)
    config_path.write_text(_CONFIG_TEMPLATE.format(org_name=org_name))

    env_example_path = config_path.parent / ".env.example"
    if not env_example_path.exists():
        env_example_path.write_text(_ENV_EXAMPLE)

    click.echo(f"Wrote {config_path}")
    click.echo(f"Wrote {env_example_path}")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. Copy .env.example to .env and fill in real values (.env is gitignored).")
    click.echo(
        "  2. (Optional, for real Slack DMs) Create a Slack app at https://api.slack.com/apps "
        '-> "Create New App" -> "From an app manifest", pasting in the manifest from '
        f"{SLACK_MANIFEST_URL} -- then copy the Bot User OAuth Token into SLACK_BOT_TOKEN and "
        "the Signing Secret into SLACK_SIGNING_SECRET."
    )
    click.echo(f"  3. Add your experts to the `experts:` section of {config_path}.")
    click.echo(
        "  4. Run `shtap serve --transport console` for a zero-Slack demo, or "
        "`shtap serve --transport slack` once your Slack app is set up."
    )
