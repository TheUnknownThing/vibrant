from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import RoadmapExecutionMode
from vibrant.mcp import MCPServerSettings
from vibrant.orchestrator.artifacts import ConsensusService, QuestionService, RoadmapService
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.mcp import OrchestratorMCPServer, create_orchestrator_fastmcp
from vibrant.orchestrator.mcp.fastmcp import _BearerTokenProtectedASGIApp
from vibrant.orchestrator.state import StateStore
from vibrant.orchestrator.state.backend import OrchestratorStateBackend
from vibrant.project_init import initialize_project

_FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None


class _StubGatekeeper:
    async def answer_question(self, question: str, answer: str):  # pragma: no cover - not exercised here
        raise NotImplementedError


def _build_facade(tmp_path: Path) -> OrchestratorFacade:
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
        roadmap_document=roadmap_service.document,
        consensus_service=consensus_service,
        consensus_path=repo / ".vibrant" / "consensus.md",
        question_service=question_service,
        execution_mode=RoadmapExecutionMode.MANUAL,
    )
    return OrchestratorFacade(lifecycle)


async def _exercise_http_app(
    app: object,
    *,
    authorization: str | None = None,
) -> tuple[int, dict[str, str], str]:
    response: dict[str, object] = {"headers": {}}
    body_parts: list[bytes] = []
    request_sent = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            response["status"] = int(message["status"])
            response["headers"] = {
                bytes(name).decode("latin-1"): bytes(value).decode("latin-1")
                for name, value in message.get("headers", [])
            }
            return
        if message["type"] == "http.response.body":
            body_parts.append(bytes(message.get("body", b"")))

    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("latin-1")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 9000),
    }

    await app(scope, receive, send)  # type: ignore[misc]
    return int(response["status"]), dict(response["headers"]), b"".join(body_parts).decode("utf-8")


@pytest.mark.asyncio
async def test_bearer_token_wrapper_returns_500_when_server_token_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBRANT_MCP_TOKEN", raising=False)
    settings = MCPServerSettings(
        url="http://127.0.0.1:9000/mcp",
        bearer_token_env_var="VIBRANT_MCP_TOKEN",
    )

    async def downstream(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    app = _BearerTokenProtectedASGIApp(downstream, settings=settings)
    status, _headers, body = await _exercise_http_app(app, authorization="Bearer secret-token")

    assert status == 500
    assert json.loads(body) == {
        "error": "server_error",
        "error_description": "Missing MCP bearer token in environment variable 'VIBRANT_MCP_TOKEN'",
    }


@pytest.mark.asyncio
async def test_bearer_token_wrapper_rejects_missing_or_invalid_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBRANT_MCP_TOKEN", "secret-token")
    settings = MCPServerSettings(
        url="http://127.0.0.1:9000/mcp",
        bearer_token_env_var="VIBRANT_MCP_TOKEN",
    )

    async def downstream(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    app = _BearerTokenProtectedASGIApp(downstream, settings=settings)

    missing_status, missing_headers, missing_body = await _exercise_http_app(app)
    invalid_status, invalid_headers, invalid_body = await _exercise_http_app(
        app,
        authorization="Bearer wrong-token",
    )

    assert missing_status == 401
    assert missing_headers["www-authenticate"] == "Bearer"
    assert json.loads(missing_body)["error"] == "unauthorized"

    assert invalid_status == 401
    assert invalid_headers["www-authenticate"] == "Bearer"
    assert json.loads(invalid_body) == {
        "error": "unauthorized",
        "error_description": "Invalid MCP bearer token",
    }


@pytest.mark.asyncio
async def test_bearer_token_wrapper_allows_valid_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBRANT_MCP_TOKEN", "secret-token")
    settings = MCPServerSettings(
        url="http://127.0.0.1:9000/mcp",
        bearer_token_env_var="VIBRANT_MCP_TOKEN",
    )
    called: list[str] = []

    async def downstream(_scope, _receive, send) -> None:
        called.append("downstream")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    app = _BearerTokenProtectedASGIApp(downstream, settings=settings)
    status, _headers, body = await _exercise_http_app(app, authorization="Bearer secret-token")

    assert status == 204
    assert body == ""
    assert called == ["downstream"]


@pytest.mark.asyncio
@pytest.mark.skipif(not _FASTMCP_AVAILABLE, reason="fastmcp is optional")
async def test_create_orchestrator_fastmcp_registers_tools_and_resources(tmp_path: Path) -> None:
    registry = OrchestratorMCPServer(_build_facade(tmp_path))

    server = create_orchestrator_fastmcp(registry)

    assert await server._local_provider.get_tool("roadmap_add_task") is not None
    assert await server._local_provider.get_tool("vibrant.update_roadmap") is not None
    assert await server._local_provider.get_resource("vibrant://consensus/current") is not None
    assert await server._local_provider.get_resource_template("vibrant://task/{task_id}") is not None


@pytest.mark.asyncio
@pytest.mark.skipif(not _FASTMCP_AVAILABLE, reason="fastmcp is optional")
async def test_create_orchestrator_fastmcp_binds_events_recent_limit_query_param(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OrchestratorMCPServer(_build_facade(tmp_path))
    captured: dict[str, object] = {}

    async def fake_read_resource(resource_name: str, **params):
        captured["resource_name"] = resource_name
        captured["params"] = params
        return []

    monkeypatch.setattr(registry, "read_resource", fake_read_resource)

    server = create_orchestrator_fastmcp(registry)
    template = await server._local_provider.get_resource_template(
        "vibrant://events/recent/task-1?limit=5"
    )

    assert template is not None
    params = template.matches("vibrant://events/recent/task-1?limit=5")
    assert params == {"task_id": "task-1", "limit": "5"}

    await template._read("vibrant://events/recent/task-1?limit=5", params)

    assert captured["resource_name"] == "events.recent"
    assert captured["params"] == {"task_id": "task-1", "limit": 5}


@pytest.mark.asyncio
@pytest.mark.skipif(not _FASTMCP_AVAILABLE, reason="fastmcp is optional")
async def test_create_orchestrator_fastmcp_uses_explicit_roadmap_update_fields(tmp_path: Path) -> None:
    registry = OrchestratorMCPServer(_build_facade(tmp_path))
    server = create_orchestrator_fastmcp(registry)
    add_tool = await server._local_provider.get_tool("roadmap_add_task")
    update_tool = await server._local_provider.get_tool("roadmap_update_task")

    await add_tool.run(
        {
            "task": {
                "id": "task-1",
                "title": "Initial title",
                "acceptance_criteria": ["One criterion"],
            }
        }
    )
    updated = await update_tool.run(
        {
            "task_id": "task-1",
            "title": "Updated title",
            "priority": 7,
        }
    )
    payload = getattr(updated, "structured_content", updated)

    assert payload["title"] == "Updated title"
    assert payload["priority"] == 7
