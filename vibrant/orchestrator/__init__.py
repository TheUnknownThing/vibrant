"""Orchestration engine components."""

from __future__ import annotations

__all__ = [
    "AgentRunSnapshot",
    "TaskResult",
    "TaskExecutionResult",
    "GitManager",
    "AgentOutputProjectionService",
    "DocumentSnapshot",
    "AgentInstanceSnapshot",
    "AgentRoleSnapshot",
    "QuestionAnswerResult",
    "RoleSnapshot",
    "Orchestrator",
    "OrchestratorFacade",
    "OrchestratorMCPServer",
    "OrchestratorSnapshot",
    "OrchestratorStateBackend",
    "TaskDispatcher",
    "WorkflowSnapshot",
    "create_orchestrator",
    "create_orchestrator_fastmcp",
    "create_orchestrator_fastmcp_app",
]


def __getattr__(name: str):
    if name in {"Orchestrator", "create_orchestrator"}:
        from .bootstrap import Orchestrator, create_orchestrator

        return {"Orchestrator": Orchestrator, "create_orchestrator": create_orchestrator}[name]
    if name == "TaskDispatcher":
        from .tasks.dispatcher import TaskDispatcher

        return TaskDispatcher
    if name == "GitManager":
        from .execution.git_manager import GitManager

        return GitManager
    if name in {"OrchestratorFacade", "OrchestratorSnapshot"}:
        from .facade import OrchestratorFacade, OrchestratorSnapshot

        return {"OrchestratorFacade": OrchestratorFacade, "OrchestratorSnapshot": OrchestratorSnapshot}[name]
    if name in {"OrchestratorMCPServer", "create_orchestrator_fastmcp", "create_orchestrator_fastmcp_app"}:
        from .mcp import OrchestratorMCPServer, create_orchestrator_fastmcp, create_orchestrator_fastmcp_app

        return {
            "OrchestratorMCPServer": OrchestratorMCPServer,
            "create_orchestrator_fastmcp": create_orchestrator_fastmcp,
            "create_orchestrator_fastmcp_app": create_orchestrator_fastmcp_app,
        }[name]
    if name == "AgentOutputProjectionService":
        from .agents.output_projection import AgentOutputProjectionService

        return AgentOutputProjectionService
    if name == "OrchestratorStateBackend":
        from .state.backend import OrchestratorStateBackend

        return OrchestratorStateBackend
    if name in {
        "AgentInstanceSnapshot",
        "AgentRoleSnapshot",
        "AgentRunSnapshot",
        "DocumentSnapshot",
        "QuestionAnswerResult",
        "RoleSnapshot",
        "TaskExecutionResult",
        "TaskResult",
        "WorkflowSnapshot",
    }:
        from .types import (
            AgentInstanceSnapshot,
            AgentRoleSnapshot,
            AgentRunSnapshot,
            DocumentSnapshot,
            QuestionAnswerResult,
            RoleSnapshot,
            TaskExecutionResult,
            TaskResult,
            WorkflowSnapshot,
        )

        return {
            "AgentInstanceSnapshot": AgentInstanceSnapshot,
            "AgentRoleSnapshot": AgentRoleSnapshot,
            "AgentRunSnapshot": AgentRunSnapshot,
            "DocumentSnapshot": DocumentSnapshot,
            "QuestionAnswerResult": QuestionAnswerResult,
            "RoleSnapshot": RoleSnapshot,
            "TaskExecutionResult": TaskExecutionResult,
            "TaskResult": TaskResult,
            "WorkflowSnapshot": WorkflowSnapshot,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
