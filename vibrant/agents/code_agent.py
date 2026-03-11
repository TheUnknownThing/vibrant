"""CodeAgent — workspace-write agent for task execution."""

from __future__ import annotations

from vibrant.models.agent import AgentType

from .base import AgentBase


class CodeAgent(AgentBase):
    """Agent that executes code tasks inside a worktree.

    Runtime modes are inherited from config defaults (typically WORKSPACE_WRITE).
    Interactive requests are auto-rejected.
    """

    def get_agent_type(self) -> AgentType:
        return AgentType.CODE
