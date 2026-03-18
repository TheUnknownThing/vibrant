"""Centralized prompt builders for agent-facing text."""

from .code_agent import build_task_execution_prompt
from .explore_agent import build_explore_prompt
from .gatekeeper import (
    build_gatekeeper_system_prompt,
    build_gatekeeper_turn_prompt,
    build_user_answer_trigger_description,
)
from .merge_agent import build_merge_prompt
from .review import (
    build_task_completion_trigger_description,
    build_task_escalation_trigger_description,
    build_task_failure_trigger_description,
)
from .test_agent import build_test_prompt

__all__ = [
    "build_explore_prompt",
    "build_gatekeeper_system_prompt",
    "build_gatekeeper_turn_prompt",
    "build_merge_prompt",
    "build_task_completion_trigger_description",
    "build_task_escalation_trigger_description",
    "build_task_execution_prompt",
    "build_task_failure_trigger_description",
    "build_test_prompt",
    "build_user_answer_trigger_description",
]
