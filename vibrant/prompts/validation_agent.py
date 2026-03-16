"""Prompt builders for validation agents."""

from __future__ import annotations


def build_validation_prompt(
    *,
    project: str,
    task_id: str,
    branch: str,
    test_commands: list[str],
    code_summary: str | None,
) -> str:
    """Render the validation prompt passed to a validation agent."""

    command_lines = "\n".join(f"{index}. `{command}`" for index, command in enumerate(test_commands, start=1))
    prior_summary = code_summary or "No implementation summary was captured from the code agent."
    return "\n".join(
        [
            f"You are a validation agent working on Project {project}.",
            "## Validation Target",
            f"Task ID: {task_id}",
            f"Branch: {branch}",
            "## Code Agent Summary",
            prior_summary,
            "## Validation Commands",
            command_lines,
            "## Rules",
            "1. You are inspecting a prepared git worktree in read-only mode.",
            "2. Do NOT modify source files, git history, or orchestrator-owned `.vibrant` state.",
            "3. Run the validation commands in order and stop only if a command cannot continue.",
            "4. Report whether validation passed or failed, which commands ran, and the first concrete failure if any.",
            "5. Keep the final summary concise and action-oriented for review.",
        ]
    )
