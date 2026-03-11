"""Roadmap orchestration services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vibrant.consensus import RoadmapDocument, RoadmapParser
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.task_dispatch import TaskDispatcher


_UNSET = object()


class RoadmapService:
    """Load, persist, and merge roadmap state for the orchestrator."""

    def __init__(
        self,
        roadmap_path: str | Path,
        *,
        project_name: str | None = None,
        parser: RoadmapParser | None = None,
    ) -> None:
        self.roadmap_path = Path(roadmap_path)
        self.project_name = project_name or self.roadmap_path.parent.parent.name
        self.parser = parser or RoadmapParser()
        self.document: RoadmapDocument | None = None
        self.dispatcher: TaskDispatcher | None = None

    def _ensure_document(self) -> RoadmapDocument:
        if self.document is not None:
            return self.document
        if self.roadmap_path.exists():
            self.document = self.parser.parse_file(self.roadmap_path)
        else:
            self.document = RoadmapDocument(project=self.project_name, tasks=[])
        if self.dispatcher is None:
            self.dispatcher = TaskDispatcher(self.document.tasks, concurrency_limit=1)
        return self.document

    def reload(self, *, project_name: str, concurrency_limit: int) -> RoadmapDocument:
        """Refresh the roadmap document and dispatcher from disk."""

        if self.roadmap_path.exists():
            incoming = self.parser.parse_file(self.roadmap_path)
        else:
            incoming = RoadmapDocument(project=project_name, tasks=[])

        if self.document is None or self.dispatcher is None:
            self.document = incoming
            self.dispatcher = TaskDispatcher(
                incoming.tasks,
                concurrency_limit=concurrency_limit,
            )
            return self.document

        self.dispatcher.concurrency_limit = concurrency_limit
        self.merge_updates(incoming)
        return self.document

    def persist(self) -> None:
        """Persist the in-memory roadmap document to disk."""

        document = self._ensure_document()
        self.parser.write(self.roadmap_path, document)

    def merge_result(self, roadmap: RoadmapDocument | None) -> None:
        """Merge roadmap updates returned by a reviewer or planner."""

        if roadmap is None:
            return
        self.merge_updates(roadmap)

    def merge_updates(self, incoming: RoadmapDocument) -> None:
        """Merge an updated roadmap document into tracked state."""

        assert self.document is not None
        assert self.dispatcher is not None

        existing_by_id = {task.id: task for task in self.document.tasks}
        merged_tasks: list[TaskInfo] = []
        incoming_ids: set[str] = set()

        for incoming_task in incoming.tasks:
            incoming_ids.add(incoming_task.id)
            existing = existing_by_id.get(incoming_task.id)
            if existing is None:
                merged_tasks.append(incoming_task)
                self.dispatcher.add_task(incoming_task)
                continue

            _apply_task_definition(existing, incoming_task)
            merged_tasks.append(existing)

        for existing in self.document.tasks:
            if existing.id not in incoming_ids:
                merged_tasks.append(existing)

        self.document.project = incoming.project
        self.document.tasks = merged_tasks

    def get_task(self, task_id: str) -> TaskInfo | None:
        document = self._ensure_document()
        for task in document.tasks:
            if task.id == task_id:
                return task
        return None

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        document = self._ensure_document()
        if self.get_task(task.id) is not None:
            raise ValueError(f"Task already exists in roadmap: {task.id}")

        insertion_index = len(document.tasks) if index is None else index
        if insertion_index < 0 or insertion_index > len(document.tasks):
            raise IndexError(f"Task insertion index out of range: {insertion_index}")

        updated_tasks = list(document.tasks)
        updated_tasks.insert(insertion_index, task)
        self.parser.validate_dependency_graph(updated_tasks)
        document.tasks = updated_tasks
        self.persist()
        return task

    def update_task(self, task_id: str, **updates: Any) -> TaskInfo:
        document = self._ensure_document()
        index = next((offset for offset, task in enumerate(document.tasks) if task.id == task_id), None)
        if index is None:
            raise KeyError(f"Task not found in roadmap: {task_id}")

        current = document.tasks[index]
        updated = current.model_copy(deep=True)

        for field_name, value in updates.items():
            if value is _UNSET:
                continue
            if field_name == "status":
                normalized_status = value if isinstance(value, TaskStatus) else TaskStatus(str(value).strip().lower())
                if updated.status is not normalized_status:
                    updated.transition_to(normalized_status)
                continue
            if not hasattr(updated, field_name):
                raise ValueError(f"Unsupported task field update: {field_name}")
            setattr(updated, field_name, value)

        updated_tasks = list(document.tasks)
        updated_tasks[index] = updated
        self.parser.validate_dependency_graph(updated_tasks)
        document.tasks = updated_tasks
        self.persist()
        return updated

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        document = self._ensure_document()
        current_ids = [task.id for task in document.tasks]
        if set(current_ids) != set(ordered_task_ids) or len(current_ids) != len(ordered_task_ids):
            raise ValueError("Task reorder must include every roadmap task exactly once")

        by_id = {task.id: task for task in document.tasks}
        document.tasks = [by_id[task_id] for task_id in ordered_task_ids]
        self.persist()
        return document


def _apply_task_definition(existing: TaskInfo, incoming: TaskInfo) -> None:
    existing.title = incoming.title
    existing.acceptance_criteria = list(incoming.acceptance_criteria)
    existing.prompt = incoming.prompt
    existing.skills = list(incoming.skills)
    existing.dependencies = list(incoming.dependencies)
    existing.priority = incoming.priority
    existing.branch = incoming.branch or existing.branch
