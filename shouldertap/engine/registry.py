"""Loads and validates shouldertap.yaml (spec §6) into a typed config. The YAML never inlines
secrets -- it only references env-var *names* (`api_key_env`, `bot_token_env`, ...); actual
values are resolved from the environment (optionally loaded from a `.env` file next to the
config) at the point of use.
"""

from __future__ import annotations

import os
import re
from datetime import time, timedelta
from pathlib import Path
from typing import Annotated, Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, BeforeValidator, Field

_SHORTHAND_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h|d)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_shorthand_duration(value: Any) -> Any:
    """shouldertap.yaml's `defaults:` section uses human shorthand ("2h", "24h") rather than
    the ISO-8601 durations the wire-level ContextRequest.routing_policy uses ("PT2H") --
    these are deliberately two different formats for two different audiences (spec §4.1 vs
    §6). Pydantic's built-in timedelta parsing only understands the ISO form, so this
    before-validator handles the YAML shorthand; anything else is passed through unchanged
    (timedelta, ISO string, number of seconds) for Pydantic to parse as usual.
    """
    if isinstance(value, str):
        match = _SHORTHAND_DURATION.match(value.strip())
        if match:
            amount, unit = match.groups()
            return timedelta(seconds=float(amount) * _UNIT_SECONDS[unit])
    return value


ShorthandTimedelta = Annotated[timedelta, BeforeValidator(_parse_shorthand_duration)]


class OrgConfig(BaseModel):
    name: str
    timezone: str = "UTC"


class LLMConfig(BaseModel):
    model: str
    api_key_env: str


class SlackConfig(BaseModel):
    bot_token_env: str
    signing_secret_env: str


class ServerConfig(BaseModel):
    """Not shown in the spec's own abbreviated §6 example -- §5 requires a static bearer
    token from config but never gives it a YAML home. Added here, following the same
    never-inline-secrets pattern as the rest of the file (see build plan)."""

    port: int = 8776
    api_token_env: str = "SHOULDERTAP_API_TOKEN"


class DefaultsConfig(BaseModel):
    escalation_after: ShorthandTimedelta = timedelta(hours=2)
    give_up_after: ShorthandTimedelta = timedelta(hours=24)
    quiet_hours: tuple[time, time] | None = None
    max_open_asks_per_expert: int = 3
    max_asks_per_expert_per_day: int = 5


class ExpertConfig(BaseModel):
    id: str
    name: str
    topics: list[str] = Field(default_factory=list)
    escalation_to: str | None = None


class TopicConfig(BaseModel):
    fallback: str | None = None


class RegistryConfig(BaseModel):
    org: OrgConfig
    llm: LLMConfig | None = None
    slack: SlackConfig | None = None
    server: ServerConfig = Field(default_factory=ServerConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    experts: list[ExpertConfig] = Field(default_factory=list)
    topics: dict[str, TopicConfig] = Field(default_factory=dict)

    def llm_api_key(self) -> str | None:
        if self.llm is None:
            return None
        return os.environ.get(self.llm.api_key_env)

    def slack_bot_token(self) -> str | None:
        if self.slack is None:
            return None
        return os.environ.get(self.slack.bot_token_env)

    def slack_signing_secret(self) -> str | None:
        if self.slack is None:
            return None
        return os.environ.get(self.slack.signing_secret_env)

    def api_token(self) -> str | None:
        return os.environ.get(self.server.api_token_env)

    def expert_by_id(self, expert_id: str) -> ExpertConfig | None:
        return next((e for e in self.experts if e.id == expert_id), None)


def load_config(config_path: Path) -> RegistryConfig:
    env_path = config_path.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()  # fall back to the default search (cwd and parents)

    with config_path.open() as f:
        raw = yaml.safe_load(f) or {}
    return RegistryConfig.model_validate(raw)


def save_config(config: RegistryConfig, config_path: Path) -> None:
    """Spec §5: `PUT /experts` rewrites the config file (it remains the source of truth, not
    the DB). Safe to dump in full -- the config model only ever holds env-var *names*, never
    secret values.
    """
    payload = config.model_dump(mode="json", exclude_none=True)
    with config_path.open("w") as f:
        yaml.safe_dump(payload, f, sort_keys=False)
