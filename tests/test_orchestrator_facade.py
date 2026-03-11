from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.models.state import OrchestratorState
from vibrant.orchestrator import OrchestratorAgentSnapshot, OrchestratorFacade


def _record(
    agent_id: str,
    *,
    task_id: str = "task-1",
    agent_type: AgentType = AgentType.CODE,
    status: AgentStatus = AgentStatus.RUNNING,
    summary: str | None = None,
) -> AgentRecord:
    now = datetime.now(timezone.utc)
    return AgentRecord(
        agent_id=agent_id,
        task_id=task_id,
        type=agent_type,
        status=status,
        started_at=now,
        summary=summary,
    )


class _FakeAgentManager:
    def __init__(self, snapshots: list[object]) -> None:
        self._snapshots = {snapshot.agent_id: snapshot for snapshot in snapshots}

    def get_agent(self, agent_id: str):
        return self._snapshots.get(agent_id)

    def list_agents(self, **kwargs):
        task_id = kwargs.get("task_id")
        include_completed = kwargs.get("include_completed", True)
        active_only = kwargs.get("active_only", False)
        agent_type = kwargs.get("agent_type")
        agent_type_value = getattr(agent_type, "value", agent_type)

        snapshots = list(self._snapshots.values())
        if task_id is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.task_id == task_id]
        if agent_type_value is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.agent_type == agent_type_value]
        if active_only:
            snapshots = [snapshot for snapshot in snapshots if snapshot.active]
        elif not include_completed:
            snapshots = [snapshot for snapshot in snapshots if not snapshot.done or snapshot.awaiting_input]
        return snapshots

    def list_records(self):
        return []


def test_facade_exposes_stable_agent_snapshot_from_agent_manager() -> None:
    managed = SimpleNamespace(
        agent_id="agent-1",
        task_id="task-1",
        agent_type="code",
        status="running",
        state="awaiting_input",
        has_handle=True,
        active=True,
        done=False,
        awaiting_input=True,
        pid=123,
        branch="vibrant/task-1",
        worktree_path="/tmp/worktree",
        started_at=datetime.now(timezone.utc),
        finished_at=None,
        summary="Waiting on user input",
        error=None,
        provider_thread_id="thread-1",
        provider_thread_path="/tmp/thread.json",
        provider_resume_cursor={"cursor": 1},
        input_requests=[],
        native_event_log="native.ndjson",
        canonical_event_log="canonical.ndjson",
    )
    lifecycle = SimpleNamespace(
        agent_manager=_FakeAgentManager([managed]),
        engine=SimpleNamespace(state=OrchestratorState(session_id="session-1")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    snapshot = facade.get_agent("agent-1")
    assert isinstance(snapshot, OrchestratorAgentSnapshot)
    assert snapshot is not None
    assert snapshot.has_handle is True
    assert snapshot.awaiting_input is True
    assert snapshot.provider_thread_id == "thread-1"

    listed = facade.list_active_agents()
    assert [item.agent_id for item in listed] == ["agent-1"]


def test_facade_falls_back_to_agent_records_when_agent_manager_is_absent() -> None:
    running = _record("agent-1", task_id="task-1", status=AgentStatus.RUNNING, summary="Still working")
    completed = _record("agent-2", task_id="task-2", status=AgentStatus.COMPLETED, summary="Done")
    lifecycle = SimpleNamespace(
        engine=SimpleNamespace(state=OrchestratorState(session_id="session-2")),
        state_store=SimpleNamespace(agent_records=lambda: [running, completed]),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    snapshots = facade.list_agents(include_completed=False)
    assert [item.agent_id for item in snapshots] == ["agent-1"]
    assert snapshots[0].has_handle is False
    assert snapshots[0].active is True

    by_type = facade.list_agents(agent_type=AgentType.CODE)
    assert [item.agent_id for item in by_type] == ["agent-1", "agent-2"]

    completed_snapshot = facade.get_agent("agent-2")
    assert completed_snapshot is not None
    assert completed_snapshot.done is True
    assert completed_snapshot.active is False
    assert completed_snapshot.summary == "Done"


def test_facade_raises_for_invalid_workflow_status() -> None:
    lifecycle = SimpleNamespace(
        engine=SimpleNamespace(state=SimpleNamespace(status="mystery")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    with pytest.raises(ValueError, match="Unsupported orchestrator status"):
        facade.workflow_status()


def test_facade_raises_for_invalid_execution_mode() -> None:
    lifecycle = SimpleNamespace(
        engine=SimpleNamespace(state=OrchestratorState(session_id="session-3")),
        execution_mode="surprise",
    )

    facade = OrchestratorFacade(lifecycle)

    with pytest.raises(ValueError, match="Unsupported roadmap execution mode"):
        _ = facade.execution_mode


def test_facade_raises_for_invalid_managed_agent_snapshot() -> None:
    managed = SimpleNamespace(
        agent_id="agent-1",
        task_id="task-1",
        agent_type="code",
        status="mystery",
        state="running",
        has_handle=True,
        active=True,
        done=False,
        awaiting_input=False,
        input_requests=[],
    )
    lifecycle = SimpleNamespace(
        agent_manager=_FakeAgentManager([managed]),
        engine=SimpleNamespace(state=OrchestratorState(session_id="session-4")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    with pytest.raises(ValueError, match="Unsupported agent status"):
        facade.get_agent("agent-1")
