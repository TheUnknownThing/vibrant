from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.models.state import OrchestratorState
from vibrant.orchestrator import OrchestratorAgentSnapshot, OrchestratorFacade
from vibrant.orchestrator.types import (
    AgentSnapshotIdentity,
    AgentSnapshotOutcome,
    AgentSnapshotProvider,
    AgentSnapshotRuntime,
    AgentSnapshotWorkspace,
)


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
        identity={"agent_id": agent_id, "task_id": task_id, "type": agent_type},
        lifecycle={"status": status, "started_at": now},
        outcome={"summary": summary},
    )


class _FakeAgentManager:
    def __init__(self, snapshots: list[object]) -> None:
        self._snapshots = {snapshot.identity.agent_id: snapshot for snapshot in snapshots}

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
            snapshots = [snapshot for snapshot in snapshots if snapshot.identity.task_id == task_id]
        if agent_type_value is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.identity.agent_type == agent_type_value]
        if active_only:
            snapshots = [snapshot for snapshot in snapshots if snapshot.runtime.active]
        elif not include_completed:
            snapshots = [
                snapshot
                for snapshot in snapshots
                if not snapshot.runtime.done or snapshot.runtime.awaiting_input
            ]
        return snapshots

    def list_records(self):
        return []


def _state_store(state: object, *, records: list[AgentRecord] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        pending_questions=lambda: [],
        agent_records=lambda: list(records or []),
        user_input_banner=lambda: "banner",
        notification_bell_enabled=lambda: False,
    )


def test_facade_exposes_stable_agent_snapshot_from_agent_manager() -> None:
    managed = OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(agent_id="agent-1", task_id="task-1", agent_type="code"),
        runtime=AgentSnapshotRuntime(
            status="running",
            state="awaiting_input",
            has_handle=True,
            active=True,
            done=False,
            awaiting_input=True,
            pid=123,
            started_at=datetime.now(timezone.utc),
        ),
        workspace=AgentSnapshotWorkspace(branch="vibrant/task-1", worktree_path="/tmp/worktree"),
        outcome=AgentSnapshotOutcome(summary="Waiting on user input"),
        provider=AgentSnapshotProvider(
            thread_id="thread-1",
            thread_path="/tmp/thread.json",
            resume_cursor={"cursor": 1},
            native_event_log="native.ndjson",
            canonical_event_log="canonical.ndjson",
        ),
    )
    lifecycle = SimpleNamespace(
        agent_manager=_FakeAgentManager([managed]),
        state_store=_state_store(OrchestratorState(session_id="session-1")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    snapshot = facade.get_agent("agent-1")
    assert isinstance(snapshot, OrchestratorAgentSnapshot)
    assert snapshot is not None
    assert snapshot.runtime.has_handle is True
    assert snapshot.runtime.awaiting_input is True
    assert snapshot.provider.thread_id == "thread-1"

    listed = facade.list_active_agents()
    assert [item.identity.agent_id for item in listed] == ["agent-1"]


def test_facade_falls_back_to_agent_records_when_agent_manager_is_absent() -> None:
    running = _record("agent-1", task_id="task-1", status=AgentStatus.RUNNING, summary="Still working")
    completed = _record("agent-2", task_id="task-2", status=AgentStatus.COMPLETED, summary="Done")
    lifecycle = SimpleNamespace(
        state_store=_state_store(OrchestratorState(session_id="session-2"), records=[running, completed]),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    snapshots = facade.list_agents(include_completed=False)
    assert [item.identity.agent_id for item in snapshots] == ["agent-1"]
    assert snapshots[0].runtime.has_handle is False
    assert snapshots[0].runtime.active is True

    by_type = facade.list_agents(agent_type=AgentType.CODE)
    assert [item.identity.agent_id for item in by_type] == ["agent-1", "agent-2"]

    completed_snapshot = facade.get_agent("agent-2")
    assert completed_snapshot is not None
    assert completed_snapshot.runtime.done is True
    assert completed_snapshot.runtime.active is False
    assert completed_snapshot.outcome.summary == "Done"


def test_facade_raises_for_invalid_workflow_status() -> None:
    lifecycle = SimpleNamespace(
        state_store=_state_store(SimpleNamespace(status="mystery")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    with pytest.raises(ValueError, match="Unsupported orchestrator status"):
        facade.workflow_status()


def test_facade_raises_for_invalid_execution_mode() -> None:
    lifecycle = SimpleNamespace(
        state_store=_state_store(OrchestratorState(session_id="session-3")),
        execution_mode="surprise",
    )

    facade = OrchestratorFacade(lifecycle)

    with pytest.raises(ValueError, match="Unsupported roadmap execution mode"):
        _ = facade.execution_mode


def test_facade_raises_for_invalid_managed_agent_snapshot() -> None:
    managed = OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(agent_id="agent-1", task_id="task-1", agent_type="code"),
        runtime=AgentSnapshotRuntime(
            status="mystery",
            state="running",
            has_handle=True,
            active=True,
            done=False,
            awaiting_input=False,
        ),
    )
    lifecycle = SimpleNamespace(
        agent_manager=_FakeAgentManager([managed]),
        state_store=_state_store(OrchestratorState(session_id="session-4")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    with pytest.raises(ValueError, match="Unsupported agent status"):
        facade.get_agent("agent-1")
