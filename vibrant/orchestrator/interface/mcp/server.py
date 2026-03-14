"""In-process MCP server for the redesigned orchestrator surface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .common import (
    CONSENSUS_WRITE_SCOPE,
    MCPNotFoundError,
    MCPPrincipal,
    MCPResourceDefinition,
    MCPToolDefinition,
    QUESTIONS_WRITE_SCOPE,
    READ_SCOPE,
    REVIEW_WRITE_SCOPE,
    ROADMAP_WRITE_SCOPE,
    WORKFLOW_WRITE_SCOPE,
    require_scopes,
    serialize_value,
)
from .resources import OrchestratorMCPResources
from .tools import OrchestratorMCPTools


class OrchestratorMCPServer:
    """Typed tool/resource registry backed by orchestrator services."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.resources = OrchestratorMCPResources(backend.queries)
        self.tools = OrchestratorMCPTools(backend.commands)
        self._resource_defs = self._build_resources()
        self._tool_defs = self._build_tools()

    @property
    def tool_definitions(self) -> Mapping[str, MCPToolDefinition]:
        return self._tool_defs

    @property
    def resource_definitions(self) -> Mapping[str, MCPResourceDefinition]:
        return self._resource_defs

    def list_tools(self, *, principal: MCPPrincipal | None = None) -> list[dict[str, Any]]:
        definitions = []
        for definition in self._tool_defs.values():
            if principal is not None and not principal.allows(*definition.required_scopes):
                continue
            definitions.append(
                {
                    "name": definition.name,
                    "description": definition.description,
                    "required_scopes": list(definition.required_scopes),
                }
            )
        return definitions

    def list_resources(self, *, principal: MCPPrincipal | None = None) -> list[dict[str, Any]]:
        definitions = []
        for definition in self._resource_defs.values():
            if principal is not None and not principal.allows(*definition.required_scopes):
                continue
            definitions.append(
                {
                    "name": definition.name,
                    "description": definition.description,
                    "required_scopes": list(definition.required_scopes),
                }
            )
        return definitions

    async def call_tool(self, name: str, /, *, principal: MCPPrincipal | None = None, **kwargs: Any) -> Any:
        definition = self._tool_defs.get(name)
        if definition is None:
            raise MCPNotFoundError(f"Unknown MCP tool: {name}")
        require_scopes(principal, *definition.required_scopes)
        result = definition.handler(**kwargs)
        if hasattr(result, "__await__"):
            result = await result
        return serialize_value(result)

    async def read_resource(self, name: str, /, *, principal: MCPPrincipal | None = None, **kwargs: Any) -> Any:
        definition = self._resource_defs.get(name)
        if definition is None:
            raise MCPNotFoundError(f"Unknown MCP resource: {name}")
        require_scopes(principal, *definition.required_scopes)
        result = definition.handler(**kwargs)
        if hasattr(result, "__await__"):
            result = await result
        return serialize_value(result)

    def _build_resources(self) -> dict[str, MCPResourceDefinition]:
        return {
            "vibrant.get_consensus": MCPResourceDefinition(
                name="vibrant.get_consensus",
                description="Return the current orchestrator-owned consensus view.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.get_consensus,
            ),
            "vibrant.get_roadmap": MCPResourceDefinition(
                name="vibrant.get_roadmap",
                description="Return the current roadmap view.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.get_roadmap,
            ),
            "vibrant.get_task": MCPResourceDefinition(
                name="vibrant.get_task",
                description="Return one roadmap task by id.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.get_task,
            ),
            "vibrant.get_workflow_status": MCPResourceDefinition(
                name="vibrant.get_workflow_status",
                description="Return the authoritative workflow status.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.get_workflow_status,
            ),
            "vibrant.list_pending_questions": MCPResourceDefinition(
                name="vibrant.list_pending_questions",
                description="List pending user decisions.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.list_pending_questions,
            ),
            "vibrant.list_active_agents": MCPResourceDefinition(
                name="vibrant.list_active_agents",
                description="List active agent runtimes.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.list_active_agents,
            ),
            "vibrant.list_active_attempts": MCPResourceDefinition(
                name="vibrant.list_active_attempts",
                description="List active execution attempts.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.list_active_attempts,
            ),
            "vibrant.get_review_ticket": MCPResourceDefinition(
                name="vibrant.get_review_ticket",
                description="Return a review ticket by id.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.get_review_ticket,
            ),
            "vibrant.list_pending_review_tickets": MCPResourceDefinition(
                name="vibrant.list_pending_review_tickets",
                description="List unresolved review tickets.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.list_pending_review_tickets,
            ),
            "vibrant.list_recent_events": MCPResourceDefinition(
                name="vibrant.list_recent_events",
                description="Return recent orchestrator domain events.",
                required_scopes=(READ_SCOPE,),
                handler=self.resources.list_recent_events,
            ),
        }

    def _build_tools(self) -> dict[str, MCPToolDefinition]:
        return {
            "vibrant.update_consensus": MCPToolDefinition(
                name="vibrant.update_consensus",
                description="Update orchestrator-owned consensus context or append a decision.",
                required_scopes=(CONSENSUS_WRITE_SCOPE,),
                handler=self.tools.update_consensus,
            ),
            "vibrant.add_task": MCPToolDefinition(
                name="vibrant.add_task",
                description="Add a roadmap task with a full typed definition.",
                required_scopes=(ROADMAP_WRITE_SCOPE,),
                handler=self.tools.add_task,
            ),
            "vibrant.update_task_definition": MCPToolDefinition(
                name="vibrant.update_task_definition",
                description="Update the editable definition of a roadmap task.",
                required_scopes=(ROADMAP_WRITE_SCOPE,),
                handler=self.tools.update_task_definition,
            ),
            "vibrant.reorder_tasks": MCPToolDefinition(
                name="vibrant.reorder_tasks",
                description="Reorder roadmap tasks by task id.",
                required_scopes=(ROADMAP_WRITE_SCOPE,),
                handler=self.tools.reorder_tasks,
            ),
            "vibrant.request_user_decision": MCPToolDefinition(
                name="vibrant.request_user_decision",
                description="Open a user decision request through the host.",
                required_scopes=(QUESTIONS_WRITE_SCOPE,),
                handler=self.tools.request_user_decision,
            ),
            "vibrant.withdraw_question": MCPToolDefinition(
                name="vibrant.withdraw_question",
                description="Withdraw a pending user decision request.",
                required_scopes=(QUESTIONS_WRITE_SCOPE,),
                handler=self.tools.withdraw_question,
            ),
            "vibrant.end_planning_phase": MCPToolDefinition(
                name="vibrant.end_planning_phase",
                description="Transition the workflow from planning into execution.",
                required_scopes=(WORKFLOW_WRITE_SCOPE,),
                handler=self.tools.end_planning_phase,
            ),
            "vibrant.pause_workflow": MCPToolDefinition(
                name="vibrant.pause_workflow",
                description="Pause orchestrator workflow execution.",
                required_scopes=(WORKFLOW_WRITE_SCOPE,),
                handler=self.tools.pause_workflow,
            ),
            "vibrant.resume_workflow": MCPToolDefinition(
                name="vibrant.resume_workflow",
                description="Resume orchestrator workflow execution.",
                required_scopes=(WORKFLOW_WRITE_SCOPE,),
                handler=self.tools.resume_workflow,
            ),
            "vibrant.accept_review_ticket": MCPToolDefinition(
                name="vibrant.accept_review_ticket",
                description="Accept a review ticket and apply the merge/acceptance flow.",
                required_scopes=(REVIEW_WRITE_SCOPE,),
                handler=self.tools.accept_review_ticket,
            ),
            "vibrant.retry_review_ticket": MCPToolDefinition(
                name="vibrant.retry_review_ticket",
                description="Reject a review ticket and request a retry with explicit feedback.",
                required_scopes=(REVIEW_WRITE_SCOPE,),
                handler=self.tools.retry_review_ticket,
            ),
            "vibrant.escalate_review_ticket": MCPToolDefinition(
                name="vibrant.escalate_review_ticket",
                description="Escalate a review ticket for human intervention.",
                required_scopes=(REVIEW_WRITE_SCOPE,),
                handler=self.tools.escalate_review_ticket,
            ),
            "vibrant.update_roadmap": MCPToolDefinition(
                name="vibrant.update_roadmap",
                description="Temporary name-level alias for replacing the roadmap document.",
                required_scopes=(ROADMAP_WRITE_SCOPE,),
                handler=self.tools.update_roadmap,
            ),
        }
