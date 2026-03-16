"""Prompt builders for code-execution agents."""

from __future__ import annotations


def build_task_execution_prompt(
    *,
    project: str,
    task_title: str,
    acceptance_criteria: list[str],
    context_sections: list[str],
    skills_text: str,
    branch: str,
    task_id: str,
) -> str:
    """Render the task prompt passed to a code agent."""

    acceptance_lines = (
        "\n".join(f"- [ ] {criterion}" for criterion in acceptance_criteria)
        if acceptance_criteria
        else "- [ ] Complete the assigned task"
    )
    return "\n".join(
        [
            f"You are a code agent working on Project {project}.",
            "## Your Task",
            task_title,
            "## Acceptance Criteria",
            acceptance_lines,
            "## Context",
            "\n\n".join(context_sections),
            "## Skills",
            skills_text,
            "## Rules",
            f"1. You are working in a git worktree on branch `{branch}`.",
            "2. Do NOT modify files outside your task scope.",
            "3. Do NOT modify orchestrator-owned `.vibrant` state such as roadmap, consensus, workflow, or review files.",
            "4. If those orchestrator-owned files should change, describe the proposed change in your summary instead of editing them.",
            "5. When you are done, provide a summary (~500 words) of:",
            "   - What you changed and why",
            "   - What tests you wrote or ran",
            "   - How your implementation satisfies each acceptance criterion",
            "   - Any proposed roadmap, consensus, workflow, or review-state changes",
            "   - Any issues or concerns for the next agent",
            f"6. Commit your changes with a descriptive message prefixed with `[vibrant:{task_id}]`.",
        ]
    )
