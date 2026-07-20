from datetime import time, timedelta
from pathlib import Path

from shouldertap.engine.registry import load_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_loads_example_config_at_repo_root() -> None:
    config = load_config(_REPO_ROOT / "shouldertap.yaml")

    assert config.org.name == "Acme Data Team"
    assert config.llm is not None
    assert config.llm.model == "claude-sonnet-4-6"
    assert config.slack is not None
    assert config.server.port == 8776
    assert config.defaults.escalation_after == timedelta(hours=2)
    assert config.defaults.give_up_after == timedelta(hours=24)
    assert config.defaults.quiet_hours == (time(18, 0), time(9, 0))
    assert config.defaults.max_open_asks_per_expert == 3
    assert len(config.experts) == 2
    assert config.expert_by_id("U0123ABC") is not None
    assert config.topics["revenue metrics"].fallback == "U0456DEF"


def test_env_var_resolution(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "shouldertap.yaml"
    config_path.write_text(
        """
org:
  name: Test Org
llm:
  model: claude-sonnet-4-6
  api_key_env: MY_LLM_KEY
slack:
  bot_token_env: MY_BOT_TOKEN
  signing_secret_env: MY_SIGNING_SECRET
server:
  api_token_env: MY_API_TOKEN
"""
    )
    monkeypatch.setenv("MY_LLM_KEY", "sk-test-llm")
    monkeypatch.setenv("MY_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("MY_SIGNING_SECRET", "shh")
    monkeypatch.setenv("MY_API_TOKEN", "bearer-test")

    config = load_config(config_path)

    assert config.llm_api_key() == "sk-test-llm"
    assert config.slack_bot_token() == "xoxb-test"
    assert config.slack_signing_secret() == "shh"
    assert config.api_token() == "bearer-test"


def test_missing_optional_sections_resolve_to_none(tmp_path: Path) -> None:
    config_path = tmp_path / "shouldertap.yaml"
    config_path.write_text("org:\n  name: Minimal Org\n")

    config = load_config(config_path)

    assert config.llm is None
    assert config.llm_api_key() is None
    assert config.slack is None
    assert config.defaults.escalation_after == timedelta(hours=2)
