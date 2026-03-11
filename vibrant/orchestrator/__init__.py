"""Orchestration engine components."""

from .bootstrap import Orchestrator, create_orchestrator
from .execution.dispatcher import TaskDispatcher
from .execution.git_manager import GitManager
from .facade import OrchestratorFacade, OrchestratorSnapshot
from .mcp import OrchestratorMCPServer
from .agents.output_projection import AgentOutputProjectionService
from .state.backend import OrchestratorStateBackend
from .types import TaskResult, OrchestratorAgentSnapshot

__all__ = [
    "TaskResult",
    "GitManager",
    "AgentOutputProjectionService",
    "Orchestrator",
    "OrchestratorFacade",
    "OrchestratorAgentSnapshot",
    "OrchestratorMCPServer",
    "OrchestratorSnapshot",
    "OrchestratorStateBackend",
    "TaskDispatcher",
    "create_orchestrator",
]
