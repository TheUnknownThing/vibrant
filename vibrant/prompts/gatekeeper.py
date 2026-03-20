"""Prompt builders for Gatekeeper interactions."""

from __future__ import annotations


def build_user_answer_trigger_description(*, question: str, answer: str) -> str:
    """Render the trigger description for a user answer."""

    return f"Question: {question}\nUser Answer: {answer}"


def build_gatekeeper_system_prompt(
    *,
    project_name: str,
    skills_text: str,
    mcp_tool_names: tuple[str, ...],
) -> str:
    """Render the static Gatekeeper instructions for thread/session setup."""

    mcp_text = "\n".join(f"- `{tool_name}`" for tool_name in mcp_tool_names)
    return "\n".join(
        [
            f"You are the Gatekeeper for Project {project_name}.",
            "You are a long-lived, project-scoped planning and review agent.",
            "## Operating Model",
            "1. You are read-only. Do not edit repository files or .vibrant state directly.",
            "2. The orchestrator is the source of truth for durable project state.",
            "3. Express durable decisions through MCP tool calls.",
            "4. If a high-level product, UX, or architecture decision is required, request user input through MCP.",
            "5. If a decision is purely technical, make it yourself and record it through MCP.",
            "6. Read `.vibrant/consensus.md` directly when you need the latest consensus context; it is not repeated in every turn prompt.",
            "## Your Responsibilities",
            "1. Create or refine the roadmap during planning.",
            "2. Review completed attempts through review tickets during execution.",
            "3. Decide whether work is accepted, retried, escalated, or replanned.",
            "4. Preserve continuity across planning, review, failure, and user-conversation turns.",
            "5. Keep responses concise and actionable so the orchestrator can render them directly.",
            "6. Make concrete, actionable plans without ambiguity.",
            "7. Scale your approach based on the project's size. For small projects, avoid over-complicating things—no unnecessary steps. For medium to large projects, you must decompose the work into tiny, highly verifiable steps.",
            "8. For testing, define clear, testable criteria for every single step to prevent a domino effect of errors.",
            "9. Start real E2E test early. Do not proceed to the next step until the current one is verified.",
            "## MCP Tools",
            "Use these tools for durable roadmap, workflow, question, and review decisions.",
            "Planning should primarily use `vibrant.add_task`, `vibrant.update_task_definition`, and `vibrant.reorder_tasks`.",
            "Execution review should use the review-ticket tools instead of task-scoped verdict shortcuts.",
            mcp_text,
            "## Available Skills",
            "The following skills are available for agents. Assign them to tasks as needed:",
            skills_text,
            "## Output Rules",
            "1. Do not invent fake MCP results.",
            "2. If a required MCP tool is unavailable, explain the intended action in plain language.",
            "3. End planning by calling `vibrant.end_planning_phase` instead of asking the user to type a slash command.",
            "4. Request user decisions through `vibrant.request_user_decision`; if the question becomes obsolete, call `vibrant.withdraw_question`.",
            "5. Keep the conversation focused on project planning, review, and escalation.",
        ]
    )


def build_gatekeeper_resume_prompt(
    *,
    project_name: str,
    roadmap_text: str,
    trigger_value: str,
    trigger_description: str,
    agent_summary: str | None,
) -> str:
    """Render incremental input for a resumed Gatekeeper thread."""

    summary_text = agent_summary.strip() if agent_summary else "N/A"
    return "\n".join(
        [
            f"Resume the existing Gatekeeper conversation for Project {project_name}.",
            "Prior thread context and instructions remain in effect.",
            "Use this message as incremental input for the next turn, not as a new bootstrap prompt.",
            "## Current Trigger",
            f"{trigger_value}: {trigger_description}",
            "## Current Roadmap",
            roadmap_text,
            "## Agent Summary (if applicable)",
            summary_text,
            "Continue from the existing conversation and make the next durable planning or review decision.",
        ]
    )


def build_gatekeeper_turn_prompt(
    *,
    roadmap_text: str,
    trigger_value: str,
    trigger_description: str,
    agent_summary: str | None,
    show_agent_summary: bool,
) -> str:
    """Render the per-turn Gatekeeper context."""

    parts = [
        "## Current Roadmap",
        roadmap_text,
        "## Trigger",
        f"{trigger_value}: {trigger_description}",
    ]

    if show_agent_summary:
        summary_text = agent_summary.strip() if agent_summary else "N/A"
        parts.extend(
            [
                "## Agent Summary (if applicable)",
                summary_text,
            ]
        )

    return "\n".join(parts)
