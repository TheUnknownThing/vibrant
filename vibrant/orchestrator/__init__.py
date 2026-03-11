"""Lazy exports for orchestration engine components."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentOutputProjectionService",
    "GitManager",
    "Orchestrator",
    "OrchestratorAgentSnapshot",
    "OrchestratorFacade",
    "OrchestratorMCPServer",
    "OrchestratorSnapshot",
    "OrchestratorStateBackend",
    "TaskDispatcher",
    "TaskResult",
    "create_orchestrator",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentOutputProjectionService": (".agents.output_projection", "AgentOutputProjectionService"),
    "GitManager": (".execution.git_manager", "GitManager"),
    "Orchestrator": (".bootstrap", "Orchestrator"),
    "OrchestratorAgentSnapshot": (".types", "OrchestratorAgentSnapshot"),
    "OrchestratorFacade": (".facade", "OrchestratorFacade"),
    "OrchestratorMCPServer": (".mcp", "OrchestratorMCPServer"),
    "OrchestratorSnapshot": (".facade", "OrchestratorSnapshot"),
    "OrchestratorStateBackend": (".state.backend", "OrchestratorStateBackend"),
    "TaskDispatcher": (".tasks.dispatcher", "TaskDispatcher"),
    "TaskResult": (".types", "TaskResult"),
    "create_orchestrator": (".bootstrap", "create_orchestrator"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)
