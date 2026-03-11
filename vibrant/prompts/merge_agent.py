"""Prompt builders for merge-conflict resolution agents."""

from __future__ import annotations

from textwrap import dedent


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
    """Render the prompt passed to the merge agent."""

    files_list = "\n".join(f"- {path}" for path in conflicted_files) if conflicted_files else "- (none listed)"
    summary_section = task_summary.strip() if task_summary else "No summary available."

    return dedent(
        f"""\
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
        """
    )
