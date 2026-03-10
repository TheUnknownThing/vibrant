"""MergeAgent — full-access agent for resolving merge conflicts."""

from __future__ import annotations

from textwrap import dedent
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.providers.base import RuntimeMode

from .base import AgentBase


class MergeAgent(AgentBase):
    """Agent that resolves merge conflicts in the project root.

    Runs with FULL_ACCESS runtime mode since it needs to manipulate
    branches and resolve conflicts across the repository.
    """

    def get_agent_type(self) -> AgentType:
        return AgentType.MERGE

    def get_thread_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.FULL_ACCESS

    def get_turn_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.FULL_ACCESS

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
        files_list = "\n".join(f"- {f}" for f in conflicted_files) if conflicted_files else "- (none listed)"
        summary_section = task_summary.strip() if task_summary else "No summary available."

        return dedent(f"""\
            You are a Merge Agent. Your sole job is to resolve merge conflicts
            between the task branch and the main branch, preserving the intent
            of both sides.

            ## Context
            - **Task ID**: {task_id}
            - **Task Title**: {task_title}
            - **Task Branch**: {branch}
            - **Main Branch**: {main_branch}

            ## Task Summary
            {summary_section}

            ## Conflicted Files
            {files_list}

            ## Conflict Diff
            ```
            {conflict_diff}
            ```

            ## Instructions
            1. Examine each conflicted file carefully.
            2. Resolve conflicts by keeping the correct intent from both sides.
            3. Stage the resolved files with `git add`.
            4. Commit the merge resolution with a clear commit message.
            5. Do NOT introduce new features or make unrelated changes.
            6. Do NOT delete or revert legitimate changes from either branch.
        """)

    def build_agent_record(
        self,
        *,
        task_id: str,
        branch: str,
    ) -> AgentRecord:
        """Create an AgentRecord for a merge agent run."""
        agent_id = f"merge-{task_id}-{uuid4().hex[:8]}"
        return AgentRecord(
            agent_id=agent_id,
            task_id=task_id,
            type=AgentType.MERGE,
            status=AgentStatus.SPAWNING,
            branch=branch,
            worktree_path=str(self.project_root),
            provider=AgentProviderMetadata(
                runtime_mode="danger-full-access",
            ),
        )
