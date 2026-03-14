"""Agent capability binding for orchestrator MCP scopes."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import uuid4

from vibrant.providers.invocation import MCPAccessDescriptor

from ...interface.mcp.binding_registry import BINDING_HEADER_NAME
from ...interface.mcp.common import MCPPrincipal
from ...types import BoundAgentCapabilities


@dataclass(slots=True)
class BindingPreset:
    role: str
    principal: MCPPrincipal
    tools: list[str]
    resources: list[str]


class AgentSessionBindingService:
    """Attach orchestrator MCP scopes to agent sessions."""

    def __init__(
        self,
        *,
        mcp_server: Any,
        mcp_host: Any | None = None,
    ) -> None:
        self._mcp_server = mcp_server
        self._mcp_host = mcp_host

    @property
    def mcp_server(self) -> Any:
        return self._mcp_server

    @property
    def mcp_host(self) -> Any | None:
        return self._mcp_host

    def bind_preset(
        self,
        *,
        preset: BindingPreset,
        conversation_id: str | None,
        session_id: str,
    ) -> BoundAgentCapabilities:
        return self._build_bound_capabilities(
            preset,
            conversation_id=conversation_id,
            session_id=session_id,
        )

    def _build_bound_capabilities(
        self,
        preset: BindingPreset,
        *,
        conversation_id: str | None,
        session_id: str,
    ) -> BoundAgentCapabilities:
        binding_id = f"binding-{preset.role}-{uuid4().hex[:12]}"
        endpoint_url = getattr(self._mcp_host, "endpoint_url", None)
        access = MCPAccessDescriptor(
            binding_id=binding_id,
            role=preset.role,
            session_id=session_id,
            conversation_id=conversation_id,
            visible_tools=list(preset.tools),
            visible_resources=list(preset.resources),
            endpoint_url=endpoint_url,
            server_id=_build_server_id(preset.role, session_id),
            transport_hint="http" if endpoint_url else None,
            required=True,
            static_headers={BINDING_HEADER_NAME: binding_id} if endpoint_url else {},
            metadata={"principal_id": preset.principal.principal_id},
        )
        provider_binding = {
            **access.to_mapping(),
            "principal_id": preset.principal.principal_id,
        }
        return BoundAgentCapabilities(
            principal=preset.principal,
            mcp_server=self._mcp_server,
            tool_names=list(preset.tools),
            resource_names=list(preset.resources),
            provider_binding=provider_binding,
            access=access,
        )


def _build_server_id(role: str, session_id: str) -> str:
    raw = f"vibrant_{role}_{session_id}"
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    return normalized[:48] or "vibrant"
