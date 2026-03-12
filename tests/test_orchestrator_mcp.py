from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import RoadmapExecutionMode
from vibrant.mcp.authz import (
    MCP_ACCESS_SCOPE,
    MCPAuthorizationError,
    MCPPrincipal,
    ORCHESTRATOR_WORKFLOW_WRITE_SCOPE,
    TASKS_RUN_SCOPE,
    orchestrator_agent_scopes,
    orchestrator_gatekeeper_scopes,
)
from vibrant.models.agent import AgentRecord, AgentStatus
from vibrant.models.state import OrchestratorState, OrchestratorStatus, QuestionStatus
from vibrant.models.task import TaskStatus
from vibrant.orchestrator import OrchestratorStateBackend
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.mcp import OrchestratorMCPServer
from vibrant.orchestrator.artifacts import ConsensusService
from vibrant.orchestrator.artifacts import QuestionService
from vibrant.orchestrator.artifacts import RoadmapService
from vibrant.orchestrator.state import StateStore
from vibrant.orchestrator.tasks.store import TaskStore
from vibrant.orchestrator.tasks.workflow import TaskWorkflowService
from vibrant.orchestrator.types import (
    AgentSnapshotIdentity,
    AgentSnapshotOutcome,
    AgentSnapshotProvider,
    AgentSnapshotRuntime,
    AgentSnapshotWorkspace,
    OrchestratorAgentSnapshot,
)
from vibrant.project_init import initialize_project


class _StubGatekeeper:
    async def answer_question(self, question: str, answer: str):  # pragma: no cover - not exercised here
        raise NotImplementedError


class _StubAgentManager:
    def __init__(self, state_store: StateStore | None = None) -> None:
        self.state_store = state_store

    def list_records(self):
        if self.state_store is None:
            return []
        return self.state_store.agent_records()

    def get_agent(self, agent_id: str):
        if self.state_store is None:
            return None
        for record in self.state_store.agent_records():
            if record.identity.agent_id == agent_id:
                return _snapshot_from_record(record)
        return None

    def list_agents(self, **kwargs):
        if self.state_store is None:
            return []
        task_id = kwargs.get("task_id")
        include_completed = kwargs.get("include_completed", True)
        active_only = kwargs.get("active_only", False)
        role = kwargs.get("role")

        snapshots = [_snapshot_from_record(record) for record in self.state_store.agent_records()]
        if task_id is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.identity.task_id == task_id]
        if role is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.identity.role == role.strip().lower()]
        if active_only:
            snapshots = [snapshot for snapshot in snapshots if snapshot.runtime.active]
        elif not include_completed:
            snapshots = [snapshot for snapshot in snapshots if not snapshot.runtime.done or snapshot.runtime.awaiting_input]
        return snapshots

    def list_active_agents(self):
        return self.list_agents(active_only=True)

    async def wait_for_agent(self, agent_id: str, *, release_terminal: bool = True):
        return {
            "agent_id": agent_id,
            "state": "completed",
            "release_terminal": release_terminal,
        }

    async def respond_to_request(
        self,
        agent_id: str,
        request_id: int | str,
        *,
        result=None,
        error=None,
    ):
        return {
            "identity": {"agent_id": agent_id, "task_id": "task-1", "role": "code"},
            "runtime": {"status": "running", "state": "running", "has_handle": True, "active": True, "done": False, "awaiting_input": False},
            "workspace": {"branch": "vibrant/task-1", "worktree_path": "/tmp/task-1"},
            "outcome": {"summary": None, "error": None, "output": None},
            "provider": {"thread_id": "thread-1", "thread_path": None, "resume_cursor": None, "native_event_log": None, "canonical_event_log": None},
            "request": {"request_id": str(request_id), "result": result, "error": error},
        }


def _snapshot_from_record(record: AgentRecord) -> OrchestratorAgentSnapshot:
    status = record.lifecycle.status.value
    done = record.lifecycle.status not in {AgentStatus.RUNNING, AgentStatus.AWAITING_INPUT}
    awaiting_input = record.lifecycle.status is AgentStatus.AWAITING_INPUT
    return OrchestratorAgentSnapshot(
        identity=AgentSnapshotIdentity(
            agent_id=record.identity.agent_id,
            task_id=record.identity.task_id,
            role=record.identity.role,
        ),
        runtime=AgentSnapshotRuntime(
            status=status,
            state=status,
            has_handle=False,
            active=not done,
            done=done,
            awaiting_input=awaiting_input,
            started_at=record.lifecycle.started_at,
            finished_at=record.lifecycle.finished_at,
        ),
        workspace=AgentSnapshotWorkspace(
            branch=record.context.branch,
            worktree_path=record.context.worktree_path,
        ),
        outcome=AgentSnapshotOutcome(
            summary=record.outcome.summary,
            error=record.outcome.error,
            output=None,
        ),
        provider=AgentSnapshotProvider(
            thread_id=record.provider.provider_thread_id,
            thread_path=record.provider.thread_path,
            resume_cursor=record.provider.resume_cursor,
            native_event_log=record.provider.native_event_log,
            canonical_event_log=record.provider.canonical_event_log,
        ),
    )


