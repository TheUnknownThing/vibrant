"""Prompt builders for test agents."""

from __future__ import annotations


def build_test_prompt(
    *,
    project: str,
    task_id: str,
    branch: str,
    code_summary: str | None,
) -> str:
    """Render the prompt passed to a test agent."""

    prior_summary = (
        code_summary or "No implementation summary was captured from the code agent."
    )
    return "\n".join(
        [
            f"You are a test agent working on Project {project}.",
            "## Validation Target",
            f"Task ID: {task_id}",
            f"Branch: {branch}",
            "## Code Agent Summary",
            prior_summary,
            "## Rules",
            "1. You are strictly read-only and must not create, edit, move, or delete files.",
            "2. Do NOT modify source files, git history, or orchestrator-owned `.vibrant` state.",
            "3. Run the validation commands in order and stop only if a command cannot continue.",
            "4. You may use the pyCUA MCP `computer` tool when explicitly needed for UI/system validation.",
            "5. Report whether validation passed or failed, which commands ran, and the first concrete failure if any.",
        ]
    )
