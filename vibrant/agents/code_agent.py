"""CodeAgent — workspace-write agent for task execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType

from .base import AgentBase

if TYPE_CHECKING:
    from vibrant.models.task import TaskInfo
    from vibrant.orchestrator.execution.git_manager import GitWorktreeInfo


class CodeAgent(AgentBase):
    """Agent that executes code tasks inside a worktree.

    Runtime modes are inherited from config defaults (typically WORKSPACE_WRITE).
    Interactive requests are auto-rejected.
    """

    def get_agent_type(self) -> AgentType:
        return AgentType.CODE

    def build_agent_record(
        self,
        *,
        task: TaskInfo,
        worktree: GitWorktreeInfo,
        prompt: str,
        vibrant_dir: str | Path | None = None,
    ) -> AgentRecord:
        """Create an AgentRecord for a code agent run.

        This is the canonical factory for code agent records, extracted from
        the legacy ``AgentRegistry.create_code_agent_record``.
        """
        agent_id = f"agent-{task.id}-{uuid4().hex[:8]}"

        provider_kwargs: dict[str, str | None] = {}
        if vibrant_dir is not None:
            vdir = Path(vibrant_dir)
            native_log = vdir / "logs" / "providers" / "native" / f"{agent_id}.ndjson"
            canonical_log = vdir / "logs" / "providers" / "canonical" / f"{agent_id}.ndjson"
            provider_kwargs["native_event_log"] = str(native_log)
            provider_kwargs["canonical_event_log"] = str(canonical_log)

        return AgentRecord(
            identity={
                "agent_id": agent_id,
                "task_id": task.id,
                "type": AgentType.CODE,
            },
            lifecycle={"status": AgentStatus.SPAWNING},
            context={
                "branch": task.branch,
                "worktree_path": str(worktree.path),
                "prompt_used": prompt,
                "skills_loaded": list(task.skills),
            },
            retry={
                "retry_count": task.retry_count,
                "max_retries": task.max_retries,
            },
            provider=AgentProviderMetadata(**provider_kwargs),
        )
