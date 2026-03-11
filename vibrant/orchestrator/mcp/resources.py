"""Resource handlers for orchestrator MCP."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
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

    def task_assigned(self, *, task_id: str | None = None, agent_id: str | None = None) -> dict[str, Any]:
        resolved_task_id = _resolve_task_id(self.facade, task_id=task_id, agent_id=agent_id)
        task = self.facade.task(resolved_task_id)
        if task is None:
            raise KeyError(f"Unknown task: {resolved_task_id}")
        agents = self.facade.list_agents(task_id=resolved_task_id, include_completed=True)
        latest_agent = agents[-1] if agents else None
        return {
            "task": _serialize_task(task),
            "agents": [_serialize_value(snapshot) for snapshot in agents],
            "latest_agent": _serialize_value(latest_agent),
        }

    def agent_status(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        if agent_id is not None:
            snapshot = self.facade.get_agent(agent_id)
            if snapshot is None:
                raise KeyError(f"Unknown agent: {agent_id}")
            return _serialize_value(snapshot)
        snapshots = self.facade.list_agents(
            task_id=task_id,
            include_completed=include_completed,
            active_only=active_only,
        )
        return [_serialize_value(snapshot) for snapshot in snapshots]

    def workflow_status(self) -> dict[str, Any]:
        return {"status": self.facade.workflow_status().value}

    def questions_pending(self) -> list[dict[str, Any]]:
        return [_serialize_question(record) for record in self.facade.pending_question_records()]

    def events_recent(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        state_store = getattr(self.facade.orchestrator, "state_store", None)
        engine = getattr(state_store, "engine", None)
        if engine is None:
            engine = getattr(self.facade.orchestrator, "engine", None)
        events = list(getattr(engine, "emitted_events", []) or [])
        filtered: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if agent_id is not None and event.get("agent_id") != agent_id:
                continue
            if task_id is not None and event.get("task_id") != task_id:
                continue
            filtered.append(_serialize_value(event))
        return filtered[-limit:] if limit else []



def _serialize_task(task: TaskInfo) -> dict[str, Any]:
    return task.model_dump(mode="json")



def _serialize_question(question: QuestionRecord) -> dict[str, Any]:
    return question.model_dump(mode="json")


def _resolve_task_id(facade: OrchestratorFacade, *, task_id: str | None, agent_id: str | None) -> str:
    if task_id is not None:
        return task_id
    if agent_id is None:
        raise ValueError("task_id or agent_id is required")
    snapshot = facade.get_agent(agent_id)
    if snapshot is None:
        raise KeyError(f"Unknown agent: {agent_id}")
    return snapshot.identity.task_id


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    if is_dataclass(value):
        return {field.name: _serialize_value(getattr(value, field.name)) for field in fields(value)}
    return value
