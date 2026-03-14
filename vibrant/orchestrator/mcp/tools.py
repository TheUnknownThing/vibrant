"""Semantic write tools for the orchestrator MCP surface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.types import QuestionPriority, WorkflowStatus


class OrchestratorMCPTools:
    """Semantic command handlers over the internal MCP command surface."""

    def __init__(self, commands: Any) -> None:
        self.commands = commands

    def update_consensus(
        self,
        *,
        context: str | None = None,
        status: str | None = None,
        decision_title: str | None = None,
        decision_context: str | None = None,
        resolution: str | None = None,
        impact: str | None = None,
    ) -> Any:
        if decision_title:
            return self.commands.append_decision(
                title=decision_title,
                context=decision_context or "",
                resolution=resolution or "",
                impact=impact or "",
            )
        return self.commands.update_consensus(context=context, status=status)

    def add_task(
        self,
        *,
        task_id: str,
        title: str,
        acceptance_criteria: Sequence[str] | None = None,
        skills: Sequence[str] | None = None,
        dependencies: Sequence[str] | None = None,
        prompt: str | None = None,
        branch: str | None = None,
        priority: int | None = None,
        max_retries: int = 3,
        index: int | None = None,
    ) -> Any:
        task = TaskInfo(
            id=task_id,
            title=title,
            acceptance_criteria=list(acceptance_criteria or ()),
            status=TaskStatus.PENDING,
            branch=branch,
            max_retries=max_retries,
            prompt=prompt,
            skills=list(skills or ()),
            dependencies=list(dependencies or ()),
            priority=priority,
        )
        return self.commands.add_task(task, index=index)

    def update_task_definition(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        skills: Sequence[str] | None = None,
        dependencies: Sequence[str] | None = None,
        prompt: str | None = None,
        branch: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
    ) -> Any:
        return self.commands.update_task_definition(
            task_id,
            title=title,
            acceptance_criteria=list(acceptance_criteria) if acceptance_criteria is not None else None,
            skills=list(skills) if skills is not None else None,
            dependencies=list(dependencies) if dependencies is not None else None,
            prompt=prompt,
            branch=branch,
            priority=priority,
            max_retries=max_retries,
        )

    def reorder_tasks(self, task_ids: Sequence[str]) -> Any:
        return self.commands.reorder_tasks(list(task_ids))

    def request_user_decision(
        self,
        *,
        text: str,
        priority: str = "blocking",
        blocking_scope: str = "planning",
        task_id: str | None = None,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        source_conversation_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> Any:
        return self.commands.request_user_decision(
            text,
            priority=QuestionPriority(priority),
            blocking_scope=blocking_scope,
            task_id=task_id,
            source_agent_id=source_agent_id,
            source_role=source_role,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
        )

    def withdraw_question(self, question_id: str, *, reason: str | None = None) -> Any:
        return self.commands.withdraw_question(question_id, reason=reason)

    def end_planning_phase(self) -> Any:
        return self.commands.set_workflow_status(WorkflowStatus.EXECUTING)

    def pause_workflow(self) -> Any:
        return self.commands.pause_workflow()

    def resume_workflow(self) -> Any:
        return self.commands.resume_workflow()

    def accept_review_ticket(self, ticket_id: str) -> Any:
        return self.commands.accept_review_ticket(ticket_id)

    def retry_review_ticket(
        self,
        ticket_id: str,
        *,
        failure_reason: str,
        prompt_patch: str | None = None,
        acceptance_patch: Sequence[str] | None = None,
    ) -> Any:
        return self.commands.retry_review_ticket(
            ticket_id,
            failure_reason=failure_reason,
            prompt_patch=prompt_patch,
            acceptance_patch=list(acceptance_patch) if acceptance_patch is not None else None,
        )

    def escalate_review_ticket(self, ticket_id: str, *, reason: str) -> Any:
        return self.commands.escalate_review_ticket(ticket_id, reason=reason)

    def update_roadmap(self, *, tasks: Sequence[dict[str, Any]], project: str | None = None) -> Any:
        normalized_tasks = [TaskInfo.model_validate(task) for task in tasks]
        return self.commands.replace_roadmap(tasks=normalized_tasks, project=project)

    def set_pending_questions(
        self,
        *,
        questions: Sequence[str],
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
    ) -> Any:
        return self.commands.set_pending_questions(
            list(questions),
            source_agent_id=source_agent_id,
            source_role=source_role,
        )

    def review_task_outcome(
        self,
        task_id: str,
        *,
        decision: str,
        failure_reason: str | None = None,
    ) -> Any:
        return self.commands.review_task_outcome(task_id, decision=decision, failure_reason=failure_reason)

    def mark_task_for_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
    ) -> Any:
        return self.commands.mark_task_for_retry(
            task_id,
            failure_reason=failure_reason,
            prompt=prompt,
            acceptance_criteria=list(acceptance_criteria) if acceptance_criteria is not None else None,
        )
