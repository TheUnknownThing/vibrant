"""Role-scoped authorization helpers for the orchestrator MCP surface."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class OrchestratorMCPRole(str, enum.Enum):
    GATEKEEPER = "gatekeeper"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class MCPPrincipal:
    """Identity presented to the orchestrator MCP surface."""

    role: OrchestratorMCPRole
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class RolePolicy:
    """Allowed resources and tools for one MCP role."""

    resources: tuple[str, ...]
    tools: tuple[str, ...]


class MCPAuthorizationError(PermissionError):
    """Raised when a principal lacks permission for a tool or resource."""


GATEKEEPER_RESOURCES = (
    "consensus.current",
    "questions.pending",
    "roadmap.current",
    "task.by_id",
    "workflow.status",
)
GATEKEEPER_TOOLS = (
    "consensus_get",
    "consensus_update",
    "question_ask_user",
    "question_resolve",
    "roadmap_add_task",
    "roadmap_get",
    "roadmap_reorder_tasks",
    "roadmap_update_task",
    "workflow_pause",
    "workflow_resume",
)
AGENT_RESOURCES = (
    "consensus.current",
    "roadmap.current",
    "task.by_id",
)
AGENT_TOOLS = (
    "consensus_get",
    "roadmap_get",
    "task_get",
)


def default_role_policies() -> dict[OrchestratorMCPRole, RolePolicy]:
    """Return the default role-scoped policy map for orchestrator MCP."""

    return {
        OrchestratorMCPRole.GATEKEEPER: RolePolicy(resources=GATEKEEPER_RESOURCES, tools=GATEKEEPER_TOOLS),
        OrchestratorMCPRole.AGENT: RolePolicy(resources=AGENT_RESOURCES, tools=AGENT_TOOLS),
    }


def ensure_resource_allowed(
    principal: MCPPrincipal,
    resource_name: str,
    *,
    policies: dict[OrchestratorMCPRole, RolePolicy] | None = None,
) -> None:
    policy_map = policies or default_role_policies()
    policy = policy_map[principal.role]
    if resource_name not in policy.resources:
        raise MCPAuthorizationError(f"Role {principal.role.value} cannot read resource {resource_name}")



def ensure_tool_allowed(
    principal: MCPPrincipal,
    tool_name: str,
    *,
    policies: dict[OrchestratorMCPRole, RolePolicy] | None = None,
) -> None:
    policy_map = policies or default_role_policies()
    policy = policy_map[principal.role]
    if tool_name not in policy.tools:
        raise MCPAuthorizationError(f"Role {principal.role.value} cannot call tool {tool_name}")
