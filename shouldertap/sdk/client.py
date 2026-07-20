"""Python client SDK (spec §2: "sdk/ # Python client (v0.1); TS client (v0.2)"). Two usage
modes, matching the two consumer delivery modes spec §4.3 defines:

1. `ShoulderTapClient` -- an HTTP client for a consumer running as a separate process from
   `shtap serve` (the common case; mirrors cli/http_client.py's pattern). Submit requests,
   check status, register/unregister as a webhook consumer.
2. `register_in_process` -- for a consumer embedded in the *same* process as the running
   engine (e.g. a script that constructs its own `Facade`): attach callbacks directly to that
   engine's `ConsumerDeliverer` instead of going over HTTP at all.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from shouldertap.engine.contracts import ConsumerCallbacks, ConsumerRegistration, ContextRequest
from shouldertap.engine.delivery import ConsumerDeliverer

DEFAULT_BASE_URL = "http://localhost:8776/api/v1"


class ShoulderTapClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, api_token: str | None = None) -> None:
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=30.0)

    def ask(self, request: ContextRequest) -> dict[str, Any]:
        """Submit a ContextRequest. Returns `{id, status}` -- see spec §5 POST /requests."""
        response = self._client.post("/requests", json=request.model_dump(mode="json"))
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def get_request(self, request_id: str) -> dict[str, Any]:
        response = self._client.get(f"/requests/{request_id}")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def register(self, registration: ConsumerRegistration) -> dict[str, Any]:
        response = self._client.post("/consumers", json=registration.model_dump(mode="json"))
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def unregister(self, consumer_id: str) -> None:
        response = self._client.delete(f"/consumers/{consumer_id}")
        response.raise_for_status()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ShoulderTapClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def register_in_process(
    deliverer: ConsumerDeliverer, consumer_id: str, callbacks: ConsumerCallbacks
) -> None:
    """spec §4.3's SDK in-process callback delivery mode."""
    deliverer.register_in_process(consumer_id, callbacks)
