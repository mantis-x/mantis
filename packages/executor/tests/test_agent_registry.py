"""
Integration tests for the Postgres-backed AgentRegistry.

Requires a disposable Postgres reachable at TEST_DATABASE_URL (defaults to
localhost:5433 — deliberately not 5432, the docker-compose default, since
this file's fixtures create/drop tables). Fixtures are scoped to this file
only, so the rest of the executor suite (guards, rule engine, arbitrum
executor, queue fan-out) keeps running with zero external dependencies.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://mantis:password@localhost:5433/mantis"
)
if ":5432/" in _TEST_DATABASE_URL and "ALLOW_TEST_DB_ON_5432" not in os.environ:
    raise RuntimeError(
        "Refusing to run: TEST_DATABASE_URL points at port 5432. This file's "
        "fixtures create/drop tables — point it at a disposable instance instead."
    )
os.environ.setdefault("DATABASE_URL", _TEST_DATABASE_URL)

from src.db.connection import get_engine, get_session, reset_engine_for_tests
from src.db.models import Base
from src.db.models.agent import AgentRow
from src.identity.agent_registry import AgentRegistry, Agent


@pytest.fixture(scope="module", autouse=True)
def _db_schema():
    reset_engine_for_tests()
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_agents():
    with get_session() as session:
        session.query(AgentRow).delete()
    yield


class TestSeedDemoAgent:
    def test_seeds_demo_agent_on_first_construction(self):
        registry = AgentRegistry()
        assert registry.count() == 1
        agents = registry.get_active_agents()
        assert agents[0].name == "Mantis Execute — Demo Agent"

    def test_does_not_reseed_on_second_construction(self):
        """A process restart (new AgentRegistry()) must not create a duplicate demo agent."""
        AgentRegistry()
        AgentRegistry()  # simulates a redeploy re-instantiating the registry
        registry = AgentRegistry()
        assert registry.count() == 1

    def test_demo_agent_rules_match_original_defaults(self):
        registry = AgentRegistry()
        agent = registry.get_active_agents()[0]
        assert agent.rules["min_confidence"] == 75
        assert agent.rules["protocols"] == ["agni_finance", "merchant_moe"]
        assert agent.rules["amount_usd"] == 100.0


class TestRegisterAndGet:
    def test_register_creates_new_agent(self):
        registry = AgentRegistry()
        agent = registry.register(
            owner_wallet="0xnewowner", name="Test Agent", rules={"min_confidence": 80}
        )
        assert isinstance(agent, Agent)
        assert agent.agent_id is not None
        assert agent.owner_wallet == "0xnewowner"

    def test_get_returns_registered_agent(self):
        registry = AgentRegistry()
        created = registry.register(owner_wallet="0xabc", name="Findable", rules={})
        fetched = registry.get(created.agent_id)
        assert fetched is not None
        assert fetched.name == "Findable"

    def test_get_returns_none_for_unknown_id(self):
        registry = AgentRegistry()
        assert registry.get(999999) is None

    def test_count_reflects_registered_agents(self):
        registry = AgentRegistry()
        assert registry.count() == 1  # just the seeded demo agent
        registry.register(owner_wallet="0x1", name="A", rules={})
        registry.register(owner_wallet="0x2", name="B", rules={})
        assert registry.count() == 3


class TestGetActiveAgents:
    def test_excludes_inactive_agents(self):
        registry = AgentRegistry()
        registry.register(owner_wallet="0x1", name="Active", rules={})
        with get_session() as session:
            row = session.query(AgentRow).filter(AgentRow.name == "Active").one()
            row.active = False

        active = registry.get_active_agents()
        names = {a.name for a in active}
        assert "Active" not in names
        assert "Mantis Execute — Demo Agent" in names


class TestRecordExecution:
    def test_increments_total_decisions_and_executions_on_success(self):
        registry = AgentRegistry()
        agent = registry.get_active_agents()[0]
        registry.record_execution(agent.agent_id, success=True)

        updated = registry.get(agent.agent_id)
        assert updated.total_decisions == 1
        assert updated.total_executions == 1
        assert updated.total_aborts == 0

    def test_increments_total_decisions_and_aborts_on_failure(self):
        registry = AgentRegistry()
        agent = registry.get_active_agents()[0]
        registry.record_execution(agent.agent_id, success=False)

        updated = registry.get(agent.agent_id)
        assert updated.total_decisions == 1
        assert updated.total_executions == 0
        assert updated.total_aborts == 1

    def test_persists_across_new_registry_instances(self):
        """The whole point: stats must survive a process restart (new AgentRegistry())."""
        registry = AgentRegistry()
        agent = registry.get_active_agents()[0]
        registry.record_execution(agent.agent_id, success=True)

        fresh_registry = AgentRegistry()  # simulates a redeploy
        updated = fresh_registry.get(agent.agent_id)
        assert updated.total_executions == 1

    def test_success_rate_computed_correctly(self):
        registry = AgentRegistry()
        agent = registry.get_active_agents()[0]
        registry.record_execution(agent.agent_id, success=True)
        registry.record_execution(agent.agent_id, success=True)
        registry.record_execution(agent.agent_id, success=False)

        updated = registry.get(agent.agent_id)
        assert abs(updated.success_rate - (2 / 3 * 100)) < 0.01

    def test_unknown_agent_id_does_not_raise(self):
        registry = AgentRegistry()
        registry.record_execution(999999, success=True)  # should log a warning, not raise
