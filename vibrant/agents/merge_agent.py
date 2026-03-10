"""Merge agent — resolves git merge conflicts via a provider adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentType
from vibrant.providers.base import RuntimeMode

from .base import AgentBase


class MergeAgent(AgentBase):
    """Agent that resolves git merge conflicts.

    Runs with ``FULL_ACCESS`` mode in the project root directory.
    Receives conflict context (conflicted files, diff output, original
    task summary) and resolves the conflicts by editing the affected files
    and staging them.
    """

    def get_agent_type(self) -> AgentType:
        return AgentType.MERGE

    def get_thread_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.FULL_ACCESS

    def get_turn_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.FULL_ACCESS

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def build_merge_prompt(
        self,
        *,
        task_id: str,
        task_title: str,
        branch: str,
        main_branch: str,
        conflicted_files: list[str],
        conflict_diff: str,
        task_summary: str | None = None,
    ) -> str:
        """Build a prompt for resolving merge conflicts.

        Args:
            task_id: The roadmap task ID whose branch caused the conflict.
            task_title: Human-readable task title for context.
            branch: The source branch being merged (e.g.
                ``vibrant/task-001``).
            main_branch: The target branch (e.g. ``main``).
            conflicted_files: File paths with unresolved conflicts.
            conflict_diff: The raw diff output including conflict markers.
            task_summary: The code agent's summary of what it changed.

        Returns:
            A fully rendered prompt string ready to pass to ``run()``.
        """

        files_list = "\n".join(f"- `{f}`" for f in conflicted_files)
        summary_text = task_summary or "No summary available from the code agent."

        return "\n".join([
            f"You are a merge agent for Project {self.project_root.name}.",
            "",
            "## Your Task",
            f"Resolve merge conflicts when merging branch `{branch}` into `{main_branch}`.",
            "",
            "### Original Task",
            f"Task {task_id}: {task_title}",
            "",
            "### Code Agent Summary",
            summary_text,
            "",
            "## Conflicted Files",
            files_list,
            "",
            "## Conflict Details",
            conflict_diff,
            "",
            "## Rules",
            "1. Resolve ALL merge conflicts in the listed files.",
            "2. Preserve the intent of both the task branch and the main branch changes.",
            "3. If a conflict involves contradictory logic, prefer the task branch's intent",
            "   (since it implements the roadmap task) while keeping main branch fixes.",
            "4. After resolving each file, stage it with `git add <file>`.",
            "5. Do NOT run `git commit` \u2014 the orchestrator handles the merge commit.",
            "6. Do NOT modify any files that are not in the conflicted files list.",
            "7. Provide a summary (~200 words) of how you resolved each conflict.",
        ])

    # ------------------------------------------------------------------
    # Agent record construction
    # ------------------------------------------------------------------

    def build_agent_record(
        self,
        *,
        task_id: str,
        branch: str | None = None,
    ) -> AgentRecord:
        """Build an :class:`AgentRecord` for a merge agent run.

        Args:
            task_id: The task whose merge triggered this agent.
            branch: The branch being merged (for metadata).

        Returns:
            A new ``AgentRecord`` in ``SPAWNING`` status.
        """

        agent_id = f"merge-{task_id}-{uuid4().hex[:8]}"
        native_log = (
            self.vibrant_dir / "logs" / "providers" / "native" / f"{agent_id}.ndjson"
        )
        canonical_log = (
            self.vibrant_dir / "logs" / "providers" / "canonical" / f"{agent_id}.ndjson"
        )
        return AgentRecord(
            agent_id=agent_id,
            task_id=task_id,
            type=AgentType.MERGE,
            branch=branch,
            worktree_path=str(self.project_root),
            provider=AgentProviderMetadata(
                runtime_mode=RuntimeMode.FULL_ACCESS.codex_thread_sandbox,
                native_event_log=str(native_log),
                canonical_event_log=str(canonical_log),
            ),
        )
