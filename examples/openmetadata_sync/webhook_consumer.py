"""Example: sync accepted glossary.definition proposals into OpenMetadata via a webhook
consumer -- the pattern spec §11's OpenMetadataAdapter is designed around.

Run:
    OPENMETADATA_HOST=https://om.example.com OPENMETADATA_TOKEN=... \\
        uv run python examples/openmetadata_sync/webhook_consumer.py

This starts a tiny FastAPI app on :9100 that ShoulderTap will POST accepted proposals to.
Register it once your `shtap serve` is running:

    curl -X POST http://localhost:8776/api/v1/consumers \\
      -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN" -H "Content-Type: application/json" \\
      -d '{"id": "openmetadata-sync", "handles_kinds": ["glossary.definition"],
           "delivery": {"type": "webhook", "url": "http://localhost:9100/webhook"}}'

Then submit a tap whose `context` includes an `entity` field (see spec §4.1's own example),
and tell this consumer which entity that request maps to via /remember -- the consumer, not
ShoulderTap, is what's expected to track that mapping (see adapters/openmetadata.py's
docstring for why: ContextProposal doesn't carry the original request's `context` dict):

    REQUEST_ID=$(curl -s -X POST http://localhost:8776/api/v1/requests \\
      -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN" -H "Content-Type: application/json" \\
      -d '{"kind": "glossary.definition", "topic": "revenue metrics",
           "question": "What counts as an active customer?", "consumer": "openmetadata-sync",
           "context": {"entity": "dim_customers.active_flag"}}' | jq -r .id)

    curl -X POST "http://localhost:9100/remember?request_id=$REQUEST_ID&entity_fqn=dim_customers.active_flag"

Once a human answers and a reviewer accepts the proposal, ShoulderTap POSTs it here and this
script PATCHes the definition (plus a provenance footer) into OpenMetadata.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI, Request

from shouldertap.adapters.openmetadata import OpenMetadataAdapter
from shouldertap.engine.contracts import ContextProposal

_entity_fqn_by_request_id: dict[str, str] = {}

adapter = OpenMetadataAdapter(
    host=os.environ["OPENMETADATA_HOST"],
    token=os.environ["OPENMETADATA_TOKEN"],
    entity_fqn_resolver=lambda proposal: _entity_fqn_by_request_id.get(proposal.request_id),
)

app = FastAPI(title="openmetadata-sync example consumer")


@app.post("/remember")
async def remember(request_id: str, entity_fqn: str) -> dict[str, bool]:
    _entity_fqn_by_request_id[request_id] = entity_fqn
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, object]:
    payload = await request.json()
    if payload.get("event") != "proposal_accepted":
        return {"ignored": True}
    proposal = ContextProposal.model_validate(payload["proposal"])
    result = adapter.on_accepted(proposal)
    return {"success": result.success, "detail": result.detail}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9100)
