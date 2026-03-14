"""MCP binding capability wrapper."""

from __future__ import annotations

from dataclasses import dataclass

from .service import AgentSessionBindingService
from ...types import BoundAgentCapabilities


@dataclass(slots=True)
class BindingCapability:
    """Expose role-scoped MCP binding mechanics."""

    service: AgentSessionBindingService

    def bind_gatekeeper(
        self,
        *,
        session_id: str,
        conversation_id: str | None,
    ) -> BoundAgentCapabilities:
        return self.service.bind_gatekeeper(
            session_id=session_id,
            conversation_id=conversation_id,
        )

    def bind_worker(
        self,
        *,
        agent_id: str,
        task_id: str,
        agent_type: str,
    ) -> BoundAgentCapabilities:
        return self.service.bind_worker(
            agent_id=agent_id,
            task_id=task_id,
            agent_type=agent_type,
        )


__all__ = ["AgentSessionBindingService", "BindingCapability"]
