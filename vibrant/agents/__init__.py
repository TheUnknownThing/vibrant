"""Agent hierarchy for Vibrant orchestration.

Exports the AgentBase class hierarchy, runtime protocol, and supporting
types used by the orchestrator to run code, test, merge, and gatekeeper agents.
"""

from .base import AgentBase, AgentRunResult, ReadOnlyAgentBase
from .code_agent import CodeAgent
from .explore_agent import ExploreAgent
from .gatekeeper import (
    ACCEPT_REVIEW_TICKET_MCP_TOOL,
    ADD_TASK_MCP_TOOL,
    ESCALATE_REVIEW_TICKET_MCP_TOOL,
    Gatekeeper,
    GatekeeperAgent,
    GatekeeperRequest,
    GatekeeperRunHandle,
    GatekeeperRunResult,
    GatekeeperTrigger,
    MCP_TOOL_NAMES,
    PAUSE_WORKFLOW_MCP_TOOL,
    PLANNING_COMPLETE_MCP_TOOL,
    REQUEST_USER_DECISION_MCP_TOOL,
    REORDER_TASKS_MCP_TOOL,
    RESUME_WORKFLOW_MCP_TOOL,
    RETRY_REVIEW_TICKET_MCP_TOOL,
    UPDATE_CONSENSUS_MCP_TOOL,
    UPDATE_TASK_DEFINITION_MCP_TOOL,
    WITHDRAW_QUESTION_MCP_TOOL,
)
from .merge_agent import MergeAgent
from .test_agent import TestAgent
from .runtime import (
    AgentHandle,
    AgentRecordCallback,
    AgentRuntime,
    BaseAgentRuntime,
    InputRequest,
    NormalizedRunResult,
    ProviderResumeHandle,
    ProviderThreadHandle,
    RunState,
)

__all__ = [
    "AgentBase",
    "AgentHandle",
    "AgentRecordCallback",
    "AgentRunResult",
    "AgentRuntime",
    "BaseAgentRuntime",
    "CodeAgent",
    "ExploreAgent",
    "ACCEPT_REVIEW_TICKET_MCP_TOOL",
    "ADD_TASK_MCP_TOOL",
    "ESCALATE_REVIEW_TICKET_MCP_TOOL",
    "Gatekeeper",
    "GatekeeperAgent",
    "GatekeeperRequest",
    "GatekeeperRunHandle",
    "GatekeeperRunResult",
    "GatekeeperTrigger",
    "InputRequest",
    "MCP_TOOL_NAMES",
    "MergeAgent",
    "NormalizedRunResult",
    "PAUSE_WORKFLOW_MCP_TOOL",
    "ProviderResumeHandle",
    "PLANNING_COMPLETE_MCP_TOOL",
    "ProviderThreadHandle",
    "REQUEST_USER_DECISION_MCP_TOOL",
    "REORDER_TASKS_MCP_TOOL",
    "ReadOnlyAgentBase",
    "RESUME_WORKFLOW_MCP_TOOL",
    "RETRY_REVIEW_TICKET_MCP_TOOL",
    "RunState",
    "TestAgent",
    "UPDATE_CONSENSUS_MCP_TOOL",
    "UPDATE_TASK_DEFINITION_MCP_TOOL",
    "WITHDRAW_QUESTION_MCP_TOOL",
]
