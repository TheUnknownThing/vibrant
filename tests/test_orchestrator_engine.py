"""Unit tests for the Phase 1 orchestrator state machine."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from vibrant.consensus.writer import ConsensusWriter
from vibrant.gatekeeper import GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorState, OrchestratorStatus
from vibrant.orchestrator.engine import OrchestratorEngine
from vibrant.project_init import initialize_project


def _write_agent_record(path: Path, record: AgentRecord) -> None:
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


class TestOrchestratorEngineTransitions:
    def test_valid_transitions_succeed(self, tmp_path):
        initialize_project(tmp_path)
        engine = OrchestratorEngine.load(tmp_path)

        assert engine.state.status is OrchestratorStatus.INIT

        engine.transition_to(OrchestratorStatus.PLANNING)
        engine.transition_to(OrchestratorStatus.PAUSED)
        engine.transition_to(OrchestratorStatus.PLANNING)
        engine.transition_to(OrchestratorStatus.EXECUTING)
        engine.transition_to(OrchestratorStatus.PAUSED)
        engine.transition_to(OrchestratorStatus.EXECUTING)
        engine.transition_to(OrchestratorStatus.VALIDATING)
        engine.transition_to(OrchestratorStatus.COMPLETED)

        assert engine.state.status is OrchestratorStatus.COMPLETED

    def test_invalid_transitions_raise(self, tmp_path):
        initialize_project(tmp_path)
        engine = OrchestratorEngine.load(tmp_path)

        with pytest.raises(ValueError, match="Invalid orchestrator state transition"):
            engine.transition_to(OrchestratorStatus.EXECUTING)

        engine.transition_to(OrchestratorStatus.PLANNING)
        with pytest.raises(ValueError, match="Invalid orchestrator state transition"):
            engine.transition_to(OrchestratorStatus.VALIDATING)

        engine.transition_to(OrchestratorStatus.EXECUTING)
        engine.transition_to(OrchestratorStatus.COMPLETED)
        with pytest.raises(ValueError, match="Invalid orchestrator state transition"):
            engine.transition_to(OrchestratorStatus.PLANNING)

    def test_paused_state_reachable_from_planning_and_executing(self, tmp_path):
        initialize_project(tmp_path)
        engine = OrchestratorEngine.load(tmp_path)

        engine.transition_to(OrchestratorStatus.PLANNING)
        engine.transition_to(OrchestratorStatus.PAUSED)
        assert engine.state.status is OrchestratorStatus.PAUSED

        engine.transition_to(OrchestratorStatus.EXECUTING)
        engine.transition_to(OrchestratorStatus.PAUSED)
        assert engine.state.status is OrchestratorStatus.PAUSED




def test_apply_gatekeeper_result_syncs_completed_status(tmp_path):
    initialize_project(tmp_path)
    engine = OrchestratorEngine.load(tmp_path)
    engine.transition_to(OrchestratorStatus.PLANNING)
    engine.transition_to(OrchestratorStatus.EXECUTING)

    consensus = ConsensusWriter().write(
        tmp_path / ".vibrant" / "consensus.md",
        ConsensusDocument(
            project="demo",
            created_at=datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 8, 10, 15, tzinfo=timezone.utc),
            version=2,
            status=ConsensusStatus.COMPLETED,
            objectives="Ship Phase 1.",
            getting_started="Read the roadmap.",
        ),
    )

    result = GatekeeperRunResult(
        request=GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description="Task accepted.",
        ),
        prompt="gatekeeper prompt",
        transcript="Verdict: accepted",
        verdict="accepted",
        questions=[],
        consensus_updated=True,
        roadmap_updated=False,
        plan_modified=False,
        consensus_document=consensus,
        roadmap_document=None,
        error=None,
        turn_result=None,
    )

    engine.apply_gatekeeper_result(result)

    assert engine.state.status is OrchestratorStatus.COMPLETED
    assert engine.state.last_consensus_version == 2


class TestOrchestratorEnginePersistence:
    def test_state_persisted_after_each_transition(self, tmp_path):
        initialize_project(tmp_path)
        engine = OrchestratorEngine.load(tmp_path)
        state_path = tmp_path / ".vibrant" / "state.json"

        engine.transition_to(OrchestratorStatus.PLANNING)
        reloaded = OrchestratorState.model_validate_json(state_path.read_text(encoding="utf-8"))
        assert reloaded.status is OrchestratorStatus.PLANNING

        engine.transition_to(OrchestratorStatus.EXECUTING)
        reloaded = OrchestratorState.model_validate_json(state_path.read_text(encoding="utf-8"))
        assert reloaded.status is OrchestratorStatus.EXECUTING

        recovered = OrchestratorEngine.load(tmp_path)
        assert recovered.state.status is OrchestratorStatus.EXECUTING

    def test_restart_recovers_from_state_agents_and_consensus(self, tmp_path):
        vibrant_dir = initialize_project(tmp_path)
        state_path = vibrant_dir / "state.json"
        agents_dir = vibrant_dir / "agents"
        consensus_path = vibrant_dir / "consensus.md"

        state = OrchestratorState(
            session_id="session-crash",
            started_at=datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc),
            status=OrchestratorStatus.EXECUTING,
            active_agents=["stale-agent-id"],
            last_consensus_version=1,
        )
        state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

        running_agent = AgentRecord(
            agent_id="agent-task-001",
            task_id="task-001",
            type=AgentType.CODE,
            status=AgentStatus.RUNNING,
            provider=AgentProviderMetadata(
                provider_thread_id="thread-001",
                resume_cursor={"threadId": "thread-001"},
            ),
        )
        completed_agent = AgentRecord(
            agent_id="agent-task-002",
            task_id="task-002",
            type=AgentType.TEST,
            status=AgentStatus.COMPLETED,
        )
        failed_agent = AgentRecord(
            agent_id="agent-task-003",
            task_id="task-003",
            type=AgentType.CODE,
            status=AgentStatus.FAILED,
            error="boom",
        )
        _write_agent_record(agents_dir / "agent-task-001.json", running_agent)
        _write_agent_record(agents_dir / "agent-task-002.json", completed_agent)
        _write_agent_record(agents_dir / "agent-task-003.json", failed_agent)

        ConsensusWriter().write(
            consensus_path,
            ConsensusDocument(
                project="demo",
                created_at=datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 3, 8, 10, 15, tzinfo=timezone.utc),
                version=14,
                status=ConsensusStatus.EXECUTING,
                objectives="Ship Phase 1.",
                getting_started="Read the roadmap.",
            ),
        )

        recovered = OrchestratorEngine.load(tmp_path)

        assert recovered.state.session_id == "session-crash"
        assert recovered.state.status is OrchestratorStatus.EXECUTING
        assert recovered.state.active_agents == ["agent-task-001"]
        assert recovered.state.completed_tasks == ["task-002"]
        assert recovered.state.failed_tasks == ["task-003"]
        assert recovered.state.last_consensus_version == 14
        assert recovered.state.provider_runtime["agent-task-001"].provider_thread_id == "thread-001"
