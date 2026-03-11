from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.models.state import OrchestratorState
from vibrant.orchestrator import Orchestrator, OrchestratorAgentSnapshot, OrchestratorFacade
from vibrant.orchestrator.types import (
    AgentOutput,
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


def test_facade_exposes_stable_agent_snapshot_from_agent_manager() -> None:
    output = AgentOutput(agent_id="agent-1", task_id="task-1", partial_text="Still working")
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
        outcome=AgentSnapshotOutcome(summary="Waiting on user input", output=output),
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
        engine=SimpleNamespace(state=OrchestratorState(session_id="session-1")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    snapshot = facade.get_agent("agent-1")
    assert isinstance(snapshot, OrchestratorAgentSnapshot)
    assert snapshot is not None
    assert snapshot.runtime.has_handle is True
    assert snapshot.runtime.awaiting_input is True
    assert snapshot.provider.thread_id == "thread-1"
    assert facade.agent_output("agent-1") is output

    listed = facade.list_active_agents()
    assert [item.identity.agent_id for item in listed] == ["agent-1"]


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
        engine=SimpleNamespace(state=OrchestratorState(session_id="session-4")),
        execution_mode="manual",
    )

    facade = OrchestratorFacade(lifecycle)

    with pytest.raises(ValueError, match="Unsupported agent status"):
        facade.get_agent("agent-1")


class _FakeSubscribedOrchestrator:
    def __init__(self, snapshots: list[object]) -> None:
        self.agent_manager = _FakeAgentManager(snapshots)
        self.engine = SimpleNamespace(state=OrchestratorState(session_id="session-subscribe"))
        self.execution_mode = "manual"
        self._subscriptions: list[tuple[object, str | None, str | None, object]] = []

    def subscribe_raw_events(self, handler, *, agent_id=None, task_id=None, event_types=None):
        entry = (handler, agent_id, task_id, event_types)
        self._subscriptions.append(entry)

        def unsubscribe() -> None:
            try:
                self._subscriptions.remove(entry)
            except ValueError:
                return

        return unsubscribe


@pytest.mark.asyncio
async def test_facade_subscribe_agent_updates_emits_snapshots() -> None:
    managed = OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(agent_id="agent-1", task_id="task-1", agent_type="code"),
        runtime=AgentSnapshotRuntime(
            status="running",
            state="running",
            has_handle=False,
            active=True,
            done=False,
            awaiting_input=False,
        ),
    )
    lifecycle = _FakeSubscribedOrchestrator([managed])
    facade = OrchestratorFacade(lifecycle)
    seen: list[OrchestratorAgentSnapshot] = []

    unsubscribe = facade.subscribe_agent_updates(seen.append, agent_id="agent-1")
    handler, agent_id, task_id, event_types = lifecycle._subscriptions[0]

    assert agent_id == "agent-1"
    assert task_id is None
    assert event_types is None

    await handler({"agent_id": "agent-1", "task_id": "task-1", "type": "turn.started"})

    assert [item.identity.agent_id for item in seen] == ["agent-1"]

    unsubscribe()
    assert lifecycle._subscriptions == []


def _make_test_orchestrator() -> Orchestrator:
    state_store = SimpleNamespace(refresh=lambda: None, state=SimpleNamespace(concurrency_limit=1))
    roadmap_service = SimpleNamespace(parser=None, document=None, reload=lambda **_: None, dispatcher=None)
    return Orchestrator(
        project_root=Path("/tmp/project"),
        vibrant_dir=Path("/tmp/project/.vibrant"),
        roadmap_path=Path("/tmp/project/.vibrant/roadmap.md"),
        consensus_path=Path("/tmp/project/.vibrant/consensus.md"),
        skills_dir=Path("/tmp/project/.vibrant/skills"),
        config=SimpleNamespace(execution_mode="manual"),
        state_backend=SimpleNamespace(),
        gatekeeper=SimpleNamespace(),
        git_manager=SimpleNamespace(),
        adapter_factory=SimpleNamespace(),
        on_canonical_event=None,
        agent_output_service=SimpleNamespace(),
        state_store=state_store,
        agent_store=SimpleNamespace(),
        roadmap_service=roadmap_service,
        consensus_service=SimpleNamespace(),
        agent_registry=SimpleNamespace(),
        question_service=SimpleNamespace(),
        git_service=SimpleNamespace(),
        prompt_service=SimpleNamespace(),
        workflow_service=SimpleNamespace(),
        gatekeeper_runtime=SimpleNamespace(busy=False),
        review_service=SimpleNamespace(),
        planning_service=SimpleNamespace(),
        runtime_service=SimpleNamespace(),
        retry_service=SimpleNamespace(),
        execution_service=SimpleNamespace(),
        agent_manager=SimpleNamespace(),
        _config_holder={"value": SimpleNamespace(execution_mode="manual")},
    )


@pytest.mark.asyncio
async def test_orchestrator_subscribe_raw_events_filters_and_unsubscribes() -> None:
    orchestrator = _make_test_orchestrator()
    seen: list[dict[str, str]] = []

    unsubscribe = orchestrator.subscribe_raw_events(
        seen.append,
        agent_id="agent-1",
        task_id="task-1",
        event_types={"turn.started"},
    )

    await orchestrator._publish_raw_event(
        {"agent_id": "agent-1", "task_id": "task-1", "type": "turn.started", "timestamp": "2026-03-11T12:00:00Z"}
    )
    await orchestrator._publish_raw_event(
        {"agent_id": "agent-2", "task_id": "task-1", "type": "turn.started", "timestamp": "2026-03-11T12:00:01Z"}
    )

    assert seen == [
        {"agent_id": "agent-1", "task_id": "task-1", "type": "turn.started", "timestamp": "2026-03-11T12:00:00Z"}
    ]

    unsubscribe()
    await orchestrator._publish_raw_event(
        {"agent_id": "agent-1", "task_id": "task-1", "type": "turn.started", "timestamp": "2026-03-11T12:00:02Z"}
    )

    assert len(seen) == 1
