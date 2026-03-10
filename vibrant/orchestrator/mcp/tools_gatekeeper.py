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

    def roadmap_update_task(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        updated = self.facade.update_task(task_id, **updates)
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
        record = self.facade.ask_question(
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
        return {"status": self.facade.workflow_status().value}

    def workflow_resume(self) -> dict[str, Any]:
        self.facade.resume_workflow()
        return {"status": self.facade.workflow_status().value}
