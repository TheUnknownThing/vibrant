"""Unit tests for Phase 0 Task 0.3 data models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from vibrant.models.agent import AgentProviderMetadata, AgentRunRecord, AgentStatus, ProviderResumeHandle
from vibrant.models.consensus import (
    ConsensusDocument,
    ConsensusStatus,
)
from vibrant.models.state import (
    GatekeeperStatus,
    OrchestratorState,
    OrchestratorStatus,
    ProviderRuntimeState,
)
from vibrant.models.task import TaskInfo, TaskStatus


class TestAgentRecord:
    def test_round_trip_serialize_deserialize(self):
        record = AgentRunRecord(
            identity={
                "agent_id": "agent-task-001",
                "task_id": "task-001",
                "role": "code",
            },
            lifecycle={
                "status": AgentStatus.RUNNING,
                "pid": 12345,
                "started_at": datetime(2026, 3, 7, 22, 0, tzinfo=timezone.utc),
            },
            context={
                "branch": "vibrant/task-001",
                "worktree_path": "/tmp/vibrant-worktrees/task-001",
                "prompt_used": "prompt",
                "skills_loaded": ["gui-design"],
            },
            provider=AgentProviderMetadata(
                runtime_mode="full-access",
                provider_thread_id="thread_abc123",
                resume_cursor={"threadId": "thread_abc123"},
                native_event_log=".vibrant/logs/providers/native/agent-task-001.ndjson",
                canonical_event_log=".vibrant/logs/providers/canonical/agent-task-001.ndjson",
            ),
            outcome={"summary": "summary"},
        )

        restored = AgentRunRecord.model_validate_json(record.model_dump_json())
        dumped = record.model_dump(mode="json")

        assert restored == record
        assert restored.identity.role == "code"
        assert dumped["identity"]["role"] == "code"
        assert dumped["provider"]["resume_handle"] == ProviderResumeHandle(
            kind="codex",
            thread_id="thread_abc123",
            resume_cursor={"threadId": "thread_abc123"},
        ).model_dump(mode="json")
        assert "provider_thread_id" not in dumped["provider"]
        assert restored.provider.resume_cursor == {"threadId": "thread_abc123"}

    def test_status_transitions_are_validated(self):
        record = AgentRunRecord(
            identity={"agent_id": "agent-1", "task_id": "task-1", "role": "code"}
        )

        record.transition_to(AgentStatus.CONNECTING)
        record.transition_to(AgentStatus.RUNNING)
        record.transition_to(AgentStatus.AWAITING_INPUT)
        record.transition_to(AgentStatus.RUNNING)
        record.transition_to(AgentStatus.COMPLETED, exit_code=0)

        assert record.lifecycle.status is AgentStatus.COMPLETED
        assert record.outcome.exit_code == 0
        assert record.lifecycle.finished_at is not None

        with pytest.raises(ValueError, match="Invalid agent status transition"):
            record.transition_to(AgentStatus.RUNNING)

    def test_nested_agent_record_deserializes(self):
        record = AgentRunRecord.model_validate(
            {
                "identity": {
                    "agent_id": "agent-gatekeeper-user_discussion-001",
                    "task_id": "gatekeeper-user_discussion",
                    "role": "gatekeeper",
                },
                "lifecycle": {"status": "running"},
                "context": {"worktree_path": "/tmp/project"},
                "provider": {
                    "kind": "codex",
                    "transport": "app-server-json-rpc",
                    "runtime_mode": "workspace_write",
                    "provider_thread_id": "thread-123",
                    "resume_token": {
                        "threadId": "thread-123",
                        "threadPath": "/tmp/thread-123.jsonl",
                    },
                    "native_event_log_path": ".vibrant/logs/providers/native/agent.ndjson",
                    "canonical_event_log_path": ".vibrant/logs/providers/canonical/agent.ndjson",
                },
            }
        )

        assert record.identity.role == "gatekeeper"
        assert record.identity.task_id == "gatekeeper-user_discussion"
        assert record.provider.kind == "codex"
        assert record.provider.transport == "app-server-json-rpc"
        assert record.provider.runtime_mode == "workspace-write"
        assert record.provider.provider_thread_id == "thread-123"
        assert record.provider.resume_cursor == {
            "threadId": "thread-123",
            "threadPath": "/tmp/thread-123.jsonl",
        }
        assert record.provider.thread_path == "/tmp/thread-123.jsonl"

    def test_invalid_provider_runtime_mode_raises(self):
        with pytest.raises(ValidationError, match="Unsupported provider runtime mode"):
            AgentProviderMetadata.model_validate({"runtime_mode": "mystery-mode"})

    def test_claude_session_resume_cursor_restores_provider_thread_id(self):
        provider = AgentProviderMetadata.model_validate(
            {
                "kind": "claude",
                "transport": "sdk-stream-json",
                "runtime_mode": "workspace-write",
                "resume_cursor": {"sessionId": "session-123"},
            }
        )

        assert provider.provider_thread_id == "session-123"
        assert provider.resume_handle == ProviderResumeHandle(
            kind="claude",
            thread_id="session-123",
            resume_cursor={"sessionId": "session-123"},
        )

    def test_nested_agent_record_requires_identity(self):
        with pytest.raises(ValidationError, match="identity"):
            AgentRunRecord.model_validate(
                {
                    "lifecycle": {"status": "running"},
                }
            )

    def test_role_only_identity_round_trips(self):
        record = AgentRunRecord.model_validate(
            {
                "identity": {
                    "agent_id": "merge-task-001",
                    "task_id": "task-001",
                    "role": "merge",
                }
            }
        )

        assert record.identity.role == "merge"


class TestOrchestratorState:
    def test_round_trip_serialize_deserialize(self):
        state = OrchestratorState(
            session_id="session-123",
            started_at=datetime(2026, 3, 7, 22, 0, tzinfo=timezone.utc),
            status=OrchestratorStatus.EXECUTING,
            active_agents=["agent-task-001", "agent-task-003"],
            gatekeeper_status=GatekeeperStatus.AWAITING_USER,
            pending_questions=["Q1", "Q3"],
            last_consensus_version=14,
            concurrency_limit=4,
            provider_runtime={
                "agent-task-001": ProviderRuntimeState(
                    status="ready",
                    provider_thread_id="thread_abc123",
                )
            },
            completed_tasks=["task-001", "task-002"],
            failed_tasks=[],
            total_agent_spawns=7,
        )

        restored = OrchestratorState.model_validate_json(state.model_dump_json())

        assert restored == state

    def test_legacy_running_status_is_normalized(self):
        state = OrchestratorState.model_validate(
            {
                "session_id": "session-123",
                "status": "running",
            }
        )

        assert state.status is OrchestratorStatus.EXECUTING

    def test_legacy_provider_threads_are_migrated(self):
        state = OrchestratorState.model_validate(
            {
                "session_id": "session-123",
                "status": "executing",
                "pending_requests": [],
                "provider_threads": [
                    {
                        "owner_agent_id": "agent-task-001",
                        "runtime_state": "running",
                        "provider_thread_id": "thread-abc123",
                    }
                ],
            }
        )

        assert state.provider_runtime == {
            "agent-task-001": ProviderRuntimeState(
                status="running",
                provider_thread_id="thread-abc123",
            )
        }


class TestTaskInfo:
    def test_round_trip_serialize_deserialize(self):
        task = TaskInfo(
            id="task-001",
            title="Implement config loader",
            acceptance_criteria=["Loads vibrant.toml", "Applies defaults"],
            branch="vibrant/task-001",
            prompt="Build the loader",
            skills=["testing-strategy"],
            dependencies=["task-000"],
            priority=1,
        )

        restored = TaskInfo.model_validate_json(task.model_dump_json())

        assert restored == task

    def test_lifecycle_state_machine(self):
        task = TaskInfo(id="task-001", title="Implement models")

        task.transition_to(TaskStatus.QUEUED)
        task.transition_to(TaskStatus.IN_PROGRESS)
        task.transition_to(TaskStatus.COMPLETED)

        assert task.status is TaskStatus.COMPLETED

    def test_failure_retry_and_escalation_paths(self):
        task = TaskInfo(id="task-001", title="Implement models", max_retries=1)

        task.transition_to(TaskStatus.QUEUED)
        task.transition_to(TaskStatus.IN_PROGRESS)
        task.transition_to(TaskStatus.FAILED, failure_reason="timeout")
        assert task.failure_reason == "timeout"

        task.transition_to(TaskStatus.QUEUED)
        assert task.retry_count == 1
        assert task.failure_reason is None

        task.transition_to(TaskStatus.IN_PROGRESS)
        task.transition_to(TaskStatus.FAILED, failure_reason="still broken")
        task.transition_to(TaskStatus.ESCALATED)

        assert task.status is TaskStatus.ESCALATED


class TestConsensusDocument:
    def test_round_trip_serialize_deserialize(self):
        document = ConsensusDocument(
            project="Project Vibrant",
            created_at=datetime(2026, 3, 7, 22, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 7, 23, 15, tzinfo=timezone.utc),
            version=14,
            status=ConsensusStatus.EXECUTING,
            context="## Objectives\nBuild the orchestration control plane.\n\n## Getting Started\nRead docs/spec.md, then inspect .vibrant/consensus.md.",
        )

        restored = ConsensusDocument.model_validate_json(document.model_dump_json())

        assert restored == document
