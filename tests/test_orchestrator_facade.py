from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import RoadmapExecutionMode
from vibrant.models.agent import AgentRunRecord, AgentStatus
from vibrant.models.state import OrchestratorState, OrchestratorStatus, QuestionRecord
from vibrant.orchestrator import AgentInstanceSnapshot, AgentRoleSnapshot, Orchestrator, OrchestratorFacade
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


def _canonical_log_entry(event_type: str, timestamp: str, **data: object) -> str:
    return json.dumps(
        {
            "timestamp": timestamp,
            "event": event_type,
            "data": data,
        }
    )


class _FakeAgentManager:
    def __init__(self, snapshots: list[object], *, run_records: list[AgentRunRecord] | None = None) -> None:
        self._snapshots = {snapshot.identity.agent_id: snapshot for snapshot in snapshots}
        self._run_records = list(run_records or [])
        self._roles = [
            AgentRoleSnapshot(
                role="code",
                display_name="Code",
                workflow_class="execution",
                default_provider_kind="codex",
                default_runtime_mode="workspace-write",
                supports_interactive_requests=False,
                persistent_thread=False,
            ),
            AgentRoleSnapshot(
                role="gatekeeper",
                display_name="Gatekeeper",
                workflow_class="planning-control",
                default_provider_kind="codex",
                default_runtime_mode="read-only",
                supports_interactive_requests=True,
                persistent_thread=True,
                question_source_role="gatekeeper",
                contributes_control_plane_status=True,
                ui_model_name="gatekeeper",
            ),
        ]

    def get_role(self, role: str):
        normalized = role.strip().lower()
        for snapshot in self._roles:
            if snapshot.role == normalized:
                return snapshot
        return None

    def list_roles(self):
        return list(self._roles)

    def get_instance_snapshot(self, agent_id: str):
        return self._snapshots.get(agent_id)

    def list_instance_snapshots(self, **kwargs):
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

    def list_run_records(self, **kwargs):
        task_id = kwargs.get("task_id")
        agent_id = kwargs.get("agent_id")
        role = kwargs.get("role")
        records = list(self._run_records)
        if task_id is not None:
            records = [record for record in records if record.identity.task_id == task_id]
        if agent_id is not None:
            records = [record for record in records if record.identity.agent_id == agent_id]
        if role is not None:
            records = [record for record in records if record.identity.role == role.strip().lower()]
        return records

    def get_run_record(self, run_id: str):
        for record in self._run_records:
            if record.identity.run_id == run_id:
                return record
        return None

    def list_active_instance_snapshots(self):
        return self.list_instance_snapshots(active_only=True)


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

    def current_record(self) -> QuestionRecord | None:
        pending = self.pending_records()
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
    managed = AgentInstanceSnapshot(
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

    snapshot = facade.instances.get("agent-1")
    assert isinstance(snapshot, AgentInstanceSnapshot)
    assert snapshot is not None
    assert snapshot.runtime.has_handle is True
    assert snapshot.runtime.awaiting_input is True
    assert snapshot.provider.thread_id == "thread-1"
    assert facade.instances.output("agent-1") is output

    listed = facade.instances.active()
    assert [item.identity.agent_id for item in listed] == ["agent-1"]


def test_facade_lists_agents_from_agent_manager() -> None:
    running = AgentInstanceSnapshot(
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
    completed = AgentInstanceSnapshot(
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

    snapshots = facade.instances.list(include_completed=False)
    assert [item.identity.agent_id for item in snapshots] == ["agent-1"]

    by_role = facade.instances.list(role="code")
    assert [item.identity.agent_id for item in by_role] == ["agent-1", "agent-2"]

    completed_snapshot = facade.instances.get("agent-2")
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
    running = AgentInstanceSnapshot(
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
    assert [role.role for role in snapshot.roles] == ["code", "gatekeeper"]
    assert [instance.identity.agent_id for instance in snapshot.instances] == ["agent-1"]
    assert snapshot.workflow.status is OrchestratorStatus.PLANNING
    assert snapshot.documents.consensus_path == Path("/tmp/project/.vibrant/consensus.md")
    assert snapshot.questions == ()


def test_facade_run_api_projects_stable_run_snapshots() -> None:
    run_record = _record(
        "agent-1",
        task_id="task-1",
        status=AgentStatus.COMPLETED,
        summary="Completed the task.",
    )
    instance = AgentInstanceSnapshot(
        identity=AgentSnapshotIdentity(
            agent_id="agent-1",
            task_id="task-1",
            role="code",
            run_id=run_record.identity.run_id,
            scope_type="task",
            scope_id="task-1",
        ),
        runtime=AgentSnapshotRuntime(
            status="completed",
            state="completed",
            has_handle=False,
            active=False,
            done=True,
            awaiting_input=False,
        ),
    )
    facade = OrchestratorFacade(
        _facade_lifecycle(
            agent_manager=_FakeAgentManager([instance], run_records=[run_record]),
        )
    )

    snapshot = facade.runs.latest_for_instance("agent-1")
    assert snapshot is not None
    assert snapshot.run_id == run_record.identity.run_id
    assert snapshot.identity.run_id == run_record.identity.run_id
    assert snapshot.envelope.summary == "Completed the task."
    assert snapshot.provider.provider_thread_id is None


def test_facade_run_events_reads_canonical_log_in_order(tmp_path: Path) -> None:
    run_record = _record(
        "agent-1",
        task_id="task-1",
        status=AgentStatus.COMPLETED,
        summary="Completed the task.",
    )
    log_path = tmp_path / "canonical.ndjson"
    log_path.write_text(
        "\n".join(
            [
                _canonical_log_entry(
                    "session.started",
                    "2026-03-12T12:00:00Z",
                    run_id=run_record.identity.run_id,
                    agent_id=run_record.identity.agent_id,
                    task_id=run_record.identity.task_id,
                    origin="provider",
                ),
                _canonical_log_entry(
                    "turn.started",
                    "2026-03-12T12:00:01Z",
                    run_id=run_record.identity.run_id,
                    agent_id=run_record.identity.agent_id,
                    task_id=run_record.identity.task_id,
                    turn_id="turn-1",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run_record.provider.canonical_event_log = str(log_path)

    facade = OrchestratorFacade(
        _facade_lifecycle(
            agent_manager=_FakeAgentManager([], run_records=[run_record]),
        )
    )

    events = facade.runs.events(run_record.identity.run_id)

    assert [event["type"] for event in events] == ["session.started", "turn.started"]
    assert events[0]["timestamp"] == "2026-03-12T12:00:00Z"
    assert events[1]["turn_id"] == "turn-1"


def test_facade_run_events_handles_missing_and_partial_logs(tmp_path: Path) -> None:
    run_record = _record(
        "agent-1",
        task_id="task-1",
        status=AgentStatus.RUNNING,
        summary="Still running.",
    )
    log_path = tmp_path / "canonical.ndjson"
    run_record.provider.canonical_event_log = str(log_path)
    facade = OrchestratorFacade(
        _facade_lifecycle(
            agent_manager=_FakeAgentManager([], run_records=[run_record]),
        )
    )

    assert facade.runs.events(run_record.identity.run_id) == []

    log_path.write_text(
        _canonical_log_entry(
            "turn.completed",
            "2026-03-12T12:00:02Z",
            run_id=run_record.identity.run_id,
            agent_id=run_record.identity.agent_id,
            task_id=run_record.identity.task_id,
            turn_id="turn-1",
        )
        + "\n"
        + '{"timestamp": "2026-03-12T12:00:03Z", "event": ',
        encoding="utf-8",
    )

    assert facade.runs.events(run_record.identity.run_id) == [
        {
            "type": "turn.completed",
            "timestamp": "2026-03-12T12:00:02Z",
            "run_id": run_record.identity.run_id,
            "agent_id": run_record.identity.agent_id,
            "task_id": run_record.identity.task_id,
            "turn_id": "turn-1",
        }
    ]


def test_facade_run_events_raise_for_unknown_run() -> None:
    facade = OrchestratorFacade(_facade_lifecycle(agent_manager=_FakeAgentManager([])))

    with pytest.raises(KeyError, match="Unknown run: missing-run"):
        facade.runs.events("missing-run")


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


@pytest.mark.asyncio
async def test_facade_run_subscribe_filters_by_run_id_and_unsubscribes() -> None:
    orchestrator = _make_test_orchestrator()
    run_record = _record("agent-1", task_id="task-1", status=AgentStatus.RUNNING)
    orchestrator.agent_manager = _FakeAgentManager([], run_records=[run_record])
    facade = OrchestratorFacade(orchestrator)
    seen: list[dict[str, str]] = []

    unsubscribe = facade.runs.subscribe(
        run_record.identity.run_id,
        seen.append,
        event_types={"turn.started"},
    )

    await orchestrator._publish_raw_event(
        {
            "run_id": run_record.identity.run_id,
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "turn.started",
            "timestamp": "2026-03-12T12:00:00Z",
        }
    )
    await orchestrator._publish_raw_event(
        {
            "run_id": "run-other",
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "turn.started",
            "timestamp": "2026-03-12T12:00:01Z",
        }
    )
    await orchestrator._publish_raw_event(
        {
            "run_id": run_record.identity.run_id,
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "turn.completed",
            "timestamp": "2026-03-12T12:00:02Z",
        }
    )

    assert seen == [
        {
            "run_id": run_record.identity.run_id,
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "turn.started",
            "timestamp": "2026-03-12T12:00:00Z",
        }
    ]

    unsubscribe()
    await orchestrator._publish_raw_event(
        {
            "run_id": run_record.identity.run_id,
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "turn.started",
            "timestamp": "2026-03-12T12:00:03Z",
        }
    )

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_orchestrator_subscribe_raw_events_continues_after_handler_failure() -> None:
    orchestrator = _make_test_orchestrator()
    seen: list[dict[str, str]] = []

    def _explode(_event: dict[str, str]) -> None:
        raise RuntimeError("boom")

    orchestrator.subscribe_raw_events(_explode, task_id="task-1")
    orchestrator.subscribe_raw_events(seen.append, task_id="task-1")

    await orchestrator._publish_raw_event(
        {"agent_id": "agent-1", "task_id": "task-1", "type": "turn.started", "timestamp": "2026-03-12T12:00:00Z"}
    )

    assert seen == [
        {"agent_id": "agent-1", "task_id": "task-1", "type": "turn.started", "timestamp": "2026-03-12T12:00:00Z"}
    ]
