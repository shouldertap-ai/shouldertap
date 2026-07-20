from pathlib import Path

import pytest

from shouldertap.engine import router
from shouldertap.engine.contracts import ContextRequest, TargetExpert
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import (
    adjust_open_asks,
    set_muted,
    upsert_expert,
)


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    engine = make_engine(db_path)
    return make_session_factory(engine)


def _request(topic: str, **kwargs) -> ContextRequest:
    return ContextRequest(
        kind="glossary.definition",
        topic=topic,
        question="what does this mean?",
        consumer="test-consumer",
        **kwargs,
    )


def _config(**topics) -> RegistryConfig:
    return RegistryConfig.model_validate({"org": {"name": "Test"}, "topics": topics})


def test_exact_match_single_candidate(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        session.commit()

        result = router.resolve(session, _config(), _request("Revenue Metrics"))
        assert result.expert_id == "U1"
        assert result.reason == "exact_match"


def test_exact_match_normalizes_stored_expert_topics_too(session_factory) -> None:
    """Regression: exact-match must normalize both sides. An admin who types extra
    whitespace/mixed case into shouldertap.yaml (e.g. "Revenue  Metrics") should still get an
    exact match, not silently fall through to fuzzy matching (which mislabels the routing
    reason and can miss entirely if token overlap happens to drop below 0.5).
    """
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["Revenue  Metrics"], escalation_to=None
        )
        session.commit()

        result = router.resolve(session, _config(), _request("revenue metrics"))
        assert result.expert_id == "U1"
        assert result.reason == "exact_match"


def test_exact_match_load_balances_to_fewest_open_asks(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        upsert_expert(
            session, expert_id="U2", name="Marco", topics=["revenue metrics"], escalation_to=None
        )
        adjust_open_asks(session, "U1", 2)
        session.commit()

        result = router.resolve(session, _config(), _request("revenue metrics"))
        assert result.expert_id == "U2"
        assert result.reason == "exact_match"


def test_muted_expert_is_never_routed_to(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        set_muted(session, "U1", True)
        session.commit()

        result = router.resolve(session, _config(), _request("revenue metrics"))
        assert result.expert_id is None
        assert result.reason == "no_expert_found"


def test_fuzzy_match_on_token_overlap(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(
            session,
            expert_id="U1",
            name="Dana",
            topics=["revenue reporting metrics"],
            escalation_to=None,
        )
        session.commit()

        result = router.resolve(session, _config(), _request("revenue metrics"))
        assert result.expert_id == "U1"
        assert result.reason == "fuzzy_match"


def test_falls_back_to_topic_fallback_expert(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(session, expert_id="U2", name="Marco", topics=[], escalation_to=None)
        session.commit()

        config = _config(**{"unrelated topic": {"fallback": "U2"}})
        result = router.resolve(session, config, _request("unrelated topic"))
        assert result.expert_id == "U2"
        assert result.reason == "fallback"


def test_no_expert_found_when_nothing_matches(session_factory) -> None:
    with session_factory() as session:
        result = router.resolve(session, _config(), _request("totally unmatched topic"))
        assert result.expert_id is None
        assert result.reason == "no_expert_found"


def test_target_experts_bypasses_topic_routing(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U9", name="Priya", topics=["unrelated"], escalation_to=None
        )
        session.commit()

        request = _request("revenue metrics", target_experts=[TargetExpert(expert_id="U9")])
        result = router.resolve(session, _config(), request)
        assert result.expert_id == "U9"
        assert result.reason == "target_override"


def test_exclude_set_skips_capped_expert_to_next_candidate(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        upsert_expert(
            session, expert_id="U2", name="Marco", topics=["revenue metrics"], escalation_to=None
        )
        session.commit()

        result = router.resolve(
            session, _config(), _request("revenue metrics"), exclude=frozenset({"U1"})
        )
        assert result.expert_id == "U2"
