"""Resource handlers for orchestrator MCP."""

from __future__ import annotations

from typing import Any

from vibrant.models.state import QuestionRecord
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.facade import OrchestratorFacade


class ResourceHandlers:
    """Expose typed read resources backed by ``OrchestratorFacade``."""

    def __init__(self, facade: OrchestratorFacade) -> None:
        self.facade = facade

    def consensus_current(self) -> dict[str, Any] | None:
        document = self.facade.consensus_document()
        return document.model_dump(mode="json") if document is not None else None

    def roadmap_current(self) -> dict[str, Any] | None:
        roadmap = self.facade.roadmap()
        if roadmap is None:
            return None
        return {
            "project": roadmap.project,
            "tasks": [_serialize_task(task) for task in roadmap.tasks],
        }

    def task_by_id(self, task_id: str) -> dict[str, Any]:
        task = self.facade.task(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")
        return _serialize_task(task)

    def workflow_status(self) -> dict[str, Any]:
        return {"status": self.facade.workflow_status().value}

    def questions_pending(self) -> list[dict[str, Any]]:
        return [_serialize_question(record) for record in self.facade.pending_question_records()]



def _serialize_task(task: TaskInfo) -> dict[str, Any]:
    return task.model_dump(mode="json")



def _serialize_question(question: QuestionRecord) -> dict[str, Any]:
    return question.model_dump(mode="json")
