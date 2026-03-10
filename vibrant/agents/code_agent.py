"""Code agent — executes roadmap tasks in an isolated git worktree."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentType
from vibrant.models.task import TaskInfo

from .base import AgentBase

if TYPE_CHECKING:
    from vibrant.orchestrator.git_manager import GitWorktreeInfo


class CodeAgent(AgentBase):
    """Agent that executes a single roadmap task in an isolated worktree.

    Runs with the default runtime mode from ``VibrantConfig`` (typically
    ``WORKSPACE_WRITE``).  Interactive provider requests are auto-rejected.
    """

    def get_agent_type(self) -> AgentType:
        return AgentType.CODE

    def build_agent_record(
        self,
        *,
        task: TaskInfo,
        worktree: GitWorktreeInfo,
        prompt: str,
    ) -> AgentRecord:
        """Build an :class:`AgentRecord` for a code-agent task execution.

        Args:
            task: The roadmap task being executed.
            worktree: The git worktree created for this agent.
            prompt: The fully rendered prompt that will be sent.

        Returns:
            A new ``AgentRecord`` in ``SPAWNING`` status.
        """

        agent_id = f"agent-{task.id}-{uuid4().hex[:8]}"
        native_log = (
            self.vibrant_dir / "logs" / "providers" / "native" / f"{agent_id}.ndjson"
        )
        canonical_log = (
            self.vibrant_dir / "logs" / "providers" / "canonical" / f"{agent_id}.ndjson"
        )
        return AgentRecord(
            agent_id=agent_id,
            task_id=task.id,
            type=AgentType.CODE,
            branch=task.branch,
            worktree_path=str(worktree.path),
            prompt_used=prompt,
            skills_loaded=list(task.skills),
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            provider=AgentProviderMetadata(
                native_event_log=str(native_log),
                canonical_event_log=str(canonical_log),
            ),
        )
