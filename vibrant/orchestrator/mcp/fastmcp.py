"""FastMCP adapter for the orchestrator MCP surface."""

from __future__ import annotations

import json
from typing import Any

from vibrant.mcp import MCPAuthorizationError, MCPServerSettings, read_bearer_token

from .server import OrchestratorMCPServer

try:  # pragma: no cover - optional dependency at runtime
    from fastmcp import FastMCP
except ModuleNotFoundError:  # pragma: no cover - optional dependency at runtime
    FastMCP = Any  # type: ignore[assignment]


class _BearerTokenProtectedASGIApp:
    """ASGI wrapper that enforces a static bearer token for HTTP MCP requests."""

    def __init__(self, app: Any, *, settings: MCPServerSettings) -> None:
        self.app = app
        self.settings = settings
        self.routes = getattr(app, "routes", ())

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        try:
            read_bearer_token(bearer_token_env_var=self.settings.bearer_token_env_var)
        except MCPAuthorizationError as exc:
            await _send_json_response(
                send,
                status_code=500,
                payload={
                    "error": "server_error",
                    "error_description": str(exc),
                },
            )
            return

        authorization_header = _header_value(scope, "authorization")
        try:
            self.settings.require_authorization(authorization_header)
        except MCPAuthorizationError as exc:
            await _send_json_response(
                send,
                status_code=401,
                payload={
                    "error": "unauthorized",
                    "error_description": str(exc),
                },
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return

        await self.app(scope, receive, send)


def create_orchestrator_fastmcp(
    registry: OrchestratorMCPServer,
    *,
    name: str = "Vibrant Orchestrator",
    instructions: str | None = None,
) -> FastMCP:
    """Register the orchestrator MCP surface with FastMCP."""

    if FastMCP is Any:  # pragma: no cover - optional dependency at runtime
        raise ModuleNotFoundError(
            "FastMCP is not installed. Install the optional server dependencies, for example: uv add --optional mcp 'fastmcp>=3.0'"
        )

    server = FastMCP(name=name, instructions=instructions)

    @server.resource(
        "vibrant://consensus/current",
        name="consensus.current",
        description=_resource_description(registry, "consensus.current"),
        mime_type="application/json",
    )
    async def consensus_current() -> str:
        return _resource_body(await registry.read_resource("consensus.current"))

    @server.resource(
        "vibrant://roadmap/current",
        name="roadmap.current",
        description=_resource_description(registry, "roadmap.current"),
        mime_type="application/json",
    )
    async def roadmap_current() -> str:
        return _resource_body(await registry.read_resource("roadmap.current"))

    @server.resource(
        "vibrant://workflow/status",
        name="workflow.status",
        description=_resource_description(registry, "workflow.status"),
        mime_type="application/json",
    )
    async def workflow_status() -> str:
        return _resource_body(await registry.read_resource("workflow.status"))

    @server.resource(
        "vibrant://questions/pending",
        name="questions.pending",
        description=_resource_description(registry, "questions.pending"),
        mime_type="application/json",
    )
    async def questions_pending() -> str:
        return _resource_body(await registry.read_resource("questions.pending"))

    @server.resource(
        "vibrant://roles",
        name="role.list",
        description=_resource_description(registry, "role.list"),
        mime_type="application/json",
    )
    async def role_list_resource() -> str:
        return _resource_body(await registry.read_resource("role.list"))

    @server.resource(
        "vibrant://task/{task_id}",
        name="task.by_id",
        description=_resource_description(registry, "task.by_id"),
        mime_type="application/json",
    )
    async def task_by_id(task_id: str) -> str:
        return _resource_body(await registry.read_resource("task.by_id", task_id=task_id))

    @server.resource(
        "vibrant://task/{task_id}/instances",
        name="task.instances",
        description=_resource_description(registry, "task.instances"),
        mime_type="application/json",
    )
    async def task_instances(task_id: str) -> str:
        return _resource_body(await registry.read_resource("task.instances", task_id=task_id))

    @server.resource(
        "vibrant://instance/{agent_id}",
        name="instance.by_id",
        description=_resource_description(registry, "instance.by_id"),
        mime_type="application/json",
    )
    async def instance_by_id(agent_id: str) -> str:
        return _resource_body(await registry.read_resource("instance.by_id", agent_id=agent_id))

    @server.resource(
        "vibrant://events/recent/{task_id}{?limit}",
        name="events.recent",
        description=_resource_description(registry, "events.recent"),
        mime_type="application/json",
    )
    async def events_recent(task_id: str, limit: int = 20) -> str:
        return _resource_body(await registry.read_resource("events.recent", task_id=task_id, limit=limit))

    @server.tool(
        name="role_get",
        description=_tool_description(registry, "role_get"),
    )
    async def role_get(role: str) -> dict[str, Any]:
        return await registry.call_tool("role_get", role=role)

    @server.tool(
        name="role_list",
        description=_tool_description(registry, "role_list"),
    )
    async def role_list() -> list[dict[str, Any]]:
        return await registry.call_tool("role_list")

    @server.tool(
        name="instance_get",
        description=_tool_description(registry, "instance_get"),
    )
    async def instance_get(agent_id: str) -> dict[str, Any]:
        return await registry.call_tool("instance_get", agent_id=agent_id)

    @server.tool(
        name="instance_list",
        description=_tool_description(registry, "instance_list"),
    )
    async def instance_list(
        task_id: str | None = None,
        role: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await registry.call_tool(
            "instance_list",
            task_id=task_id,
            role=role,
            include_completed=include_completed,
            active_only=active_only,
        )

    @server.tool(
        name="run_get",
        description=_tool_description(registry, "run_get"),
    )
    async def run_get(run_id: str) -> dict[str, Any]:
        return await registry.call_tool("run_get", run_id=run_id)

    @server.tool(
        name="run_list",
        description=_tool_description(registry, "run_list"),
    )
    async def run_list(
        task_id: str | None = None,
        agent_id: str | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return await registry.call_tool(
            "run_list",
            task_id=task_id,
            agent_id=agent_id,
            role=role,
        )

    @server.tool(
        name="instance_respond_to_request",
        description=_tool_description(registry, "instance_respond_to_request"),
    )
    async def instance_respond_to_request(
        agent_id: str,
        request_id: int | str,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "instance_respond_to_request",
            agent_id=agent_id,
            request_id=request_id,
            result=result,
            error=error,
        )

    @server.tool(
        name="instance_wait",
        description=_tool_description(registry, "instance_wait"),
    )
    async def instance_wait(agent_id: str, release_terminal: bool = True) -> dict[str, Any]:
        return await registry.call_tool(
            "instance_wait",
            agent_id=agent_id,
            release_terminal=release_terminal,
        )

    @server.tool(
        name="consensus_get",
        description=_tool_description(registry, "consensus_get"),
    )
    async def consensus_get() -> dict[str, Any] | None:
        return await registry.call_tool("consensus_get")

    @server.tool(
        name="consensus_update",
        description=_tool_description(registry, "consensus_update"),
    )
    async def consensus_update(
        status: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "consensus_update",
            status=status,
            context=context,
        )

    @server.tool(
        name="question_ask_user",
        description=_tool_description(registry, "question_ask_user"),
    )
    async def question_ask_user(
        text: str,
        source_agent_id: str | None = None,
        priority: str = "blocking",
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "question_ask_user",
            text=text,
            source_agent_id=source_agent_id,
            priority=priority,
        )

    @server.tool(
        name="question_resolve",
        description=_tool_description(registry, "question_resolve"),
    )
    async def question_resolve(question_id: str, answer: str | None = None) -> dict[str, Any]:
        return await registry.call_tool(
            "question_resolve",
            question_id=question_id,
            answer=answer,
        )

    @server.tool(
        name="roadmap_add_task",
        description=_tool_description(registry, "roadmap_add_task"),
    )
    async def roadmap_add_task(task: dict[str, Any], index: int | None = None) -> dict[str, Any]:
        return await registry.call_tool(
            "roadmap_add_task",
            task=task,
            index=index,
        )

    @server.tool(
        name="roadmap_get",
        description=_tool_description(registry, "roadmap_get"),
    )
    async def roadmap_get() -> dict[str, Any] | None:
        return await registry.call_tool("roadmap_get")

    @server.tool(
        name="roadmap_reorder_tasks",
        description=_tool_description(registry, "roadmap_reorder_tasks"),
    )
    async def roadmap_reorder_tasks(ordered_task_ids: list[str]) -> dict[str, Any]:
        return await registry.call_tool(
            "roadmap_reorder_tasks",
            ordered_task_ids=ordered_task_ids,
        )

    @server.tool(
        name="roadmap_update_task",
        description=_tool_description(registry, "roadmap_update_task"),
    )
    async def roadmap_update_task(
        task_id: str,
        title: str | None = None,
        acceptance_criteria: list[str] | None = None,
        status: str | None = None,
        branch: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        dependencies: list[str] | None = None,
        priority: int | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "roadmap_update_task",
            task_id=task_id,
            title=title,
            acceptance_criteria=acceptance_criteria,
            status=status,
            branch=branch,
            retry_count=retry_count,
            max_retries=max_retries,
            prompt=prompt,
            skills=skills,
            dependencies=dependencies,
            priority=priority,
            failure_reason=failure_reason,
        )

    @server.tool(
        name="task_get",
        description=_tool_description(registry, "task_get"),
    )
    async def task_get(task_id: str) -> dict[str, Any]:
        return await registry.call_tool("task_get", task_id=task_id)

    @server.tool(
        name="workflow_execute_next_task",
        description=_tool_description(registry, "workflow_execute_next_task"),
    )
    async def workflow_execute_next_task() -> dict[str, Any] | None:
        return await registry.call_tool("workflow_execute_next_task")

    @server.tool(
        name="workflow_pause",
        description=_tool_description(registry, "workflow_pause"),
    )
    async def workflow_pause() -> dict[str, Any]:
        return await registry.call_tool("workflow_pause")

    @server.tool(
        name="workflow_resume",
        description=_tool_description(registry, "workflow_resume"),
    )
    async def workflow_resume() -> dict[str, Any]:
        return await registry.call_tool("workflow_resume")

    @server.tool(
        name="vibrant.end_planning_phase",
        description=_tool_description(registry, "vibrant.end_planning_phase"),
    )
    async def end_planning_phase() -> dict[str, Any]:
        return await registry.call_tool("vibrant.end_planning_phase")

    @server.tool(
        name="vibrant.request_user_decision",
        description=_tool_description(registry, "vibrant.request_user_decision"),
    )
    async def request_user_decision(
        question: str,
        source_agent_id: str | None = None,
        priority: str = "blocking",
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.request_user_decision",
            question=question,
            source_agent_id=source_agent_id,
            priority=priority,
        )

    @server.tool(
        name="vibrant.set_pending_questions",
        description=_tool_description(registry, "vibrant.set_pending_questions"),
    )
    async def set_pending_questions(
        questions: list[str],
        source_agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await registry.call_tool(
            "vibrant.set_pending_questions",
            questions=questions,
            source_agent_id=source_agent_id,
        )

    @server.tool(
        name="vibrant.review_task_outcome",
        description=_tool_description(registry, "vibrant.review_task_outcome"),
    )
    async def review_task_outcome(
        task_id: str,
        decision: str,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.review_task_outcome",
            task_id=task_id,
            decision=decision,
            failure_reason=failure_reason,
        )

    @server.tool(
        name="vibrant.mark_task_for_retry",
        description=_tool_description(registry, "vibrant.mark_task_for_retry"),
    )
    async def mark_task_for_retry(
        task_id: str,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.mark_task_for_retry",
            task_id=task_id,
            failure_reason=failure_reason,
            prompt=prompt,
            acceptance_criteria=acceptance_criteria,
        )

    @server.tool(
        name="vibrant.update_consensus",
        description=_tool_description(registry, "vibrant.update_consensus"),
    )
    async def update_consensus(
        status: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.update_consensus",
            status=status,
            context=context,
        )

    @server.tool(
        name="vibrant.update_roadmap",
        description=_tool_description(registry, "vibrant.update_roadmap"),
    )
    async def update_roadmap(tasks: list[dict[str, Any]], project: str | None = None) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.update_roadmap",
            tasks=tasks,
            project=project,
        )

    return server


def create_orchestrator_fastmcp_app(
    registry: OrchestratorMCPServer,
    *,
    settings: MCPServerSettings,
    mcp_path: str = "/mcp",
    name: str = "Vibrant Orchestrator",
    instructions: str | None = None,
    transport: str = "http",
) -> Any:
    """Expose the FastMCP server as an HTTP ASGI app protected by a bearer token."""

    server = create_orchestrator_fastmcp(
        registry,
        name=name,
        instructions=instructions,
    )
    app = server.http_app(path=mcp_path, transport=transport)
    if transport == "stdio":
        return app
    return _BearerTokenProtectedASGIApp(app, settings=settings)


def _resource_description(registry: OrchestratorMCPServer, name: str) -> str:
    definition = registry.get_resource_definition(name)
    if definition is None:
        raise KeyError(f"Unknown orchestrator MCP resource: {name}")
    return definition.description


def _tool_description(registry: OrchestratorMCPServer, name: str) -> str:
    definition = registry.get_tool_definition(name)
    if definition is None:
        raise KeyError(f"Unknown orchestrator MCP tool: {name}")
    return definition.description


def _resource_body(payload: Any) -> str:
    return json.dumps(payload)


def _header_value(scope: dict[str, Any], header_name: str) -> str | None:
    expected = header_name.encode("latin-1").lower()
    for name, value in scope.get("headers", []):
        if bytes(name).lower() == expected:
            return bytes(value).decode("latin-1")
    return None


async def _send_json_response(
    send: Any,
    *,
    status_code: int,
    payload: dict[str, Any],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        }
    )


__all__ = [
    "create_orchestrator_fastmcp",
    "create_orchestrator_fastmcp_app",
]
