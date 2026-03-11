"""Read-only sub-agent orchestrator MCP tools."""

from __future__ import annotations

from typing import Any

from vibrant.orchestrator.facade import OrchestratorFacade

from .resources import ResourceHandlers


class AgentToolHandlers:
    """Read-only tools exposed to execution agents."""

    def __init__(self, facade: OrchestratorFacade) -> None:
        self.facade = facade
        self.resources = ResourceHandlers(facade)

    def consensus_get(self) -> dict[str, Any] | None:
        return self.resources.consensus_current()

    def roadmap_get(self) -> dict[str, Any] | None:
        return self.resources.roadmap_current()

    def task_get(self, task_id: str) -> dict[str, Any]:
        return self.resources.task_by_id(task_id)
