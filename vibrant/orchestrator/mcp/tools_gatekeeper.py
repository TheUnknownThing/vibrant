"""Gatekeeper-oriented orchestrator MCP tools."""

from __future__ import annotations

from typing import Any, Sequence

from vibrant.models.state import QuestionPriority
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.facade import OrchestratorFacade

from .resources import ResourceHandlers


class GatekeeperToolHandlers:
    """Privileged tools exposed to the Gatekeeper role."""

    def __init__(self, facade: OrchestratorFacade) -> None:
        self.facade = facade
        self.resources = ResourceHandlers(facade)

    def consensus_get(self) -> dict[str, Any] | None:
        return self.resources.consensus_current()

    def consensus_update(
        self,
        *,
        status: str | None = None,
        objectives: str | None = None,
        getting_started: str | None = None,
        questions: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self.facade.update_consensus(
            status=status,
            objectives=objectives,
            getting_started=getting_started,
            questions=questions,
        ).model_dump(mode="json")

    def roadmap_get(self) -> dict[str, Any] | None:
        return self.resources.roadmap_current()

    def roadmap_add_task(self, task: dict[str, Any], *, index: int | None = None) -> dict[str, Any]:
        created = self.facade.add_task(TaskInfo.model_validate(task), index=index)
        return created.model_dump(mode="json")

    def roadmap_update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        status: str | None = None,
        branch: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        prompt: str | None = None,
        skills: Sequence[str] | None = None,
        dependencies: Sequence[str] | None = None,
        priority: int | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        updated = self.facade.update_task(
            task_id,
            title=title,
            acceptance_criteria=acceptance_criteria,
            status=status,
            branch=branch,
            retry_count=retry_count,
            max_retries=max_retries,
            prompt=prompt,
            skills=skills,
            dependencies=dependencies,
            priority=priority,
            failure_reason=failure_reason,
        )
        return updated.model_dump(mode="json")

    def roadmap_reorder_tasks(self, ordered_task_ids: list[str]) -> dict[str, Any]:
        roadmap = self.facade.reorder_tasks(ordered_task_ids)
        return {
            "project": roadmap.project,
            "tasks": [task.model_dump(mode="json") for task in roadmap.tasks],
        }

    def question_ask_user(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        priority: str = QuestionPriority.BLOCKING.value,
    ) -> dict[str, Any]:
        record = self.facade.request_user_decision(
            text,
            source_agent_id=source_agent_id,
            source_role="gatekeeper",
            priority=QuestionPriority(priority),
        )
        return record.model_dump(mode="json")

    def question_resolve(self, question_id: str, *, answer: str | None = None) -> dict[str, Any]:
        return self.facade.resolve_question(question_id, answer=answer).model_dump(mode="json")

    def workflow_pause(self) -> dict[str, Any]:
        self.facade.pause_workflow()
        return {"status": self.facade.get_workflow_status().value}

    def workflow_resume(self) -> dict[str, Any]:
        self.facade.resume_workflow()
        return {"status": self.facade.get_workflow_status().value}

    def end_planning_phase(self) -> dict[str, Any]:
        return {"status": self.facade.end_planning_phase().value}

    def request_user_decision(
        self,
        question: str,
        *,
        source_agent_id: str | None = None,
        priority: str = QuestionPriority.BLOCKING.value,
    ) -> dict[str, Any]:
        record = self.facade.request_user_decision(
            question,
            source_agent_id=source_agent_id,
            source_role="gatekeeper",
            priority=QuestionPriority(priority),
        )
        return record.model_dump(mode="json")

    def set_pending_questions(
        self,
        questions: Sequence[str],
        *,
        source_agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        records = self.facade.set_pending_questions(
            questions,
            source_agent_id=source_agent_id,
            source_role="gatekeeper",
        )
        return [record.model_dump(mode="json") for record in records]

    def review_task_outcome(
        self,
        task_id: str,
        *,
        decision: str,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        task = self.facade.review_task_outcome(task_id, decision=decision, failure_reason=failure_reason)
        return task.model_dump(mode="json")

    def mark_task_for_retry(
        self,
        task_id: str,
        *,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        task = self.facade.mark_task_for_retry(
            task_id,
            failure_reason=failure_reason,
            prompt=prompt,
            acceptance_criteria=acceptance_criteria,
        )
        return task.model_dump(mode="json")

    def update_consensus(
        self,
        *,
        status: str | None = None,
        objectives: str | None = None,
        getting_started: str | None = None,
        questions: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self.consensus_update(
            status=status,
            objectives=objectives,
            getting_started=getting_started,
            questions=questions,
        )

    def update_roadmap(self, *, tasks: Sequence[dict[str, Any]], project: str | None = None) -> dict[str, Any]:
        roadmap = self.facade.replace_roadmap(
            tasks=[TaskInfo.model_validate(task) for task in tasks],
            project=project,
        )
        return {
            "project": roadmap.project,
            "tasks": [task.model_dump(mode="json") for task in roadmap.tasks],
        }
