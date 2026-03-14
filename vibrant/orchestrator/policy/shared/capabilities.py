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


def worker_principal(*, agent_type: str, principal_id: str) -> MCPPrincipal:
    return MCPPrincipal(
        principal_id=principal_id,
        role=agent_type,
        scopes=frozenset({READ_SCOPE}),
    )


def gatekeeper_binding_preset(mcp_server, session_id: str) -> BindingPreset:
    principal = gatekeeper_principal(principal_id=f"gatekeeper:{session_id}")
    return BindingPreset(
        role="gatekeeper",
        principal=principal,
        tools=visible_tool_names(mcp_server.tool_definitions, principal=principal),
        resources=visible_resource_names(mcp_server.resource_definitions, principal=principal),
    )


def worker_binding_preset(mcp_server, agent_id: str, agent_type: str) -> BindingPreset:
    principal = worker_principal(agent_type=agent_type, principal_id=f"{agent_type}:{agent_id}")
    return BindingPreset(
        role=agent_type,
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