def _build_facade(tmp_path: Path) -> tuple[OrchestratorFacade, StateStore, QuestionService, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    engine = OrchestratorStateBackend.load(repo, notification_bell_enabled=False)
    state_store = StateStore(engine)
    roadmap_service = RoadmapService(repo / ".vibrant" / "roadmap.md", project_name=repo.name)
    roadmap_service.reload(project_name=repo.name, concurrency_limit=engine.state.concurrency_limit)
    consensus_service = ConsensusService(repo / ".vibrant" / "consensus.md", state_store=state_store)
    question_service = QuestionService(state_store=state_store, gatekeeper=_StubGatekeeper())
    lifecycle = SimpleNamespace(
        project_root=repo,
        engine=engine,
        state_store=state_store,
        roadmap_service=roadmap_service,
        consensus_service=consensus_service,
        question_service=question_service,
        agent_manager=_StubAgentManager(state_store),
        agent_output_service=SimpleNamespace(output_for_agent=lambda _agent_id: None),
        workflow_service=SimpleNamespace(begin_execution_if_needed=lambda: state_store.transition_to(OrchestratorStatus.EXECUTING)),
        execution_mode=RoadmapExecutionMode.MANUAL,
    )
    return OrchestratorFacade(lifecycle), state_store, question_service, repo


def _persist_agent_record(state_store: StateStore, *, agent_id: str, task_id: str, status: str = "completed") -> None:
    record = AgentRecord.model_validate(
        {
            "identity": {"agent_id": agent_id, "task_id": task_id, "role": "code"},
            "lifecycle": {"status": status},
            "context": {"branch": f"vibrant/{task_id}", "worktree_path": f"/tmp/{task_id}", "prompt_used": "Prompt"},
            "outcome": {"summary": "Implemented successfully." if status == "completed" else None},
            "provider": {"provider_thread_id": f"thread-{task_id}", "canonical_event_log": f"/tmp/{agent_id}.ndjson"},
        }
    )
    state_store.engine.upsert_agent_record(record)


def test_state_migrates_legacy_pending_questions_to_records() -> None:
    state = OrchestratorState.model_validate(
        {
            "session_id": "session-123",
            "status": "planning",
            "pending_questions": ["Q1", "Q2"],
        }
    )

    assert [record.text for record in state.questions] == ["Q1", "Q2"]
    assert [record.status for record in state.questions] == [QuestionStatus.PENDING, QuestionStatus.PENDING]
    assert state.pending_questions == ["Q1", "Q2"]


def test_question_service_tracks_structured_records(tmp_path: Path) -> None:
    _facade, state_store, questions, _repo = _build_facade(tmp_path)

    created = questions.ask("Should we ship the UI in v1?", source_agent_id="gatekeeper-1")
    assert created.question_id.startswith("question-")
    assert created.source_role == "gatekeeper"
    assert state_store.state.pending_questions == ["Should we ship the UI in v1?"]
    assert state_store.state.questions[0].source_agent_id == "gatekeeper-1"

    resolved = questions.resolve(created.question_id, answer="Yes, ship it.")
    assert resolved.status is QuestionStatus.ANSWERED
    assert resolved.answer == "Yes, ship it."
    assert state_store.state.pending_questions == []


@pytest.mark.asyncio
async def test_orchestrator_mcp_server_enforces_shared_scopes_and_mutates_state(tmp_path: Path) -> None:
    facade, state_store, _questions, _repo = _build_facade(tmp_path)
    state_store.transition_to(OrchestratorStatus.PLANNING)

    server = OrchestratorMCPServer(facade)
    gatekeeper = MCPPrincipal(scopes=orchestrator_gatekeeper_scopes(), subject_id="gatekeeper-1")
    agent = MCPPrincipal(scopes=orchestrator_agent_scopes(), subject_id="agent-task-1")

    created = await server.call_tool(
        "roadmap_add_task",
        principal=gatekeeper,
        task={
            "id": "task-1",
            "title": "Add MCP facade tests",
            "acceptance_criteria": ["Server exposes task_get"],
        },
    )
    assert created["id"] == "task-1"

    question = await server.call_tool(
        "question_ask_user",
        principal=gatekeeper,
        text="Should we expose workflow_pause to agents?",
    )
    assert question["text"] == "Should we expose workflow_pause to agents?"

    pending = await server.read_resource("questions.pending", principal=gatekeeper)
    assert [item["text"] for item in pending] == ["Should we expose workflow_pause to agents?"]

    fetched = await server.call_tool("task_get", principal=agent, task_id="task-1")
    assert fetched["title"] == "Add MCP facade tests"

    with pytest.raises(MCPAuthorizationError, match="roadmap_update_task"):
        await server.call_tool(
            "roadmap_update_task",
            principal=agent,
            task_id="task-1",
            updates={"title": "Nope"},
        )

    with pytest.raises(MCPAuthorizationError, match="questions.pending"):
        await server.read_resource("questions.pending", principal=agent)

    paused = await server.call_tool("workflow_pause", principal=gatekeeper)
    assert paused == {"status": "paused"}

    workflow = await server.read_resource("workflow.status", principal=gatekeeper)
    assert workflow == {"status": "paused"}


@pytest.mark.asyncio
async def test_orchestrator_mcp_server_supports_vibrant_gatekeeper_tools(tmp_path: Path) -> None:
    facade, state_store, _questions, _repo = _build_facade(tmp_path)
    state_store.transition_to(OrchestratorStatus.PLANNING)

    server = OrchestratorMCPServer(facade)
    gatekeeper = MCPPrincipal(scopes=orchestrator_gatekeeper_scopes(), subject_id="gatekeeper-1")

    updated_consensus = await server.call_tool(
        "vibrant.update_consensus",
        principal=gatekeeper,
        status="planning",
        context="## Objectives\nShip MCP-driven orchestration.",
    )
    assert updated_consensus["context"] == "## Objectives\nShip MCP-driven orchestration."

    updated_roadmap = await server.call_tool(
        "vibrant.update_roadmap",
        principal=gatekeeper,
        tasks=[
            {
                "id": "task-1",
                "title": "Implement MCP-backed reviews",
                "acceptance_criteria": ["Gatekeeper can record an accepted verdict"],
            }
        ],
    )
    assert [task["id"] for task in updated_roadmap["tasks"]] == ["task-1"]

    pending = await server.call_tool(
        "vibrant.set_pending_questions",
        principal=gatekeeper,
        questions=["Should retries stay automatic?"],
    )
    assert [item["text"] for item in pending if item["status"] == "pending"] == ["Should retries stay automatic?"]
    assert state_store.state.gatekeeper_status.value == "awaiting_user"

    decision = await server.call_tool(
        "vibrant.request_user_decision",
        principal=gatekeeper,
        question="Should we expose the new MCPs now?",
    )
    assert decision["text"] == "Should we expose the new MCPs now?"

    transitioned = await server.call_tool("vibrant.end_planning_phase", principal=gatekeeper)
    assert transitioned == {"status": "executing"}


@pytest.mark.asyncio
async def test_orchestrator_mcp_server_review_tools_mutate_task_state(tmp_path: Path) -> None:
    facade, state_store, _questions, _repo = _build_facade(tmp_path)
    state_store.transition_to(OrchestratorStatus.PLANNING)
    facade.orchestrator.task_workflow = TaskWorkflowService(task_store=TaskStore(state_store=state_store))
    server = OrchestratorMCPServer(facade)
    gatekeeper = MCPPrincipal(scopes=orchestrator_gatekeeper_scopes(), subject_id="gatekeeper-1")

    await server.call_tool(
        "vibrant.update_roadmap",
        principal=gatekeeper,
        tasks=[
            {
                "id": "task-1",
                "title": "Implement MCP-backed reviews",
                "acceptance_criteria": ["Gatekeeper can record an accepted verdict"],
                "status": "completed",
            }
        ],
    )

    accepted = await server.call_tool(
        "vibrant.review_task_outcome",
        principal=gatekeeper,
        task_id="task-1",
        decision="accepted",
    )
    assert accepted["status"] == "accepted"

    await server.call_tool(
        "vibrant.update_roadmap",
        principal=gatekeeper,
        tasks=[
            {
                "id": "task-2",
                "title": "Retry flaky implementation",
                "acceptance_criteria": ["Task is requeued with updated prompt"],
                "status": "completed",
                "max_retries": 2,
                "retry_count": 0,
            }
        ],
    )
    retried = await server.call_tool(
        "vibrant.mark_task_for_retry",
        principal=gatekeeper,
        task_id="task-2",
        failure_reason="Needs a safer retry path",
        prompt="Retry with a safer implementation plan.",
    )
    assert retried["status"] == "queued"
    assert retried["prompt"] == "Retry with a safer implementation plan."

    await server.call_tool(
        "vibrant.update_roadmap",
        principal=gatekeeper,
        tasks=[
            {
                "id": "task-3",
                "title": "Reject unsafe implementation",
                "acceptance_criteria": ["Rejected reviews stay distinguishable in history"],
                "status": "completed",
            }
        ],
    )
    rejected = await server.call_tool(
        "vibrant.review_task_outcome",
        principal=gatekeeper,
        task_id="task-3",
        decision="rejected",
        failure_reason="Unsafe database migration plan",
    )
    assert rejected["status"] == "failed"
    assert state_store.state.tasks["task-3"].reviews[-1].decision.value == "rejected"


@pytest.mark.asyncio
async def test_orchestrator_mcp_server_exposes_agent_and_event_reads(tmp_path: Path) -> None:
    facade, state_store, _questions, _repo = _build_facade(tmp_path)
    state_store.transition_to(OrchestratorStatus.PLANNING)
    server = OrchestratorMCPServer(facade)
    gatekeeper = MCPPrincipal(scopes=orchestrator_gatekeeper_scopes(), subject_id="gatekeeper-1")
    agent = MCPPrincipal(scopes=orchestrator_agent_scopes(), subject_id="agent-task-1")

    await server.call_tool(
        "roadmap_add_task",
        principal=gatekeeper,
        task={
            "id": "task-1",
            "title": "Inspect assignment surfaces",
            "acceptance_criteria": ["Task and agent data are queryable"],
        },
    )
    _persist_agent_record(state_store, agent_id="agent-task-1", task_id="task-1")
    state_store.append_event(
        {
            "type": "task.progress",
            "timestamp": "2026-03-11T12:00:00Z",
            "agent_id": "agent-task-1",
            "task_id": "task-1",
            "item": {"message": "started"},
        }
    )

    assigned = await server.read_resource("task.assigned", principal=agent, task_id="task-1")
    assert assigned["task"]["id"] == "task-1"
    assert assigned["latest_agent"]["identity"]["agent_id"] == "agent-task-1"

    status = await server.read_resource("agent.status", principal=agent, agent_id="agent-task-1")
    assert status["identity"]["task_id"] == "task-1"
    assert status["runtime"]["done"] is True

    events = await server.read_resource("events.recent", principal=agent, task_id="task-1")
    assert len(events) == 1
    assert events[0]["type"] == "task.progress"


@pytest.mark.asyncio
async def test_orchestrator_mcp_server_supports_safe_agent_tools(tmp_path: Path) -> None:
    facade, state_store, _questions, _repo = _build_facade(tmp_path)
    state_store.transition_to(OrchestratorStatus.PLANNING)
    server = OrchestratorMCPServer(facade)
    gatekeeper = MCPPrincipal(scopes=orchestrator_gatekeeper_scopes(), subject_id="gatekeeper-1")
    agent = MCPPrincipal(scopes=orchestrator_agent_scopes(), subject_id="agent-task-1")
    runner = MCPPrincipal(
        scopes=(MCP_ACCESS_SCOPE, TASKS_RUN_SCOPE, ORCHESTRATOR_WORKFLOW_WRITE_SCOPE),
        subject_id="operator-1",
    )

    await server.call_tool(
        "roadmap_add_task",
        principal=gatekeeper,
        task={
            "id": "task-1",
            "title": "Inspect tool surfaces",
            "acceptance_criteria": ["Agent query tools work"],
        },
    )
    _persist_agent_record(state_store, agent_id="agent-task-1", task_id="task-1")

    agent_snapshot = await server.call_tool("agent_get", principal=agent, agent_id="agent-task-1")
    assert agent_snapshot["identity"]["agent_id"] == "agent-task-1"

    agent_list = await server.call_tool("agent_list", principal=agent, task_id="task-1")
    assert [item["identity"]["agent_id"] for item in agent_list] == ["agent-task-1"]

    agent_result = await server.call_tool("agent_result_get", principal=agent, agent_id="agent-task-1")
    assert agent_result["summary"] == "Implemented successfully."

    async def _run_next_task():
        return {"task_id": "task-1", "outcome": "accepted", "task_status": TaskStatus.ACCEPTED.value}

    facade.orchestrator.run_next_task = _run_next_task
    executed = await server.call_tool("workflow_execute_next_task", principal=runner)
    assert executed == {"task_id": "task-1", "outcome": "accepted", "task_status": "accepted"}

    facade.orchestrator.agent_manager = _StubAgentManager()
    waited = await server.call_tool("agent_wait", principal=agent, agent_id="agent-task-1")
    assert waited["state"] == "completed"

    responded = await server.call_tool(
        "agent_respond_to_request",
        principal=runner,
        agent_id="agent-task-1",
        request_id="req-1",
        result={"approved": True},
    )
    assert responded["request"]["request_id"] == "req-1"
    assert responded["request"]["result"] == {"approved": True}

    with pytest.raises(MCPAuthorizationError, match="workflow_execute_next_task"):
        await server.call_tool("workflow_execute_next_task", principal=agent)
