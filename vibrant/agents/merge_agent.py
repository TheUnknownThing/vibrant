"""MergeAgent — full-access agent for resolving merge conflicts."""

from __future__ import annotations

from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRunRecord, AgentStatus
from vibrant.providers.base import RuntimeMode
from vibrant.providers.registry import provider_transport
from vibrant.prompts import build_merge_prompt as render_merge_prompt

from .base import AgentBase, AgentRunResult
from .role_results import RoleResultPayload, build_merge_role_result


class MergeAgent(AgentBase):
    """Agent that resolves merge conflicts in the project root.

    Runs with FULL_ACCESS runtime mode since it needs to manipulate
    branches and resolve conflicts across the repository.
    """

    def get_agent_role(self) -> str:
        return "merge"

    def get_thread_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.FULL_ACCESS

    def get_turn_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.FULL_ACCESS

    def build_role_result(
        self,
        *,
        result: AgentRunResult,
        agent_record: AgentRunRecord,
        input_requests: list[object],
    ) -> RoleResultPayload | None:
        return build_merge_role_result(
            transcript=result.transcript,
            summary=agent_record.outcome.summary,
            error=result.error,
            exit_code=result.exit_code,
            awaiting_input=agent_record.lifecycle.status is AgentStatus.AWAITING_INPUT,
            events=result.events,
        )

    @staticmethod
    def build_merge_prompt(
        *,
        task_id: str,
        task_title: str,
        branch: str,
        main_branch: str,
        conflicted_files: list[str],
        conflict_diff: str,
        task_summary: str | None = None,
    ) -> str:
        """Build the prompt instructing the merge agent to resolve conflicts."""
        return render_merge_prompt(
            task_id=task_id,
            task_title=task_title,
            branch=branch,
            main_branch=main_branch,
            conflicted_files=conflicted_files,
            conflict_diff=conflict_diff,
            task_summary=task_summary,
        )

    def build_agent_record(
        self,
        *,
        task_id: str,
        branch: str,
    ) -> AgentRunRecord:
        """Create an AgentRunRecord for a merge agent run."""
        agent_id = f"merge-{task_id}-{uuid4().hex[:8]}"
        return AgentRunRecord(
            identity={
                "agent_id": agent_id,
                "task_id": task_id,
                "role": "merge",
            },
            lifecycle={"status": AgentStatus.SPAWNING},
            context={
                "branch": branch,
                "worktree_path": str(self.project_root),
            },
            provider=AgentProviderMetadata(
                kind=self.config.provider_kind.value,
                transport=provider_transport(self.config.provider_kind),
                runtime_mode="danger-full-access",
            ),
        )
