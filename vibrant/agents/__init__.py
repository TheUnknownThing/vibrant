"""Agent hierarchy for Vibrant orchestration.

Exports the AgentBase class hierarchy, runtime protocol, and supporting
types used by the orchestrator to run code, merge, and (future) test agents.
"""

from .base import AgentBase, AgentRunResult, ReadOnlyAgentBase
from .code_agent import CodeAgent
from .merge_agent import MergeAgent
from .runtime import (
    AgentHandle,
    AgentRecordCallback,
    AgentRuntime,
    BaseAgentRuntime,
    InputRequest,
    NormalizedRunResult,
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
    "InputRequest",
    "MergeAgent",
    "NormalizedRunResult",
    "ProviderThreadHandle",
    "ReadOnlyAgentBase",
    "RunState",
]
