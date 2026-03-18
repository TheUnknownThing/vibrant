"""CodeAgent — workspace-write agent for task execution."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from vibrant.config import DEFAULT_CONFIG_DIR
from vibrant.models.agent import AgentProviderMetadata, AgentRunRecord, AgentStatus, AgentType
from vibrant.models.task import TaskInfo
from vibrant.providers.registry import provider_transport

from .base import AgentBase
from .utils import extract_summary_from_turn_result, extract_tagged_summary_from_transcript

if TYPE_CHECKING:
    from vibrant.orchestrator.types import WorkspaceHandle


class CodeAgent(AgentBase):
    """Agent that executes code tasks inside a worktree.

    Runtime modes are inherited from config defaults (typically WORKSPACE_WRITE).
    Interactive requests are auto-rejected.
    """

    def get_agent_type(self) -> AgentType:
        return AgentType.CODE

    def extract_summary(
        self,
        transcript: str,
        turn_result: object | None,
    ) -> str | None:
        tagged_summary = extract_tagged_summary_from_transcript(transcript)
        if tagged_summary:
            return tagged_summary

        provider_summary = extract_summary_from_turn_result(turn_result)
        if provider_summary:
            return provider_summary

        return transcript or None

    def build_run_record(
        self,
        *,
        task: TaskInfo,
        worktree: WorkspaceHandle,
        prompt: str,
        agent_id: str | None = None,
        role: str | None = None,
        run_id: str | None = None,
    ) -> AgentRunRecord:
        """Create an AgentRunRecord for one code-agent execution."""

        resolved_agent_id = agent_id or f"agent-{task.id}-{uuid4().hex[:8]}"
        resolved_run_id = run_id or f"run-{task.id}-{uuid4().hex[:8]}"
        resolved_role = role or task.agent_role or self.get_agent_type().value

        vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        native_log = vibrant_dir / "logs" / "providers" / "native" / f"{resolved_run_id}.ndjson"
        canonical_log = vibrant_dir / "logs" / "providers" / "canonical" / f"{resolved_run_id}.ndjson"

        return AgentRunRecord(
            identity={
                "run_id": resolved_run_id,
                "agent_id": resolved_agent_id,
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
                native_event_log=str(native_log),
                canonical_event_log=str(canonical_log),
            ),
        )

    def build_agent_record(
        self,
        *,
        task: TaskInfo,
        worktree: WorkspaceHandle,
        prompt: str,
    ) -> AgentRunRecord:
        return self.build_run_record(
            task=task,
            worktree=worktree,
            prompt=prompt,
        )
