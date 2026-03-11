"""Centralized prompt builders for agent-facing text."""

from .code_agent import build_task_execution_prompt
from .gatekeeper import build_gatekeeper_prompt, build_user_answer_trigger_description
from .review import (
    build_task_completion_trigger_description,
    build_task_escalation_trigger_description,
    build_task_failure_trigger_description,
)

__all__ = [
    "build_gatekeeper_prompt",
    "build_task_completion_trigger_description",
    "build_task_escalation_trigger_description",
    "build_task_execution_prompt",
    "build_task_failure_trigger_description",
    "build_user_answer_trigger_description",
]
