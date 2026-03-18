"""Redesigned orchestrator package."""

from typing import TYPE_CHECKING

from .types import AgentRunSnapshot, TaskResult

if TYPE_CHECKING:
    from .bootstrap import Orchestrator, create_orchestrator
    from .facade import OrchestratorFacade, OrchestratorSnapshot
    from .mcp import OrchestratorMCPServer

__all__ = [
    "AgentRunSnapshot",
    "Orchestrator",
    "OrchestratorFacade",
    "OrchestratorMCPServer",
    "OrchestratorSnapshot",
    "TaskResult",
    "create_orchestrator",
]


def __getattr__(name: str) -> object:
    if name in {"Orchestrator", "create_orchestrator"}:
        from .bootstrap import Orchestrator, create_orchestrator

        return {"Orchestrator": Orchestrator, "create_orchestrator": create_orchestrator}[name]
    if name in {"OrchestratorFacade", "OrchestratorSnapshot"}:
        from .facade import OrchestratorFacade, OrchestratorSnapshot

        return {"OrchestratorFacade": OrchestratorFacade, "OrchestratorSnapshot": OrchestratorSnapshot}[name]
    if name == "OrchestratorMCPServer":
        from .mcp import OrchestratorMCPServer

        return OrchestratorMCPServer
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(__all__)
