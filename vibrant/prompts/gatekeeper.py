"""Prompt builders for Gatekeeper interactions."""

from __future__ import annotations


def build_user_answer_trigger_description(*, question: str, answer: str) -> str:
    """Render the trigger description for a user answer."""

    return f"Question: {question}\nUser Answer: {answer}"


def build_gatekeeper_prompt(
    *,
    project_name: str,
    consensus_text: str,
    consensus_contract_text: str,
    trigger_value: str,
    trigger_description: str,
    agent_summary: str | None,
    skills_text: str,
    planning_complete_mcp_tool: str,
) -> str:
    """Render the main Gatekeeper prompt."""

    summary_text = agent_summary.strip() if agent_summary else "N/A"
    return "\n".join(
        [
            f"You are the Gatekeeper for Project {project_name}. You are the sole authority over the project plan.",
            "## Your Responsibilities",
            "1. Evaluate agent output against the plan's acceptance criteria.",
            "2. Update orchestrator-owned consensus and roadmap state through MCP tools.",
            "3. If an agent failed, analyze the failure and modify the plan through MCP.",
            "4. If you encounter a high-level decision (product direction, UX, architecture), request user input through MCP.",
            "5. If the decision is purely technical, make it yourself and record it through MCP.",
            "## Consensus Context",
            consensus_contract_text,
            "## Current Consensus",
            consensus_text,
            "## Trigger",
            f"{trigger_value}: {trigger_description}",
            "## Agent Summary (if applicable)",
            summary_text,
            "## Rules",
            "1. The orchestrator is the source of truth for durable project state.",
            "2. Use orchestrator MCP tools for every durable update.",
            "3. Never ask the user to type legacy slash commands to advance workflow state.",
            "4. When re-planning a failed task, preserve the failure context in your review output.",
            f"5. End planning by calling `{planning_complete_mcp_tool}`.",
            "## Available Skills",
            "The following skills are available for agents. Assign them to tasks as needed:",
            skills_text,
        ]
    )
