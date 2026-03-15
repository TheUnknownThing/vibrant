from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from vibrant.agents.gatekeeper import GatekeeperRequest, GatekeeperTrigger
from vibrant.models.task import TaskInfo

from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.mcp import BINDING_HEADER_NAME
from vibrant.orchestrator.policy.shared.capabilities import gatekeeper_binding_preset, worker_binding_preset
from vibrant.orchestrator.policy.task_loop.models import DispatchLease, PreparedTaskExecution
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


def _prepare_orchestrator(tmp_path: Path):
    initialize_project(tmp_path)
    _initialize_git_repo(tmp_path)
    return create_orchestrator(tmp_path)


@pytest.mark.asyncio
async def test_fastmcp_host_exposes_gatekeeper_surface_over_loopback_http(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    try:
        workspace = orchestrator.workspace_service.prepare_task_workspace("task-1")
        attempt = orchestrator.attempt_store.create(
            task_id="task-1",
            task_definition_version=1,
            workspace_id=workspace.workspace_id,
            status=AttemptStatus.RUNNING,
            code_run_id="run-task-1",
            conversation_id="attempt-conv-1",
        )
        orchestrator.conversation_stream.bind_run(
            conversation_id="attempt-conv-1",
            run_id="run-task-1",
        )
        orchestrator.conversation_stream.record_host_message(
            conversation_id="attempt-conv-1",
            role="system",
            text="Attempt resumed for transport inspection.",
        )
        orchestrator.mcp_host.transport.port = 8765
        app = orchestrator.mcp_host.http_app()
        bound = orchestrator.binding_service.bind_preset(
            preset=gatekeeper_binding_preset(orchestrator.mcp_server, "gatekeeper-test"),
            run_id="gatekeeper-test",
            conversation_id="gatekeeper-test",
        )
        orchestrator.mcp_host.register_binding(bound)

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
                headers={BINDING_HEADER_NAME: bound.access.binding_id},
            ) as http_client:
                async with streamable_http_client(
                    "http://127.0.0.1/mcp",
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        resources = await session.list_resources()
                        templates = await session.list_resource_templates()

                        assert "vibrant.add_task" in {tool.name for tool in tools.tools}
                        assert "vibrant.update_consensus" in {tool.name for tool in tools.tools}
                        assert "vibrant.get_consensus" in {resource.name for resource in resources.resources}
                        assert "vibrant.get_workflow_session" in {resource.name for resource in resources.resources}
                        assert "vibrant.get_gatekeeper_session" in {resource.name for resource in resources.resources}
                        assert "vibrant.get_task" in {template.name for template in templates.resourceTemplates}
                        assert "vibrant.get_attempt_execution" in {template.name for template in templates.resourceTemplates}
                        assert "vibrant.get_conversation" in {template.name for template in templates.resourceTemplates}

                        add_task_result = await session.call_tool(
                            "vibrant.add_task",
                            {
                                "task_id": "task-1",
                                "title": "Wire the loopback host",
                                "acceptance_criteria": ["host is reachable"],
                            },
                        )
                        task_result = await session.read_resource("vibrant://tasks/task-1")
                        workflow_session_result = await session.read_resource("vibrant://workflow-session")
                        gatekeeper_session_result = await session.read_resource("vibrant://gatekeeper-session")
                        active_attempts_result = await session.read_resource("vibrant://active-attempts")
                        attempt_execution_result = await session.read_resource(
                            f"vibrant://attempts/{attempt.attempt_id}"
                        )
                        conversation_result = await session.read_resource("vibrant://conversations/attempt-conv-1")

        created_task = json.loads(add_task_result.content[0].text)
        resolved_task = json.loads(task_result.contents[0].text)
        workflow_session = json.loads(workflow_session_result.contents[0].text)
        gatekeeper_session = json.loads(gatekeeper_session_result.contents[0].text)
        active_attempts = json.loads(active_attempts_result.contents[0].text)
        attempt_execution = json.loads(attempt_execution_result.contents[0].text)
        conversation = json.loads(conversation_result.contents[0].text)
        assert created_task["id"] == "task-1"
        assert resolved_task["id"] == "task-1"
        assert workflow_session["status"] == "init"
        assert gatekeeper_session["lifecycle_state"] in {"not_started", "idle"}
        assert active_attempts[0]["attempt_id"] == attempt.attempt_id
        assert active_attempts[0]["code_run_id"] == "run-task-1"
        assert "run_id" not in active_attempts[0]
        assert attempt_execution["run_id"] == "run-task-1"
        assert "workspace_path" not in attempt_execution
        assert "provider_thread_path" not in attempt_execution
        assert "provider_resume_cursor" not in attempt_execution
        assert conversation["conversation_id"] == "attempt-conv-1"
        assert conversation["run_ids"] == ["run-task-1"]
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_fastmcp_host_filters_worker_bindings_server_side(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    try:
        orchestrator.mcp_host.transport.port = 8765
        app = orchestrator.mcp_host.http_app()
        bound = orchestrator.binding_service.bind_preset(
            preset=worker_binding_preset(orchestrator.mcp_server, "worker-1", "code"),
            run_id="task-1",
            conversation_id=None,
        )
        orchestrator.mcp_host.register_binding(bound)

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
                headers={BINDING_HEADER_NAME: bound.access.binding_id},
            ) as http_client:
                async with streamable_http_client(
                    "http://127.0.0.1/mcp",
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        resources = await session.list_resources()

                        assert tools.tools == []
                        assert "vibrant.get_consensus" in {resource.name for resource in resources.resources}
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_gatekeeper_lifecycle_compiles_and_passes_invocation_plan(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    captured: dict[str, object] = {}

    class _FakeHandle:
        async def wait(self):
            return type(
                "_Result",
                (),
                {
                    "provider_thread_id": None,
                    "awaiting_input": False,
                    "error": None,
                },
            )()

    async def fake_start_run(**kwargs):
        captured.update(kwargs)
        return _FakeHandle()

    monkeypatch.setattr(orchestrator.gatekeeper_lifecycle.runtime_service, "start_run", fake_start_run)
    async def fake_ensure_started() -> str:
        orchestrator.mcp_host.transport.port = 8765
        return "http://127.0.0.1:8765/mcp"

    monkeypatch.setattr(orchestrator.mcp_host, "ensure_started", fake_ensure_started)

    try:
        await orchestrator.gatekeeper_lifecycle.submit(
            request=GatekeeperRequest(
                trigger=GatekeeperTrigger.USER_CONVERSATION,
                trigger_description="Plan the next phase",
                agent_summary="Plan the next phase",
            ),
            submission_id="submission-1",
            resume=False,
        )
        await asyncio.sleep(0)

        invocation_plan = captured["invocation_plan"]
        assert invocation_plan is not None
        assert invocation_plan.binding_id is not None
        assert "--config" in invocation_plan.launch_args
        assert any(arg.startswith("mcp_servers.") for arg in invocation_plan.launch_args if arg != "--config")
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_execution_coordinator_compiles_and_passes_worker_invocation_plan(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    captured: dict[str, object] = {}

    class _FakeHandle:
        async def wait(self):
            return None

    async def fake_start_run(**kwargs):
        captured.update(kwargs)
        return _FakeHandle()

    monkeypatch.setattr(orchestrator.execution_coordinator.runtime_service, "start_run", fake_start_run)

    async def fake_ensure_started() -> str:
        orchestrator.mcp_host.transport.port = 8765
        return "http://127.0.0.1:8765/mcp"

    monkeypatch.setattr(orchestrator.mcp_host, "ensure_started", fake_ensure_started)

    try:
        prepared = PreparedTaskExecution(
            lease=DispatchLease(
                task_id="task-1",
                lease_id="lease-1",
                task_definition_version=1,
            ),
            task=TaskInfo(
                id="task-1",
                title="Wire worker MCP access",
                acceptance_criteria=["worker can read orchestrator MCP resources"],
            ),
            prompt="Implement the task.",
        )

        await orchestrator.execution_coordinator.start_attempt(prepared)
        await asyncio.sleep(0)

        invocation_plan = captured["invocation_plan"]
        assert invocation_plan is not None
        assert invocation_plan.binding_id is not None
        assert "--config" in invocation_plan.launch_args
        assert any(arg.startswith("mcp_servers.") for arg in invocation_plan.launch_args if arg != "--config")
    finally:
        await orchestrator.shutdown()
