"""Framework-agnostic MCP server registry for orchestrator resources and tools."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from vibrant.mcp.authz import (
    MCP_ACCESS_SCOPE,
    MCPPrincipal,
    ORCHESTRATOR_CONSENSUS_READ_SCOPE,
    ORCHESTRATOR_CONSENSUS_WRITE_SCOPE,
    ORCHESTRATOR_QUESTIONS_READ_SCOPE,
    ORCHESTRATOR_QUESTIONS_WRITE_SCOPE,
    ORCHESTRATOR_WORKFLOW_READ_SCOPE,
    ORCHESTRATOR_WORKFLOW_WRITE_SCOPE,
    TASKS_READ_SCOPE,
    TASKS_RUN_SCOPE,
    TASKS_WRITE_SCOPE,
    ensure_scopes,
    has_scopes,
)
from vibrant.orchestrator.facade import OrchestratorFacade

from .resources import ResourceHandlers
from .tools_agents import AgentToolHandlers
from .tools_gatekeeper import GatekeeperToolHandlers

ToolHandler = Callable[..., dict[str, Any] | list[dict[str, Any]] | Awaitable[Any] | None]
ResourceHandler = Callable[..., dict[str, Any] | list[dict[str, Any]] | Awaitable[Any] | None]

_RESOURCE_SCOPES: dict[str, tuple[str, ...]] = {
    "agent.status": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "consensus.current": (MCP_ACCESS_SCOPE, ORCHESTRATOR_CONSENSUS_READ_SCOPE),
    "events.recent": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "questions.pending": (MCP_ACCESS_SCOPE, ORCHESTRATOR_QUESTIONS_READ_SCOPE),
    "roadmap.current": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "task.assigned": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "task.by_id": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "workflow.status": (MCP_ACCESS_SCOPE, ORCHESTRATOR_WORKFLOW_READ_SCOPE),
}

_TOOL_SCOPES: dict[str, tuple[str, ...]] = {
    "agent_get": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "agent_list": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "agent_result_get": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "agent_respond_to_request": (MCP_ACCESS_SCOPE, TASKS_RUN_SCOPE),
    "agent_wait": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "consensus_get": (MCP_ACCESS_SCOPE, ORCHESTRATOR_CONSENSUS_READ_SCOPE),
    "consensus_update": (MCP_ACCESS_SCOPE, ORCHESTRATOR_CONSENSUS_WRITE_SCOPE),
    "question_ask_user": (MCP_ACCESS_SCOPE, ORCHESTRATOR_QUESTIONS_WRITE_SCOPE),
    "question_resolve": (MCP_ACCESS_SCOPE, ORCHESTRATOR_QUESTIONS_WRITE_SCOPE),
    "roadmap_add_task": (MCP_ACCESS_SCOPE, TASKS_WRITE_SCOPE),
    "roadmap_get": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "roadmap_reorder_tasks": (MCP_ACCESS_SCOPE, TASKS_WRITE_SCOPE),
    "roadmap_update_task": (MCP_ACCESS_SCOPE, TASKS_WRITE_SCOPE),
    "task_get": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
    "workflow_execute_next_task": (MCP_ACCESS_SCOPE, TASKS_RUN_SCOPE),
    "workflow_pause": (MCP_ACCESS_SCOPE, ORCHESTRATOR_WORKFLOW_WRITE_SCOPE),
    "workflow_resume": (MCP_ACCESS_SCOPE, ORCHESTRATOR_WORKFLOW_WRITE_SCOPE),
    "vibrant.end_planning_phase": (MCP_ACCESS_SCOPE, ORCHESTRATOR_WORKFLOW_WRITE_SCOPE),
    "vibrant.request_user_decision": (MCP_ACCESS_SCOPE, ORCHESTRATOR_QUESTIONS_WRITE_SCOPE),
    "vibrant.set_pending_questions": (MCP_ACCESS_SCOPE, ORCHESTRATOR_QUESTIONS_WRITE_SCOPE),
    "vibrant.review_task_outcome": (MCP_ACCESS_SCOPE, TASKS_WRITE_SCOPE),
    "vibrant.mark_task_for_retry": (MCP_ACCESS_SCOPE, TASKS_WRITE_SCOPE),
    "vibrant.update_consensus": (MCP_ACCESS_SCOPE, ORCHESTRATOR_CONSENSUS_WRITE_SCOPE),
    "vibrant.update_roadmap": (MCP_ACCESS_SCOPE, TASKS_WRITE_SCOPE),
}


@dataclass(frozen=True, slots=True)
class MCPResourceDefinition:
    """Metadata for one orchestrator MCP resource."""

    name: str
    description: str
    handler: ResourceHandler


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    """Metadata for one orchestrator MCP tool."""

    name: str
    description: str
    handler: ToolHandler


class OrchestratorMCPServer:
    """Small in-process MCP surface over the orchestrator facade."""

    def __init__(self, facade: OrchestratorFacade) -> None:
        self.facade = facade
        self.resources = ResourceHandlers(facade)
        self.gatekeeper_tools = GatekeeperToolHandlers(facade)
        self.agent_tools = AgentToolHandlers(facade)

        self._resources: dict[str, MCPResourceDefinition] = {
            "agent.status": MCPResourceDefinition(
                name="agent.status",
                description="Read one agent snapshot or list agent snapshots.",
                handler=self.resources.agent_status,
            ),
            "consensus.current": MCPResourceDefinition(
                name="consensus.current",
                description="Read the current consensus document.",
                handler=self.resources.consensus_current,
            ),
            "events.recent": MCPResourceDefinition(
                name="events.recent",
                description="Read recent orchestrator canonical events.",
                handler=self.resources.events_recent,
            ),
            "questions.pending": MCPResourceDefinition(
                name="questions.pending",
                description="Read unresolved user-facing orchestrator questions.",
                handler=self.resources.questions_pending,
            ),
            "roadmap.current": MCPResourceDefinition(
                name="roadmap.current",
                description="Read the current roadmap document.",
                handler=self.resources.roadmap_current,
            ),
            "task.by_id": MCPResourceDefinition(
                name="task.by_id",
                description="Read one roadmap task by id.",
                handler=self.resources.task_by_id,
            ),
            "task.assigned": MCPResourceDefinition(
                name="task.assigned",
                description="Read a task together with its related agents.",
                handler=self.resources.task_assigned,
            ),
            "workflow.status": MCPResourceDefinition(
                name="workflow.status",
                description="Read the current orchestrator workflow status.",
                handler=self.resources.workflow_status,
            ),
        }
        self._tools: dict[str, MCPToolDefinition] = {
            "agent_get": MCPToolDefinition("agent_get", "Read one agent snapshot by id.", self.agent_tools.agent_get),
            "agent_list": MCPToolDefinition("agent_list", "List orchestrator agent snapshots.", self.agent_tools.agent_list),
            "agent_result_get": MCPToolDefinition("agent_result_get", "Read the latest known result for one agent.", self.agent_tools.agent_result_get),
            "agent_respond_to_request": MCPToolDefinition("agent_respond_to_request", "Answer a pending provider request for an existing agent.", self.agent_tools.agent_respond_to_request),
            "agent_wait": MCPToolDefinition("agent_wait", "Wait for an existing agent to reach a result state.", self.agent_tools.agent_wait),
            "consensus_get": MCPToolDefinition("consensus_get", "Read the current consensus document.", self.gatekeeper_tools.consensus_get),
            "consensus_update": MCPToolDefinition("consensus_update", "Update orchestrator-owned consensus fields.", self.gatekeeper_tools.consensus_update),
            "question_ask_user": MCPToolDefinition("question_ask_user", "Create a structured user-facing question.", self.gatekeeper_tools.question_ask_user),
            "question_resolve": MCPToolDefinition("question_resolve", "Resolve a structured question record.", self.gatekeeper_tools.question_resolve),
            "roadmap_add_task": MCPToolDefinition("roadmap_add_task", "Add a task to the roadmap.", self.gatekeeper_tools.roadmap_add_task),
            "roadmap_get": MCPToolDefinition("roadmap_get", "Read the current roadmap document.", self.gatekeeper_tools.roadmap_get),
            "roadmap_reorder_tasks": MCPToolDefinition("roadmap_reorder_tasks", "Reorder roadmap tasks by id.", self.gatekeeper_tools.roadmap_reorder_tasks),
            "roadmap_update_task": MCPToolDefinition("roadmap_update_task", "Update a roadmap task definition.", self.gatekeeper_tools.roadmap_update_task),
            "task_get": MCPToolDefinition("task_get", "Read one roadmap task by id.", self.agent_tools.task_get),
            "workflow_execute_next_task": MCPToolDefinition("workflow_execute_next_task", "Dispatch and execute the next roadmap task according to orchestrator workflow rules.", self.agent_tools.workflow_execute_next_task),
            "workflow_pause": MCPToolDefinition("workflow_pause", "Pause the workflow.", self.gatekeeper_tools.workflow_pause),
            "workflow_resume": MCPToolDefinition("workflow_resume", "Resume the workflow.", self.gatekeeper_tools.workflow_resume),
            "vibrant.end_planning_phase": MCPToolDefinition("vibrant.end_planning_phase", "Transition the orchestrator from planning into execution.", self.gatekeeper_tools.end_planning_phase),
            "vibrant.request_user_decision": MCPToolDefinition("vibrant.request_user_decision", "Create one user-facing decision request for the Gatekeeper.", self.gatekeeper_tools.request_user_decision),
            "vibrant.set_pending_questions": MCPToolDefinition("vibrant.set_pending_questions", "Replace the pending Gatekeeper question set.", self.gatekeeper_tools.set_pending_questions),
            "vibrant.review_task_outcome": MCPToolDefinition("vibrant.review_task_outcome", "Record the Gatekeeper verdict for a task outcome.", self.gatekeeper_tools.review_task_outcome),
            "vibrant.mark_task_for_retry": MCPToolDefinition("vibrant.mark_task_for_retry", "Update a task for retry and requeue or escalate it.", self.gatekeeper_tools.mark_task_for_retry),
            "vibrant.update_consensus": MCPToolDefinition("vibrant.update_consensus", "Update orchestrator-owned consensus fields.", self.gatekeeper_tools.update_consensus),
            "vibrant.update_roadmap": MCPToolDefinition("vibrant.update_roadmap", "Replace the roadmap document with a validated task list.", self.gatekeeper_tools.update_roadmap),
        }

    def list_resources(self, principal: MCPPrincipal) -> list[MCPResourceDefinition]:
        return [definition for name, definition in self._resources.items() if has_scopes(principal.scopes, _RESOURCE_SCOPES[name])]

    def list_tools(self, principal: MCPPrincipal) -> list[MCPToolDefinition]:
        return [definition for name, definition in self._tools.items() if has_scopes(principal.scopes, _TOOL_SCOPES[name])]

    async def read_resource(self, resource_name: str, *, principal: MCPPrincipal, **params: Any) -> Any:
        definition = self._resources.get(resource_name)
        if definition is None:
            raise KeyError(f"Unknown orchestrator MCP resource: {resource_name}")
        ensure_scopes(principal.scopes, _RESOURCE_SCOPES[resource_name], action=f"read resource {resource_name}")
        result = definition.handler(**params)
        if inspect.isawaitable(result):
            return await result
        return result

    async def call_tool(self, tool_name: str, *, principal: MCPPrincipal, **params: Any) -> Any:
        definition = self._tools.get(tool_name)
        if definition is None:
            raise KeyError(f"Unknown orchestrator MCP tool: {tool_name}")
        ensure_scopes(principal.scopes, _TOOL_SCOPES[tool_name], action=f"call tool {tool_name}")
        result = definition.handler(**params)
        if inspect.isawaitable(result):
            return await result
        return result
