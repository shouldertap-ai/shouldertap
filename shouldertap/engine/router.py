"""Registry-based expert routing -- spec §6, the exact 6-step algorithm. Deliberately simple:
no embeddings, no learning, no org-chart inference. Operates against the `experts` table (live
state: muted/open_asks, synced from shouldertap.yaml at startup) plus the YAML's `topics:`
fallback config, which has no live-state counterpart.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.orm import Session

from shouldertap.engine.contracts import ContextRequest
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.store.models import ExpertRow
from shouldertap.engine.store.repository import list_experts_for_topic


def normalize(text: str) -> str:
    """Lowercase, collapsed whitespace -- spec §6 step 2's exact normalization rule."""
    return " ".join(text.lower().split())


def _token_overlap(a: str, b: str) -> float:
    """Jaccard similarity between the normalized token sets of two topic strings -- spec §6
    step 4 says "fuzzy-match topics by token overlap >= 0.5" without giving an exact formula;
    Jaccard (|intersection| / |union|) is the standard reading of "token overlap".
    """
    tokens_a, tokens_b = set(normalize(a).split()), set(normalize(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


@dataclass
class RoutingResult:
    expert_id: str | None
    reason: (
        str  # "target_override" | "exact_match" | "fuzzy_match" | "fallback" | "no_expert_found"
    )


def _pick_least_loaded(candidates: list[ExpertRow]) -> ExpertRow:
    """Spec §6 step 3, exact: fewest open_asks, tie-break random."""
    min_open = min(c.open_asks for c in candidates)
    least_loaded = [c for c in candidates if c.open_asks == min_open]
    return random.choice(least_loaded)


def _available(candidates: list[ExpertRow], exclude: frozenset[str]) -> list[ExpertRow]:
    return [c for c in candidates if not c.muted and c.id not in exclude]


def resolve(
    session: Session,
    config: RegistryConfig,
    request: ContextRequest,
    *,
    exclude: frozenset[str] = frozenset(),
) -> RoutingResult:
    """`exclude` lets asker.py retry routing around an expert that turned out to be at their
    rate-limit cap (spec §7.4: "if an expert is at cap, route to next candidate") without
    re-implementing the cascade itself.
    """
    # Step 1: target_experts override entirely bypasses topic-based routing.
    if request.target_experts:
        for target in request.target_experts:
            if target.expert_id in exclude:
                continue
            row = session.get(ExpertRow, target.expert_id)
            if row is not None and not row.muted:
                return RoutingResult(row.id, "target_override")
        return RoutingResult(None, "no_expert_found")

    normalized_topic = normalize(request.topic)

    # Steps 2-3: exact topic match, load-balanced.
    exact_candidates = _available(list_experts_for_topic(session, normalized_topic), exclude)
    if exact_candidates:
        return RoutingResult(_pick_least_loaded(exact_candidates).id, "exact_match")

    # Step 4: fuzzy match by token overlap >= 0.5, same load-balance tie-break.
    all_experts = session.query(ExpertRow).all()
    fuzzy_candidates = [
        e
        for e in _available(all_experts, exclude)
        if any(_token_overlap(normalized_topic, t) >= 0.5 for t in e.topics)
    ]
    if fuzzy_candidates:
        return RoutingResult(_pick_least_loaded(fuzzy_candidates).id, "fuzzy_match")

    # Step 5: the topic's configured fallback expert.
    topic_config = config.topics.get(request.topic) or config.topics.get(normalized_topic)
    if topic_config and topic_config.fallback and topic_config.fallback not in exclude:
        row = session.get(ExpertRow, topic_config.fallback)
        if row is not None and not row.muted:
            return RoutingResult(row.id, "fallback")

    # Step 6.
    return RoutingResult(None, "no_expert_found")
