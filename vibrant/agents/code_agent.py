"""CodeAgent — workspace-write agent for task execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRunRecord, AgentStatus, AgentType
from vibrant.providers.registry import provider_transport

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

    def build_run_record(
        self,
        *,
        task: TaskInfo,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_id: str | None = None,
        role: str | None = None,
        run_id: str | None = None,
        vibrant_dir: str | Path | None = None,
    ) -> AgentRunRecord:
        """Create an AgentRunRecord for one code-agent execution."""

        resolved_agent_id = agent_id or f"agent-{task.id}-{uuid4().hex[:8]}"
        resolved_run_id = run_id or f"run-{task.id}-{uuid4().hex[:8]}"
        resolved_role = role or task.agent_role or self.get_agent_type().value

        provider_kwargs: dict[str, str | None] = {}
        if vibrant_dir is not None:
            vdir = Path(vibrant_dir)
            native_log = vdir / "logs" / "providers" / "native" / f"{resolved_run_id}.ndjson"
            canonical_log = vdir / "logs" / "providers" / "canonical" / f"{resolved_run_id}.ndjson"
            provider_kwargs["native_event_log"] = str(native_log)
            provider_kwargs["canonical_event_log"] = str(canonical_log)

        return AgentRunRecord(
            identity={
                "run_id": resolved_run_id,
                "agent_id": resolved_agent_id,
                "task_id": task.id,
                "role": resolved_role,
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
            provider=AgentProviderMetadata(
                kind=self.config.provider_kind.value,
                transport=provider_transport(self.config.provider_kind),
                **provider_kwargs,
            ),
        )

    def build_agent_record(
        self,
        *,
        task: TaskInfo,
        worktree: GitWorktreeInfo,
        prompt: str,
        vibrant_dir: str | Path | None = None,
    ) -> AgentRunRecord:
        return self.build_run_record(
            task=task,
            worktree=worktree,
            prompt=prompt,
            vibrant_dir=vibrant_dir,
        )
