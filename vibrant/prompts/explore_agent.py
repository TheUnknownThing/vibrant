"""Prompt builders for repository-exploration agents."""

from __future__ import annotations


def build_explore_prompt(*, project: str, objective: str, context_sections: list[str] | None = None) -> str:
    """Render the prompt passed to an explore agent."""

    context_text = "\n\n".join(context_sections or []) or "No additional context provided."
    return "\n".join(
        [
            f"You are an exploration agent working on Project {project}.",
            "## Exploration Objective",
            objective,
            "## Context",
            context_text,
            "## Rules",
            "1. You are strictly read-only and must not create, edit, move, or delete any files.",
            "2. You may inspect source code, configs, git history, and logs to understand project structure.",
            "3. Provide a concise architecture map with key modules, ownership boundaries, and notable risks.",
            "4. If a change seems needed, describe it as a recommendation only; do not implement it.",
        ]
    )
