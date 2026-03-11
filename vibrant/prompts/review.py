"""Prompt builders for Gatekeeper review triggers."""

from __future__ import annotations


def build_task_completion_trigger_description(
    *,
    task_id: str,
    task_title: str,
    branch: str,
    acceptance_criteria: list[str],
    diff_text: str,
) -> str:
    """Render the review request after a task completes."""

    return "\n".join(
        [
            f"Task {task_id}: {task_title}",
            "Evaluate the completed implementation against the roadmap acceptance criteria.",
            f"Branch: {branch}",
            "Acceptance Criteria:",
            *[f"- {criterion}" for criterion in acceptance_criteria],
            "Git Diff:",
            diff_text,
        ]
    )


def build_task_failure_trigger_description(
    *,
    task_id: str,
    task_title: str,
    retry_count: int,
    max_retries: int,
    reason: str,
    diff_text: str,
) -> str:
    """Render the review request after a task fails."""

    return "\n".join(
        [
            f"Task {task_id}: {task_title}",
            f"Failure Reason: {reason}",
            f"Retry Count: {retry_count} / {max_retries}",
            "Please adjust the task prompt or acceptance criteria for the next retry.",
            "Current Diff / Status:",
            diff_text,
        ]
    )


def build_task_escalation_trigger_description(
    *,
    task_id: str,
    task_title: str,
    retry_count: int,
    max_retries: int,
    reason: str,
    diff_text: str,
) -> str:
    """Render the review request after retries are exhausted."""

    return "\n".join(
        [
            f"Task {task_id}: {task_title}",
            f"Failure Reason: {reason}",
            f"Max retries exceeded at {retry_count} / {max_retries}.",
            "Escalate to the user or pivot the plan.",
            "Current Diff / Status:",
            diff_text,
        ]
    )
