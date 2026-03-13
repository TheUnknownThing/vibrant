"""Agent hierarchy for Vibrant orchestration.

Exports the AgentBase class hierarchy, runtime protocol, and supporting
types used by the orchestrator to run code, merge, and (future) test agents.
"""

from .base import AgentBase, AgentRunResult, ReadOnlyAgentBase
from .code_agent import CodeAgent
from .gatekeeper import (
    Gatekeeper,
    GatekeeperAgent,
    GatekeeperRequest,
    GatekeeperRunHandle,
    GatekeeperRunResult,
    GatekeeperTrigger,
    MARK_TASK_FOR_RETRY_MCP_TOOL,
    MCP_TOOL_NAMES,
    PLANNING_COMPLETE_MCP_TOOL,
    REQUEST_USER_DECISION_MCP_TOOL,
    REVIEW_TASK_OUTCOME_MCP_TOOL,
    SET_PENDING_QUESTIONS_MCP_TOOL,
    UPDATE_CONSENSUS_MCP_TOOL,
    UPDATE_ROADMAP_MCP_TOOL,
)
from .merge_agent import MergeAgent
from .role_results import (
    CodeRoleResult,
    GatekeeperRoleResult,
    GenericRoleResult,
    MergeRoleResult,
    RoleResultPayload,
    TestRoleResult,
    parse_role_result,
    serialize_role_result,
)
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
    "Gatekeeper",
    "GatekeeperAgent",
    "GatekeeperRequest",
    "GatekeeperRunHandle",
    "GatekeeperRunResult",
    "GatekeeperTrigger",
    "InputRequest",
    "MARK_TASK_FOR_RETRY_MCP_TOOL",
    "MCP_TOOL_NAMES",
    "MergeAgent",
    "CodeRoleResult",
    "GatekeeperRoleResult",
    "GenericRoleResult",
    "MergeRoleResult",
    "RoleResultPayload",
    "TestRoleResult",
    "NormalizedRunResult",
    "ProviderResumeHandle",
    "PLANNING_COMPLETE_MCP_TOOL",
    "ProviderThreadHandle",
    "REQUEST_USER_DECISION_MCP_TOOL",
    "REVIEW_TASK_OUTCOME_MCP_TOOL",
    "ReadOnlyAgentBase",
    "RunState",
    "SET_PENDING_QUESTIONS_MCP_TOOL",
    "UPDATE_CONSENSUS_MCP_TOOL",
    "UPDATE_ROADMAP_MCP_TOOL",
    "parse_role_result",
    "serialize_role_result",
]
