import json
from types import SimpleNamespace
from typing import Any

import pytest

from shouldertap.engine.llm import LiteLLMProvider, build_llm_provider
from shouldertap.engine.registry import RegistryConfig


def _config_without_llm() -> RegistryConfig:
    return RegistryConfig.model_validate({"org": {"name": "Test"}})


def _config_with_llm(monkeypatch: pytest.MonkeyPatch) -> RegistryConfig:
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    return RegistryConfig.model_validate(
        {
            "org": {"name": "Test"},
            "llm": {"model": "claude-sonnet-4-6", "api_key_env": "TEST_LLM_KEY"},
        }
    )


def _fake_response(content: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_build_llm_provider_returns_none_when_unconfigured() -> None:
    assert build_llm_provider(_config_without_llm()) is None


def test_build_llm_provider_returns_provider_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = build_llm_provider(_config_with_llm(monkeypatch))
    assert isinstance(provider, LiteLLMProvider)


def test_draft_question_returns_stripped_content(monkeypatch: pytest.MonkeyPatch) -> None:
    import shouldertap.engine.llm as llm_module

    monkeypatch.setattr(
        llm_module.litellm, "completion", lambda **kwargs: _fake_response("  What is X?  ")
    )
    provider = LiteLLMProvider(model="m", api_key="k")
    assert provider.draft_question("prompt") == "What is X?"


def test_draft_question_degrades_to_none_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import shouldertap.engine.llm as llm_module

    def _raise(**kwargs: Any) -> Any:
        raise RuntimeError("network error")

    monkeypatch.setattr(llm_module.litellm, "completion", _raise)
    provider = LiteLLMProvider(model="m", api_key="k")
    assert provider.draft_question("prompt") is None


def test_structure_answer_parses_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    import shouldertap.engine.llm as llm_module

    payload = json.dumps({"structured": {"definition": "x"}, "confidence": 0.8})
    monkeypatch.setattr(llm_module.litellm, "completion", lambda **kwargs: _fake_response(payload))
    provider = LiteLLMProvider(model="m", api_key="k")
    structured, confidence = provider.structure_answer("prompt")
    assert structured == {"definition": "x"}
    assert confidence == 0.8


def test_structure_answer_degrades_to_none_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shouldertap.engine.llm as llm_module

    monkeypatch.setattr(
        llm_module.litellm, "completion", lambda **kwargs: _fake_response("not json")
    )
    provider = LiteLLMProvider(model="m", api_key="k")
    structured, confidence = provider.structure_answer("prompt")
    assert structured is None
    assert confidence is None


def test_structure_answer_degrades_to_none_on_missing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shouldertap.engine.llm as llm_module

    payload = json.dumps({"something_else": True})
    monkeypatch.setattr(llm_module.litellm, "completion", lambda **kwargs: _fake_response(payload))
    provider = LiteLLMProvider(model="m", api_key="k")
    structured, confidence = provider.structure_answer("prompt")
    assert structured is None
    assert confidence is None


def test_structure_answer_degrades_to_none_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import shouldertap.engine.llm as llm_module

    def _raise(**kwargs: Any) -> Any:
        raise RuntimeError("timeout")

    monkeypatch.setattr(llm_module.litellm, "completion", _raise)
    provider = LiteLLMProvider(model="m", api_key="k")
    structured, confidence = provider.structure_answer("prompt")
    assert structured is None
    assert confidence is None
