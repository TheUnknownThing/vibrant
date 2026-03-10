"""Framework-agnostic MCP server registry for orchestrator resources and tools."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from vibrant.orchestrator.facade import OrchestratorFacade

from .authz import (
    MCPPrincipal,
    OrchestratorMCPRole,
    RolePolicy,
    default_role_policies,
    ensure_resource_allowed,
    ensure_tool_allowed,
)
from .resources import ResourceHandlers
from .tools_agents import AgentToolHandlers
from .tools_gatekeeper import GatekeeperToolHandlers

ToolHandler = Callable[..., dict[str, Any] | list[dict[str, Any]] | Awaitable[Any] | None]
ResourceHandler = Callable[..., dict[str, Any] | list[dict[str, Any]] | Awaitable[Any] | None]


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
    """Small in-process MCP surface over the orchestrator facade.

    This stays framework-agnostic so the transport layer can evolve independently
    from the typed orchestrator control-plane contract.
    """

    def __init__(
        self,
        facade: OrchestratorFacade,
        *,
        policies: dict[OrchestratorMCPRole, RolePolicy] | None = None,
    ) -> None:
        self.facade = facade
        self.policies = policies or default_role_policies()
        self.resources = ResourceHandlers(facade)
        self.gatekeeper_tools = GatekeeperToolHandlers(facade)
        self.agent_tools = AgentToolHandlers(facade)

        self._resources: dict[str, MCPResourceDefinition] = {
            "consensus.current": MCPResourceDefinition(
                name="consensus.current",
                description="Read the current consensus document.",
                handler=self.resources.consensus_current,
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
            "workflow.status": MCPResourceDefinition(
                name="workflow.status",
                description="Read the current orchestrator workflow status.",
                handler=self.resources.workflow_status,
            ),
        }
        self._tools: dict[str, MCPToolDefinition] = {
            "consensus_get": MCPToolDefinition(
                name="consensus_get",
                description="Read the current consensus document.",
                handler=self.gatekeeper_tools.consensus_get,
            ),
            "consensus_update": MCPToolDefinition(
                name="consensus_update",
                description="Update orchestrator-owned consensus fields.",
                handler=self.gatekeeper_tools.consensus_update,
            ),
            "question_ask_user": MCPToolDefinition(
                name="question_ask_user",
                description="Create a structured user-facing question.",
                handler=self.gatekeeper_tools.question_ask_user,
            ),
            "question_resolve": MCPToolDefinition(
                name="question_resolve",
                description="Resolve a structured question record.",
                handler=self.gatekeeper_tools.question_resolve,
            ),
            "roadmap_add_task": MCPToolDefinition(
                name="roadmap_add_task",
                description="Add a task to the roadmap.",
                handler=self.gatekeeper_tools.roadmap_add_task,
            ),
            "roadmap_get": MCPToolDefinition(
                name="roadmap_get",
                description="Read the current roadmap document.",
                handler=self.gatekeeper_tools.roadmap_get,
            ),
            "roadmap_reorder_tasks": MCPToolDefinition(
                name="roadmap_reorder_tasks",
                description="Reorder roadmap tasks by id.",
                handler=self.gatekeeper_tools.roadmap_reorder_tasks,
            ),
            "roadmap_update_task": MCPToolDefinition(
                name="roadmap_update_task",
                description="Update a roadmap task definition.",
                handler=self.gatekeeper_tools.roadmap_update_task,
            ),
            "task_get": MCPToolDefinition(
                name="task_get",
                description="Read one roadmap task by id.",
                handler=self.agent_tools.task_get,
            ),
            "workflow_pause": MCPToolDefinition(
                name="workflow_pause",
                description="Pause the workflow.",
                handler=self.gatekeeper_tools.workflow_pause,
            ),
            "workflow_resume": MCPToolDefinition(
                name="workflow_resume",
                description="Resume the workflow.",
                handler=self.gatekeeper_tools.workflow_resume,
            ),
        }

    def list_resources(self, principal: MCPPrincipal) -> list[MCPResourceDefinition]:
        allowed = set(self.policies[principal.role].resources)
        return [definition for name, definition in self._resources.items() if name in allowed]

    def list_tools(self, principal: MCPPrincipal) -> list[MCPToolDefinition]:
        allowed = set(self.policies[principal.role].tools)
        return [definition for name, definition in self._tools.items() if name in allowed]

    async def read_resource(self, resource_name: str, *, principal: MCPPrincipal, **params: Any) -> Any:
        definition = self._resources.get(resource_name)
        if definition is None:
            raise KeyError(f"Unknown orchestrator MCP resource: {resource_name}")
        ensure_resource_allowed(principal, resource_name, policies=self.policies)
        result = definition.handler(**params)
        if inspect.isawaitable(result):
            return await result
        return result

    async def call_tool(self, tool_name: str, *, principal: MCPPrincipal, **params: Any) -> Any:
        definition = self._tools.get(tool_name)
        if definition is None:
            raise KeyError(f"Unknown orchestrator MCP tool: {tool_name}")
        ensure_tool_allowed(principal, tool_name, policies=self.policies)
        result = definition.handler(**params)
        if inspect.isawaitable(result):
            return await result
        return result
