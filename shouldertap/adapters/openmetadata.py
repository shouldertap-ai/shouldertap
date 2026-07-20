"""Spec §11 reference implementation: on a `glossary.definition` accept, PATCH the glossary
term's description in OpenMetadata with the structured definition plus a provenance footer
("Source: Dana Kim via ShoulderTap, 2026-07-20").

`ContextProposal` doesn't carry the original request's `context` dict (only `ContextRequest`
does), so the "entity-FQN mapping from context.entity" spec calls out as config is expressed
here as an injected resolver callable: the integrating consumer is the one that knows how to
map a proposal back to the entity it was originally asked about (e.g. via its own
request_id -> context tracking), so it supplies that mapping at adapter construction time
rather than this adapter trying to derive it from the proposal alone.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from shouldertap.adapters.base import WriteResult
from shouldertap.engine.contracts import KIND_GLOSSARY_DEFINITION, ContextProposal

EntityResolver = Callable[[ContextProposal], str | None]


def provenance_footer(proposal: ContextProposal) -> str:
    date_str = proposal.provenance.answered_at.date().isoformat()
    return f"Source: {proposal.provenance.expert_name} via ShoulderTap, {date_str}"


class OpenMetadataAdapter:
    def __init__(
        self,
        *,
        host: str,
        token: str,
        entity_fqn_resolver: EntityResolver,
        timeout: float = 10.0,
    ) -> None:
        self._host = host.rstrip("/")
        self._token = token
        self._resolve_entity_fqn = entity_fqn_resolver
        self._timeout = timeout

    def on_accepted(self, proposal: ContextProposal) -> WriteResult:
        if proposal.kind != KIND_GLOSSARY_DEFINITION:
            return WriteResult(success=False, detail=f"unsupported kind: {proposal.kind}")

        entity_fqn = self._resolve_entity_fqn(proposal)
        if entity_fqn is None:
            return WriteResult(success=False, detail="no entity FQN resolved for this proposal")

        definition = (proposal.structured or {}).get("definition") or proposal.answer
        description = f"{definition}\n\n---\n{provenance_footer(proposal)}"

        try:
            response = httpx.patch(
                f"{self._host}/api/v1/glossaryTerms/name/{entity_fqn}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json-patch+json",
                },
                json=[{"op": "add", "path": "/description", "value": description}],
                timeout=self._timeout,
            )
            response.raise_for_status()
            return WriteResult(success=True, detail=entity_fqn)
        except Exception as e:
            return WriteResult(success=False, detail=str(e))
