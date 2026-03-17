from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from vibrant.orchestrator import OrchestratorFacade, create_orchestrator
from vibrant.orchestrator.policy.shared.capabilities import gatekeeper_principal
from vibrant.orchestrator.types import AttemptStatus
from vibrant.project_init import initialize_project


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _initialize_git_repo(project_root: Path) -> None:
    _git(project_root, "init", "-b", "main")
    _git(project_root, "config", "user.name", "Vibrant Tests")
    _git(project_root, "config", "user.email", "vibrant-tests@example.com")
    _git(project_root, "add", ".")
    _git(project_root, "commit", "-m", "Initial commit")


def _build_server(tmp_path: Path):
    initialize_project(tmp_path)
    _initialize_git_repo(tmp_path)
    orchestrator = create_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    assert orchestrator.mcp_server is not None
    return facade, orchestrator, orchestrator.mcp_server, gatekeeper_principal()


@pytest.mark.asyncio
async def test_mcp_server_supports_semantic_tools_and_resources(tmp_path: Path) -> None:
    facade, _, server, principal = _build_server(tmp_path)

    await server.call_tool(
        "vibrant.add_task",
        principal=principal,
        task_id="task-1",
        title="Wire the control plane",
        acceptance_criteria=["control plane exists"],
    )
    await server.call_tool(
        "vibrant.request_user_decision",
        principal=principal,
        text="Approve the refactor order",
    )
    consensus = await server.call_tool(
        "vibrant.update_consensus",
        principal=principal,
        context="## Objectives\nRefactor the orchestrator.\n",
    )
    task = await server.read_resource("vibrant.get_task", principal=principal, task_id="task-1")
    questions = await server.read_resource("vibrant.list_pending_questions", principal=principal)
    workflow_session = await server.read_resource("vibrant.get_workflow_session", principal=principal)
    gatekeeper_session = await server.read_resource("vibrant.get_gatekeeper_session", principal=principal)

    assert consensus["context"].startswith("## Objectives")
    assert task["id"] == "task-1"
    assert questions[0]["text"] == "Approve the refactor order"
    assert "source_turn_id" not in questions[0]
    assert "source_agent_id" not in questions[0]
    assert workflow_session["status"] == "init"
    assert gatekeeper_session["lifecycle_state"] in {"not_started", "idle"}
    assert facade.get_task("task-1") is not None


@pytest.mark.asyncio
async def test_mcp_server_exposes_attempt_execution_without_breaking_active_attempt_shape(tmp_path: Path) -> None:
    facade, orchestrator, server, principal = _build_server(tmp_path)

    await server.call_tool(
        "vibrant.add_task",
        principal=principal,
        task_id="task-1",
        title="Recover an active attempt",
        acceptance_criteria=["attempt can be inspected"],
    )
    workspace = orchestrator._workspace_service.prepare_task_workspace("task-1")
    attempt = orchestrator._attempt_store.create(
        task_id="task-1",
        task_definition_version=1,
        workspace_id=workspace.workspace_id,
        status=AttemptStatus.RUNNING,
        code_run_id="run-task-1",
        conversation_id="attempt-conv-1",
    )
    orchestrator._conversation_stream.bind_run(
        conversation_id="attempt-conv-1",
        run_id="run-task-1",
    )
    orchestrator._conversation_stream.record_host_message(
        conversation_id="attempt-conv-1",
        role="system",
        text="Attempt resumed for inspection.",
    )

    attempts = await server.read_resource("vibrant.list_active_attempts", principal=principal)
    attempt_execution = await server.read_resource(
        "vibrant.get_attempt_execution",
        principal=principal,
        attempt_id=attempt.attempt_id,
    )
    conversation = await server.read_resource(
        "vibrant.get_conversation",
        principal=principal,
        conversation_id="attempt-conv-1",
    )

    assert attempts[0]["attempt_id"] == attempt.attempt_id
    assert attempts[0]["code_run_id"] == "run-task-1"
    assert "run_id" not in attempts[0]
    assert attempt_execution["attempt_id"] == attempt.attempt_id
    assert attempt_execution["run_id"] == "run-task-1"
    assert "workspace_path" not in attempt_execution
    assert "provider_thread_path" not in attempt_execution
    assert "provider_resume_cursor" not in attempt_execution
    assert conversation["conversation_id"] == "attempt-conv-1"
    assert conversation["run_ids"] == ["run-task-1"]


@pytest.mark.asyncio
async def test_mcp_surface_keeps_only_name_level_update_roadmap_alias(tmp_path: Path) -> None:
    facade, _, server, principal = _build_server(tmp_path)

    roadmap = await server.call_tool(
        "vibrant.update_roadmap",
        principal=principal,
        tasks=[
            {
                "id": "task-1",
                "title": "Replace legacy orchestrator",
                "acceptance_criteria": ["legacy package deleted"],
            }
        ],
    )
    questions = await server.call_tool(
        "vibrant.request_user_decision",
        principal=principal,
        text="Need approval for runtime API",
    )

    assert roadmap["tasks"][0]["id"] == "task-1"
    assert questions["text"] == "Need approval for runtime API"
    assert "vibrant.update_roadmap" in server.tool_definitions
    assert "vibrant.set_pending_questions" not in server.tool_definitions
    assert "vibrant.review_task_outcome" not in server.tool_definitions
    assert "vibrant.mark_task_for_retry" not in server.tool_definitions
    assert not hasattr(facade, "set_pending_questions")
