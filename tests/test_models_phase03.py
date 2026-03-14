"""Focused regression tests for core model behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.state import OrchestratorState
from vibrant.models.task import TaskInfo, TaskStatus


def test_agent_record_status_transition_guardrails():
    record = AgentRecord(
        identity={
            "run_id": "run-1",
            "agent_id": "agent-1",
            "role": AgentType.CODE.value,
            "type": AgentType.CODE,
        }
    )

    record.transition_to(AgentStatus.CONNECTING)
    record.transition_to(AgentStatus.RUNNING)
    record.transition_to(AgentStatus.COMPLETED, exit_code=0)

    assert record.lifecycle.status is AgentStatus.COMPLETED
    with pytest.raises(ValueError, match="Invalid agent status transition"):
        record.transition_to(AgentStatus.RUNNING)


def test_agent_record_requires_explicit_role_in_run_identity():
    with pytest.raises(ValidationError, match="role"):
        AgentRecord(
            identity={
                "run_id": "run-legacy",
                "agent_id": "agent-legacy",
                "type": AgentType.CODE,
            }
        )


def test_provider_runtime_mode_validation_still_rejects_unknown_values():
    with pytest.raises(ValidationError, match="Unsupported provider runtime mode"):
        AgentProviderMetadata.model_validate({"runtime_mode": "mystery-mode"})


def test_orchestrator_state_rejects_legacy_running_status_alias():
    with pytest.raises(ValidationError, match="status"):
        OrchestratorState.model_validate({"session_id": "session-123", "status": "running"})


def test_task_failure_retry_counter_increments_on_requeue():
    task = TaskInfo(id="task-001", title="Implement models", max_retries=1)

    task.transition_to(TaskStatus.QUEUED)
    task.transition_to(TaskStatus.IN_PROGRESS)
    task.transition_to(TaskStatus.FAILED, failure_reason="timeout")
    task.transition_to(TaskStatus.QUEUED)

    assert task.retry_count == 1
    assert task.failure_reason is None
