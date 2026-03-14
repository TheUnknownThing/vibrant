"""Redesigned orchestrator package."""

from .types import AgentRunSnapshot, OrchestratorAgentSnapshot, TaskResult

__all__ = [
    "AgentRunSnapshot",
    "Orchestrator",
    "OrchestratorAgentSnapshot",
    "OrchestratorFacade",
    "OrchestratorMCPServer",
    "OrchestratorSnapshot",
    "TaskResult",
    "create_orchestrator",
]


def __getattr__(name: str):
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
