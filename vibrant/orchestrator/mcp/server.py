"""Framework-agnostic MCP server registry for orchestrator resources and tools."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from vibrant.orchestrator.facade import OrchestratorFacade

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
    """Small in-process MCP surface over the orchestrator facade."""

    def __init__(self, facade: OrchestratorFacade) -> None:
        self.facade = facade
        self.resources = ResourceHandlers(facade)
        self.gatekeeper_tools = GatekeeperToolHandlers(facade)
        self.agent_tools = AgentToolHandlers(facade)

        self._resources: dict[str, MCPResourceDefinition] = {
            "role.list": MCPResourceDefinition(
                name="role.list",
                description="Read the built-in orchestrator role catalog.",
                handler=self.resources.role_list,
            ),
            "instance.by_id": MCPResourceDefinition(
                name="instance.by_id",
                description="Read one agent-instance snapshot by id.",
                handler=self.resources.instance_by_id,
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
            "task.instances": MCPResourceDefinition(
                name="task.instances",
                description="Read a task together with its related agent instances.",
                handler=self.resources.task_instances,
            ),
            "workflow.status": MCPResourceDefinition(
                name="workflow.status",
                description="Read the current orchestrator workflow status.",
                handler=self.resources.workflow_status,
            ),
        }
        self._tools: dict[str, MCPToolDefinition] = {
            "role_get": MCPToolDefinition(
                "role_get",
                "Read one orchestrator role by id.",
                self.agent_tools.role_get,
            ),
            "role_list": MCPToolDefinition(
                "role_list",
                "List orchestrator role metadata.",
                self.agent_tools.role_list,
            ),
            "instance_get": MCPToolDefinition(
                "instance_get",
                "Read one agent-instance snapshot by id.",
                self.agent_tools.instance_get,
            ),
            "instance_list": MCPToolDefinition(
                "instance_list",
                "List orchestrator agent-instance snapshots.",
                self.agent_tools.instance_list,
            ),
            "run_get": MCPToolDefinition(
                "run_get",
                "Read one orchestrator agent run by run id.",
                self.agent_tools.run_get,
            ),
            "run_list": MCPToolDefinition(
                "run_list",
                "List orchestrator agent runs.",
                self.agent_tools.run_list,
            ),
            "instance_respond_to_request": MCPToolDefinition(
                "instance_respond_to_request",
                "Answer a pending provider request for an existing agent instance.",
                self.agent_tools.instance_respond_to_request,
            ),
            "instance_wait": MCPToolDefinition(
                "instance_wait",
                "Wait for an existing agent instance to reach a result state.",
                self.agent_tools.instance_wait,
            ),
            "consensus_get": MCPToolDefinition(
                "consensus_get",
                "Read the current consensus document.",
                self.gatekeeper_tools.consensus_get,
            ),
            "consensus_update": MCPToolDefinition(
                "consensus_update",
                "Update orchestrator-owned consensus fields.",
                self.gatekeeper_tools.consensus_update,
            ),
            "question_ask_user": MCPToolDefinition(
                "question_ask_user",
                "Create a structured user-facing question.",
                self.gatekeeper_tools.question_ask_user,
            ),
            "question_resolve": MCPToolDefinition(
                "question_resolve",
                "Resolve a structured question record.",
                self.gatekeeper_tools.question_resolve,
            ),
            "roadmap_add_task": MCPToolDefinition(
                "roadmap_add_task",
                "Add a task to the roadmap.",
                self.gatekeeper_tools.roadmap_add_task,
            ),
            "roadmap_get": MCPToolDefinition(
                "roadmap_get",
                "Read the current roadmap document.",
                self.gatekeeper_tools.roadmap_get,
            ),
            "roadmap_reorder_tasks": MCPToolDefinition(
                "roadmap_reorder_tasks",
                "Reorder roadmap tasks by id.",
                self.gatekeeper_tools.roadmap_reorder_tasks,
            ),
            "roadmap_update_task": MCPToolDefinition(
                "roadmap_update_task",
                "Update a roadmap task definition.",
                self.gatekeeper_tools.roadmap_update_task,
            ),
            "task_get": MCPToolDefinition(
                "task_get",
                "Read one roadmap task by id.",
                self.agent_tools.task_get,
            ),
            "workflow_execute_next_task": MCPToolDefinition(
                "workflow_execute_next_task",
                "Dispatch and execute the next roadmap task according to orchestrator workflow rules.",
                self.agent_tools.workflow_execute_next_task,
            ),
            "workflow_pause": MCPToolDefinition(
                "workflow_pause",
                "Pause the workflow.",
                self.gatekeeper_tools.workflow_pause,
            ),
            "workflow_resume": MCPToolDefinition(
                "workflow_resume",
                "Resume the workflow.",
                self.gatekeeper_tools.workflow_resume,
            ),
            "vibrant.end_planning_phase": MCPToolDefinition(
                "vibrant.end_planning_phase",
                "Transition the orchestrator from planning into execution.",
                self.gatekeeper_tools.end_planning_phase,
            ),
            "vibrant.request_user_decision": MCPToolDefinition(
                "vibrant.request_user_decision",
                "Create one user-facing decision request for the Gatekeeper.",
                self.gatekeeper_tools.request_user_decision,
            ),
            "vibrant.set_pending_questions": MCPToolDefinition(
                "vibrant.set_pending_questions",
                "Replace the pending Gatekeeper question set.",
                self.gatekeeper_tools.set_pending_questions,
            ),
            "vibrant.review_task_outcome": MCPToolDefinition(
                "vibrant.review_task_outcome",
                "Record the Gatekeeper verdict for a task outcome.",
                self.gatekeeper_tools.review_task_outcome,
            ),
            "vibrant.mark_task_for_retry": MCPToolDefinition(
                "vibrant.mark_task_for_retry",
                "Update a task for retry and requeue or escalate it.",
                self.gatekeeper_tools.mark_task_for_retry,
            ),
            "vibrant.update_consensus": MCPToolDefinition(
                "vibrant.update_consensus",
                "Update orchestrator-owned consensus fields.",
                self.gatekeeper_tools.update_consensus,
            ),
            "vibrant.update_roadmap": MCPToolDefinition(
                "vibrant.update_roadmap",
                "Replace the roadmap document with a validated task list.",
                self.gatekeeper_tools.update_roadmap,
            ),
        }

    def resource_definitions(self) -> tuple[MCPResourceDefinition, ...]:
        return tuple(self._resources.values())

    def tool_definitions(self) -> tuple[MCPToolDefinition, ...]:
        return tuple(self._tools.values())

    def get_resource_definition(self, resource_name: str) -> MCPResourceDefinition | None:
        return self._resources.get(resource_name)

    def get_tool_definition(self, tool_name: str) -> MCPToolDefinition | None:
        return self._tools.get(tool_name)

    def list_resources(self, principal: object | None = None) -> list[MCPResourceDefinition]:
        return list(self._resources.values())

    def list_tools(self, principal: object | None = None) -> list[MCPToolDefinition]:
        return list(self._tools.values())

    async def read_resource(self, resource_name: str, *, principal: object | None = None, **params: Any) -> Any:
        definition = self._resources.get(resource_name)
        if definition is None:
            raise KeyError(f"Unknown orchestrator MCP resource: {resource_name}")
        result = definition.handler(**params)
        if inspect.isawaitable(result):
            return await result
        return result

    async def call_tool(self, tool_name: str, *, principal: object | None = None, **params: Any) -> Any:
        definition = self._tools.get(tool_name)
        if definition is None:
            raise KeyError(f"Unknown orchestrator MCP tool: {tool_name}")
        result = definition.handler(**params)
        if inspect.isawaitable(result):
            return await result
        return result
