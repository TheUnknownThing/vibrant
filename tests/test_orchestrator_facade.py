from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import RoadmapExecutionMode
from vibrant.models.agent import AgentRunRecord, AgentStatus
from vibrant.models.state import OrchestratorState, OrchestratorStatus, QuestionRecord
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
    role: str = "code",
    status: AgentStatus = AgentStatus.RUNNING,
    summary: str | None = None,
) -> AgentRunRecord:
    now = datetime.now(timezone.utc)
    return AgentRunRecord(
        identity={"agent_id": agent_id, "task_id": task_id, "role": role},
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
        role = kwargs.get("role")

        snapshots = list(self._snapshots.values())
        if task_id is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.identity.task_id == task_id]
        if role is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.identity.role == role.strip().lower()]
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

    def list_active_agents(self):
        return self.list_agents(active_only=True)

    def list_agent_instances(self, **kwargs):
        return self.list_agents(**kwargs)


class _FakeQuestionService:
    def __init__(self, records: list[QuestionRecord] | None = None) -> None:
        self._records = list(records or [])

    def records(self) -> list[QuestionRecord]:
        return list(self._records)

    def pending_records(self) -> list[QuestionRecord]:
        return [record for record in self._records if record.is_pending()]

    def pending_questions(self) -> list[str]:
        return [record.text for record in self.pending_records()]

    def current_question(self) -> str | None:
        pending = self.pending_questions()
        return pending[0] if pending else None


def _state_store(status: OrchestratorStatus, *, records: list[AgentRunRecord] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        user_input_banner=lambda: "banner",
        notification_bell_enabled=lambda: False,
    )


def _facade_lifecycle(
    *,
    agent_manager: _FakeAgentManager,
    status: OrchestratorStatus = OrchestratorStatus.PLANNING,
    execution_mode: RoadmapExecutionMode = RoadmapExecutionMode.MANUAL,
    question_records: list[QuestionRecord] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        agent_manager=agent_manager,
        agent_output_service=SimpleNamespace(output_for_agent=lambda _agent_id: None),
        state_store=_state_store(status),
        execution_mode=execution_mode,
        question_service=_FakeQuestionService(question_records),
        roadmap_document=None,
        consensus_service=SimpleNamespace(current=lambda: None),
        consensus_path=Path("/tmp/project/.vibrant/consensus.md"),
    )


def test_facade_exposes_stable_agent_snapshot_from_agent_manager() -> None:
    output = AgentOutput(agent_id="agent-1", task_id="task-1", partial_text="Still working")
    managed = OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(agent_id="agent-1", task_id="task-1", role="code"),
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
        **_facade_lifecycle(agent_manager=_FakeAgentManager([managed])).__dict__,
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


def test_facade_lists_agents_from_agent_manager() -> None:
    running = OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(agent_id="agent-1", task_id="task-1", role="code"),
        runtime=AgentSnapshotRuntime(
            status="running",
            state="running",
            has_handle=False,
            active=True,
            done=False,
            awaiting_input=False,
        ),
    )
    completed = OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(agent_id="agent-2", task_id="task-2", role="code"),
        runtime=AgentSnapshotRuntime(
            status="completed",
            state="completed",
            has_handle=False,
            active=False,
            done=True,
            awaiting_input=False,
        ),
        outcome=AgentSnapshotOutcome(summary="Done"),
    )

    facade = OrchestratorFacade(_facade_lifecycle(agent_manager=_FakeAgentManager([running, completed])))

    snapshots = facade.list_agents(include_completed=False)
    assert [item.identity.agent_id for item in snapshots] == ["agent-1"]

    by_role = facade.list_agents(role="code")
    assert [item.identity.agent_id for item in by_role] == ["agent-1", "agent-2"]

    completed_snapshot = facade.get_agent("agent-2")
    assert completed_snapshot is not None
    assert completed_snapshot.runtime.done is True
    assert completed_snapshot.outcome.summary == "Done"


def test_facade_exposes_workflow_and_question_state() -> None:
    question = QuestionRecord(question_id="question-1", text="Need approval?")
    facade = OrchestratorFacade(
        _facade_lifecycle(
            agent_manager=_FakeAgentManager([]),
            status=OrchestratorStatus.PLANNING,
            question_records=[question],
        )
    )

    assert facade.get_workflow_status() is OrchestratorStatus.PLANNING
    assert facade.list_question_records() == [question]
    assert facade.list_pending_question_records() == [question]
    assert facade.get_current_pending_question() == "Need approval?"


def test_facade_snapshot_exposes_instance_snapshots() -> None:
    running = OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(agent_id="agent-1", task_id="task-1", role="code"),
        runtime=AgentSnapshotRuntime(
            status="running",
            state="running",
            has_handle=False,
            active=True,
            done=False,
            awaiting_input=False,
        ),
    )
    facade = OrchestratorFacade(_facade_lifecycle(agent_manager=_FakeAgentManager([running])))

    snapshot = facade.snapshot()
    assert [agent.identity.agent_id for agent in snapshot.agents] == ["agent-1"]


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
