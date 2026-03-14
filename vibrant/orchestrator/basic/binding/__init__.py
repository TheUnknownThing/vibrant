"""MCP binding capability wrapper."""

from __future__ import annotations

from dataclasses import dataclass

from .service import AgentSessionBindingService, BindingPreset
from ...types import BoundAgentCapabilities


@dataclass(slots=True)
class BindingCapability:
    """Expose role-scoped MCP binding mechanics."""

    service: AgentSessionBindingService

    def bind_preset(
        self,
        *,
        preset: BindingPreset,
        run_id: str,
        conversation_id: str | None,
    ) -> BoundAgentCapabilities:
        return self.service.bind_preset(
            preset=preset,
            run_id=run_id,
            conversation_id=conversation_id,
        )


__all__ = ["AgentSessionBindingService", "BindingCapability"]
