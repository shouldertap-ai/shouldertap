"""litellm wrapper (spec §2: "LLM calls through a provider-agnostic interface (litellm)").

The provider is `None` whenever `llm:` is absent from shouldertap.yaml, and any call failure
(missing key, network error, malformed response) is caught and degrades to `None`/`(None,
None)` rather than raising -- this is the entire mechanism behind zero-LLM degradation
(acceptance criterion 7): callers (asker.py, capture.py) always handle "no LLM" and "LLM call
failed" identically, so there's no special-casing needed anywhere else.

Prompt construction (loading/rendering the .md templates under engine/asker/prompts/ and
engine/capture/prompts/, injecting kind schemas, etc.) is deliberately NOT this module's job --
callers render a final prompt string and pass it in, keeping this wrapper generic.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import litellm

from shouldertap.engine.registry import RegistryConfig

logger = logging.getLogger(__name__)

_DRAFT_QUESTION_TIMEOUT_SECONDS = 15.0
_STRUCTURE_ANSWER_TIMEOUT_SECONDS = 15.0


class LLMProvider(Protocol):
    def draft_question(self, prompt: str) -> str | None: ...

    def structure_answer(self, prompt: str) -> tuple[dict[str, Any] | None, float | None]: ...


class LiteLLMProvider:
    def __init__(self, model: str, api_key: str | None) -> None:
        self._model = model
        self._api_key = api_key

    def draft_question(self, prompt: str) -> str | None:
        """Spec §7.3: temperature 0.3, single conversational question."""
        try:
            response = litellm.completion(
                model=self._model,
                api_key=self._api_key,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                timeout=_DRAFT_QUESTION_TIMEOUT_SECONDS,
            )
            content = response.choices[0].message.content
            return content.strip() if content else None
        except Exception:
            logger.warning("LLM draft_question call failed; degrading to verbatim", exc_info=True)
            return None

    def structure_answer(self, prompt: str) -> tuple[dict[str, Any] | None, float | None]:
        """Expects the prompt to instruct the model to reply with a JSON object shaped like
        {"structured": {...}, "confidence": 0.0-1.0}. Any failure -- call error, non-JSON
        response, missing keys -- degrades to (None, None); spec §8.2 says the proposal must
        still be created with structured=null rather than the answer being dropped.
        """
        try:
            response = litellm.completion(
                model=self._model,
                api_key=self._api_key,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=_STRUCTURE_ANSWER_TIMEOUT_SECONDS,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                return None, None
            payload = json.loads(content)
            structured = payload.get("structured")
            confidence = payload.get("confidence")
            if not isinstance(structured, dict) or not isinstance(confidence, int | float):
                return None, None
            return structured, float(confidence)
        except Exception:
            logger.warning("LLM structure_answer call failed; degrading to null", exc_info=True)
            return None, None


def build_llm_provider(config: RegistryConfig) -> LLMProvider | None:
    if config.llm is None:
        return None
    api_key = config.llm_api_key()
    return LiteLLMProvider(model=config.llm.model, api_key=api_key)
