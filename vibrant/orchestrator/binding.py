"""Agent capability binding for orchestrator MCP scopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import BoundAgentCapabilities


@dataclass(slots=True)
class BindingPreset:
    principal: str
    tools: list[str]
    resources: list[str]


class AgentSessionBindingService:
    """Attach orchestrator MCP scopes to agent sessions."""

    def __init__(self, *, mcp_server: Any) -> None:
        self._mcp_server = mcp_server

    def bind_gatekeeper(
        self,
        *,
        session_id: str,
        conversation_id: str | None,
    ) -> BoundAgentCapabilities:
        preset = BindingPreset(
            principal=f"gatekeeper:{session_id}",
            tools=self._mcp_server.gatekeeper_tool_names(),
            resources=self._mcp_server.gatekeeper_resource_names(),
        )
        return self._build_bound_capabilities(
            preset,
            conversation_id=conversation_id,
            session_id=session_id,
        )

    def bind_worker(
        self,
        *,
        agent_id: str,
        task_id: str,
        agent_type: str,
    ) -> BoundAgentCapabilities:
        preset = BindingPreset(
            principal=f"{agent_type}:{agent_id}",
            tools=self._mcp_server.worker_tool_names(agent_type=agent_type),
            resources=self._mcp_server.worker_resource_names(agent_type=agent_type),
        )
        return self._build_bound_capabilities(
            preset,
            conversation_id=None,
            session_id=task_id,
        )

    def _build_bound_capabilities(
        self,
        preset: BindingPreset,
        *,
        conversation_id: str | None,
        session_id: str,
    ) -> BoundAgentCapabilities:
        provider_binding = {
            "principal": preset.principal,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "tools": list(preset.tools),
            "resources": list(preset.resources),
        }
        return BoundAgentCapabilities(
            principal=preset.principal,
            mcp_server=self._mcp_server,
            tool_names=list(preset.tools),
            resource_names=list(preset.resources),
            provider_binding=provider_binding,
        )
