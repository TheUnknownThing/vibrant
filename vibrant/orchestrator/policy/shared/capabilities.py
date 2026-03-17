"""Policy-owned MCP capability presets."""

from __future__ import annotations

from collections.abc import Mapping

from ...basic.binding.service import BindingPreset
from ...interface.mcp.common import (
    CONSENSUS_WRITE_SCOPE,
    QUESTIONS_WRITE_SCOPE,
    READ_SCOPE,
    REVIEW_WRITE_SCOPE,
    ROADMAP_WRITE_SCOPE,
    WORKFLOW_WRITE_SCOPE,
    MCPPrincipal,
    MCPResourceDefinition,
    MCPToolDefinition,
)


def gatekeeper_principal(principal_id: str = "gatekeeper") -> MCPPrincipal:
    return MCPPrincipal(
        principal_id=principal_id,
        role="gatekeeper",
        scopes=frozenset(
            {
                READ_SCOPE,
                CONSENSUS_WRITE_SCOPE,
                ROADMAP_WRITE_SCOPE,
                QUESTIONS_WRITE_SCOPE,
                WORKFLOW_WRITE_SCOPE,
                REVIEW_WRITE_SCOPE,
            }
        ),
    )


def worker_principal(*, role: str, principal_id: str) -> MCPPrincipal:
    return MCPPrincipal(
        principal_id=principal_id,
        role=role,
        scopes=frozenset({READ_SCOPE}),
    )


def gatekeeper_binding_preset(mcp_server, run_id: str) -> BindingPreset:
    principal = gatekeeper_principal(principal_id=f"gatekeeper:{run_id}")
    return BindingPreset(
        role="gatekeeper",
        principal=principal,
        tools=visible_tool_names(mcp_server.tool_definitions, principal=principal),
        resources=visible_resource_names(mcp_server.resource_definitions, principal=principal),
    )


def worker_binding_preset(mcp_server, agent_id: str, role: str) -> BindingPreset:
    principal = worker_principal(role=role, principal_id=f"{role}:{agent_id}")
    return BindingPreset(
        role=role,
        principal=principal,
        tools=visible_tool_names(mcp_server.tool_definitions, principal=principal),
        resources=visible_resource_names(mcp_server.resource_definitions, principal=principal),
    )


def validator_binding_preset(mcp_server, agent_id: str, role: str = "test") -> BindingPreset:
    principal = worker_principal(role=role, principal_id=f"{role}:{agent_id}")
    return BindingPreset(
        role=role,
        principal=principal,
        tools=visible_tool_names(mcp_server.tool_definitions, principal=principal),
        resources=visible_resource_names(mcp_server.resource_definitions, principal=principal),
    )


def visible_tool_names(
    definitions: Mapping[str, MCPToolDefinition],
    *,
    principal: MCPPrincipal,
) -> list[str]:
    return [
        definition.name
        for definition in definitions.values()
        if principal.allows(*definition.required_scopes)
    ]


def visible_resource_names(
    definitions: Mapping[str, MCPResourceDefinition],
    *,
    principal: MCPPrincipal,
) -> list[str]:
    return [
        definition.name
        for definition in definitions.values()
        if principal.allows(*definition.required_scopes)
    ]
