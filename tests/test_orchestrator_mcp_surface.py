from __future__ import annotations

from pathlib import Path

import pytest

from vibrant.orchestrator import OrchestratorFacade, create_orchestrator
from vibrant.project_init import initialize_project


def _build_server(tmp_path: Path):
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    assert orchestrator.mcp_server is not None
    return facade, orchestrator.mcp_server, orchestrator.mcp_server.gatekeeper_principal()


@pytest.mark.asyncio
async def test_mcp_server_supports_semantic_tools_and_resources(tmp_path: Path) -> None:
    facade, server, principal = _build_server(tmp_path)

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

    assert consensus["context"].startswith("## Objectives")
    assert task["id"] == "task-1"
    assert questions[0]["text"] == "Approve the refactor order"
    assert facade.get_task("task-1") is not None


@pytest.mark.asyncio
async def test_mcp_compatibility_aliases_delegate_to_new_backend(tmp_path: Path) -> None:
    _, server, principal = _build_server(tmp_path)

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
        "vibrant.set_pending_questions",
        principal=principal,
        questions=["Need approval for runtime API"],
    )

    assert roadmap["tasks"][0]["id"] == "task-1"
    assert questions[0]["text"] == "Need approval for runtime API"
