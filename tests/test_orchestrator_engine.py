"""Unit tests for the Phase 1 orchestrator state machine."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vibrant.agents import GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.agents.runtime import RunState
from vibrant.consensus.writer import ConsensusWriter
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorState, OrchestratorStatus
from vibrant.orchestrator import OrchestratorStateBackend
from vibrant.orchestrator.state import StateStore
from vibrant.project_init import initialize_project


def _write_agent_record(path: Path, record: AgentRecord) -> None:
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


class TestOrchestratorEngineTransitions:
    def test_valid_transitions_succeed(self, tmp_path):
        initialize_project(tmp_path)
        engine = OrchestratorStateBackend.load(tmp_path)

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
        engine = OrchestratorStateBackend.load(tmp_path)

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
        engine = OrchestratorStateBackend.load(tmp_path)

        engine.transition_to(OrchestratorStatus.PLANNING)
        engine.transition_to(OrchestratorStatus.PAUSED)
        assert engine.state.status is OrchestratorStatus.PAUSED

        engine.transition_to(OrchestratorStatus.EXECUTING)
        engine.transition_to(OrchestratorStatus.PAUSED)
        assert engine.state.status is OrchestratorStatus.PAUSED




def test_state_store_apply_gatekeeper_result_syncs_completed_status(tmp_path):
    initialize_project(tmp_path)
    engine = OrchestratorStateBackend.load(tmp_path)
    state_store = StateStore(engine)
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

    gatekeeper_record = AgentRecord(
        identity={
            "agent_id": "gatekeeper-task_completion-test",
            "task_id": "gatekeeper-task_completion",
            "type": AgentType.GATEKEEPER,
        },
        lifecycle={"status": AgentStatus.COMPLETED},
        provider=AgentProviderMetadata(
            provider_thread_id="thread-gatekeeper-completed",
            resume_cursor={"threadId": "thread-gatekeeper-completed"},
        ),
    )
    result = GatekeeperRunResult(
        agent_record=gatekeeper_record,
        state=RunState.COMPLETED,
        transcript="Task review complete.",
        summary="Task review complete.",
        started_at=gatekeeper_record.lifecycle.started_at,
        finished_at=gatekeeper_record.lifecycle.finished_at,
    )

    state_store.apply_gatekeeper_result(result)

    assert engine.state.status is OrchestratorStatus.COMPLETED
    assert engine.state.last_consensus_version == 2


class TestOrchestratorEnginePersistence:
    def test_state_persisted_after_each_transition(self, tmp_path):
        initialize_project(tmp_path)
        engine = OrchestratorStateBackend.load(tmp_path)
        state_path = tmp_path / ".vibrant" / "state.json"

        engine.transition_to(OrchestratorStatus.PLANNING)
        reloaded = OrchestratorState.model_validate_json(state_path.read_text(encoding="utf-8"))
        assert reloaded.status is OrchestratorStatus.PLANNING

        engine.transition_to(OrchestratorStatus.EXECUTING)
        reloaded = OrchestratorState.model_validate_json(state_path.read_text(encoding="utf-8"))
        assert reloaded.status is OrchestratorStatus.EXECUTING

        recovered = OrchestratorStateBackend.load(tmp_path)
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
            identity={"agent_id": "agent-task-001", "task_id": "task-001", "type": AgentType.CODE},
            lifecycle={"status": AgentStatus.RUNNING},
            provider=AgentProviderMetadata(
                provider_thread_id="thread-001",
                resume_cursor={"threadId": "thread-001"},
            ),
        )
        completed_agent = AgentRecord(
            identity={"agent_id": "agent-task-002", "task_id": "task-002", "type": AgentType.MERGE},
            lifecycle={"status": AgentStatus.COMPLETED},
        )
        failed_agent = AgentRecord(
            identity={"agent_id": "agent-task-003", "task_id": "task-003", "type": AgentType.CODE},
            lifecycle={"status": AgentStatus.FAILED},
            outcome={"error": "boom"},
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

        recovered = OrchestratorStateBackend.load(tmp_path)

        assert recovered.state.session_id == "session-crash"
        assert recovered.state.status is OrchestratorStatus.EXECUTING
        assert recovered.state.active_agents == ["agent-task-001"]
        assert recovered.state.completed_tasks == ["task-002"]
        assert recovered.state.failed_tasks == ["task-003"]
        assert recovered.state.last_consensus_version == 14
        assert recovered.state.provider_runtime["agent-task-001"].provider_thread_id == "thread-001"

    def test_restart_migrates_legacy_runtime_state_and_reads_nested_agents(self, tmp_path):
        vibrant_dir = initialize_project(tmp_path)
        state_path = vibrant_dir / "state.json"
        agent_path = vibrant_dir / "agents" / "agent-gatekeeper-user_discussion-001.json"

        state_path.write_text(
            json.dumps(
                {
                    "session_id": "session-legacy",
                    "started_at": "2026-03-09T00:57:40Z",
                    "status": "executing",
                    "active_agents": ["agent-gatekeeper-user_discussion-001"],
                    "gatekeeper_status": "running",
                    "pending_requests": [],
                    "last_consensus_version": 4,
                    "concurrency_limit": 1,
                    "provider_threads": [
                        {
                            "owner_agent_id": "agent-gatekeeper-user_discussion-001",
                            "provider_name": "codex",
                            "transport_name": "app-server-json-rpc",
                            "runtime_state": "running",
                            "runtime_mode": "workspace_write",
                            "provider_thread_id": "thread-legacy",
                            "resume_token": {
                                "threadId": "thread-legacy",
                                "threadPath": "/tmp/thread-legacy.jsonl",
                            },
                        }
                    ],
                    "completed_tasks": [],
                    "failed_tasks": [],
                    "total_agent_spawns": 1,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        agent_path.write_text(
            json.dumps(
                {
                    "identity": {
                        "agent_id": "agent-gatekeeper-user_discussion-001",
                        "task_id": "gatekeeper-user_discussion",
                        "type": "gatekeeper",
                    },
                    "lifecycle": {
                        "status": "running",
                        "started_at": "2026-03-09T10:37:04Z",
                    },
                    "context": {
                        "worktree_path": str(vibrant_dir),
                        "prompt_used": "hello",
                        "skills_loaded": [],
                    },
                    "retry": {"retry_count": 0, "max_retries": 1},
                    "provider": {
                        "kind": "codex",
                        "transport": "app-server-json-rpc",
                        "runtime_mode": "workspace_write",
                        "provider_thread_id": "thread-legacy",
                        "resume_token": {
                            "threadId": "thread-legacy",
                            "threadPath": "/tmp/thread-legacy.jsonl",
                        },
                        "native_event_log_path": ".vibrant/logs/providers/native/agent-gatekeeper.ndjson",
                        "canonical_event_log_path": ".vibrant/logs/providers/canonical/agent-gatekeeper.ndjson",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        recovered = OrchestratorStateBackend.load(tmp_path)

        assert recovered.state.session_id == "session-legacy"
        assert recovered.state.active_agents == ["agent-gatekeeper-user_discussion-001"]
        assert recovered.state.provider_runtime["agent-gatekeeper-user_discussion-001"].provider_thread_id == (
            "thread-legacy"
        )
        recovered_records = {record.identity.agent_id: record for record in recovered.list_agent_records()}
        assert (
            recovered_records["agent-gatekeeper-user_discussion-001"].identity.task_id
            == "gatekeeper-user_discussion"
        )
        assert recovered_records["agent-gatekeeper-user_discussion-001"].provider.thread_path == "/tmp/thread-legacy.jsonl"

        persisted_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "provider_threads" not in persisted_state
        assert "pending_requests" not in persisted_state
