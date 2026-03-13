from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.mcp import BINDING_HEADER_NAME
from vibrant.orchestrator.types import GatekeeperMessageKind
from vibrant.project_init import initialize_project


def _prepare_orchestrator(tmp_path: Path):
    initialize_project(tmp_path)
    return create_orchestrator(tmp_path)


@pytest.mark.asyncio
async def test_fastmcp_host_exposes_gatekeeper_surface_over_loopback_http(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    try:
        orchestrator.mcp_host.transport.port = 8765
        orchestrator.mcp_host.fastmcp.settings.port = 8765
        app = orchestrator.mcp_host.fastmcp.streamable_http_app()
        bound = orchestrator.binding_service.bind_gatekeeper(
            session_id="gatekeeper-test",
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
                        assert "vibrant.get_task" in {template.name for template in templates.resourceTemplates}

                        add_task_result = await session.call_tool(
                            "vibrant.add_task",
                            {
                                "task_id": "task-1",
                                "title": "Wire the loopback host",
                                "acceptance_criteria": ["host is reachable"],
                            },
                        )
                        task_result = await session.read_resource("vibrant://tasks/task-1")

        created_task = json.loads(add_task_result.content[0].text)
        resolved_task = json.loads(task_result.contents[0].text)
        assert created_task["id"] == "task-1"
        assert resolved_task["id"] == "task-1"
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_fastmcp_host_filters_worker_bindings_server_side(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    try:
        orchestrator.mcp_host.transport.port = 8765
        orchestrator.mcp_host.fastmcp.settings.port = 8765
        app = orchestrator.mcp_host.fastmcp.streamable_http_app()
        bound = orchestrator.binding_service.bind_worker(
            agent_id="worker-1",
            task_id="task-1",
            agent_type="code",
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
        orchestrator.mcp_host.fastmcp.settings.port = 8765
        return "http://127.0.0.1:8765/mcp"

    monkeypatch.setattr(orchestrator.mcp_host, "ensure_started", fake_ensure_started)

    try:
        await orchestrator.gatekeeper_lifecycle.submit(
            message_kind=GatekeeperMessageKind.USER_MESSAGE,
            text="Plan the next phase",
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
