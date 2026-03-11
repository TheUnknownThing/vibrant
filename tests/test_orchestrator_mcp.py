from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import RoadmapExecutionMode
from vibrant.mcp.authz import (
    MCPAuthorizationError,
    MCPPrincipal,
    orchestrator_agent_scopes,
    orchestrator_gatekeeper_scopes,
)
from vibrant.models.state import OrchestratorState, OrchestratorStatus, QuestionStatus
from vibrant.orchestrator.engine import OrchestratorEngine
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.mcp import OrchestratorMCPServer
from vibrant.orchestrator.artifacts import ConsensusService
from vibrant.orchestrator.artifacts import QuestionService
from vibrant.orchestrator.artifacts import RoadmapService
from vibrant.orchestrator.state import StateStore
from vibrant.project_init import initialize_project


class _StubGatekeeper:
    async def answer_question(self, question: str, answer: str):  # pragma: no cover - not exercised here
        raise NotImplementedError


def _build_facade(tmp_path: Path) -> tuple[OrchestratorFacade, StateStore, QuestionService, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    engine = OrchestratorEngine.load(repo, notification_bell_enabled=False)
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
        execution_mode=RoadmapExecutionMode.MANUAL,
    )
    return OrchestratorFacade(lifecycle), state_store, question_service, repo


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
        objectives="Ship MCP-driven orchestration.",
    )
    assert updated_consensus["objectives"] == "Ship MCP-driven orchestration."

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
