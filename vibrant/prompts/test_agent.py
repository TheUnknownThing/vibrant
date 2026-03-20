"""Prompt builders for test agents."""

from __future__ import annotations


def build_test_prompt(
    *,
    project: str,
    task_id: str,
    branch: str,
    code_summary: str | None,
    pycua_enabled: bool = False,
) -> str:
    """Render the prompt passed to a test agent."""

    prior_summary = (
        code_summary or "No implementation summary was captured from the code agent."
    )
    prompt_lines = [
        f"You are a test agent working on Project {project}.",
        "## Validation Target",
        f"Task ID: {task_id}",
        f"Branch: {branch}",
        "## Code Agent Summary",
        prior_summary,
        "## Rules",
        "1. You are strictly read-only and must not create, edit, move, or delete files.",
        "2. Do NOT modify source files, git history, or orchestrator-owned `.vibrant` state.",
        "3. Select the validation steps that best verify the implementation and stop only if a step cannot continue.",
        "4. End your response with exactly one `<vibrant_summary>...</vibrant_summary>` block.",
        "5. Only the text inside `<vibrant_summary>` and `</vibrant_summary>` is captured as the validation summary sent to the gatekeeper.",
        "6. Inside that tagged summary block, report:",
        "   - Whether validation passed or failed",
        "   - Which commands or checks you ran",
        "   - The first concrete failure, if any",
        "   - Any notable risks, gaps, or follow-up validation needed",
    ]

    if pycua_enabled:
        prompt_lines.append(
            "7. You may use the pyCUA MCP `computer` tool when explicitly needed for UI/system validation."
        )

    return "\n".join(prompt_lines)
