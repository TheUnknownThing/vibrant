"""Roadmap orchestration services."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from vibrant.consensus import RoadmapDocument, RoadmapParser
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.execution.dispatcher import TaskDispatcher


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
        self._sync_dispatcher(concurrency_limit=1)
        return self.document

    def reload(self, *, project_name: str, concurrency_limit: int) -> RoadmapDocument:
        """Refresh the roadmap document and dispatcher from disk."""

        if self.roadmap_path.exists():
            incoming = self.parser.parse_file(self.roadmap_path)
        else:
            incoming = RoadmapDocument(project=project_name, tasks=[])

        if self.document is None or self.dispatcher is None:
            self.document = incoming
            self.dispatcher = TaskDispatcher(incoming.tasks, concurrency_limit=concurrency_limit)
            return self.document

        self.merge_updates(incoming)
        self._sync_dispatcher(concurrency_limit=concurrency_limit)
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
        existing_by_id = {task.id: task for task in self.document.tasks}
        merged_tasks: list[TaskInfo] = []
        incoming_ids: set[str] = set()

        for incoming_task in incoming.tasks:
            incoming_ids.add(incoming_task.id)
            existing = existing_by_id.get(incoming_task.id)
            if existing is None:
                merged_tasks.append(incoming_task)
                continue

            _apply_task_definition(existing, incoming_task)
            merged_tasks.append(existing)

        for existing in self.document.tasks:
            if existing.id not in incoming_ids:
                merged_tasks.append(existing)

        self.document.project = incoming.project
        self.document.tasks = merged_tasks
        self._sync_dispatcher()

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
        self._sync_dispatcher()
        self.persist()
        return task

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        status: TaskStatus | str | None = None,
        branch: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        prompt: str | None = None,
        skills: Sequence[str] | None = None,
        dependencies: Sequence[str] | None = None,
        priority: int | None = None,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        document = self._ensure_document()
        index = next((offset for offset, task in enumerate(document.tasks) if task.id == task_id), None)
        if index is None:
            raise KeyError(f"Task not found in roadmap: {task_id}")

        current = document.tasks[index]
        updated = current.model_copy(deep=True)
        if title is not None:
            updated.title = title
        if acceptance_criteria is not None:
            updated.acceptance_criteria = list(acceptance_criteria)
        if branch is not None:
            updated.branch = branch
        if retry_count is not None:
            updated.retry_count = retry_count
        if max_retries is not None:
            updated.max_retries = max_retries
        if prompt is not None:
            updated.prompt = prompt
        if skills is not None:
            updated.skills = list(skills)
        if dependencies is not None:
            updated.dependencies = list(dependencies)
        if priority is not None:
            updated.priority = priority

        normalized_status = None
        if status is not None:
            normalized_status = status if isinstance(status, TaskStatus) else TaskStatus(str(status).strip().lower())
        if normalized_status is not None and updated.status is not normalized_status:
            updated.transition_to(normalized_status, failure_reason=failure_reason)
        elif failure_reason is not None:
            updated.failure_reason = failure_reason

        updated = TaskInfo.model_validate(updated.model_dump())
        updated_tasks = list(document.tasks)
        updated_tasks[index] = updated
        self.parser.validate_dependency_graph(updated_tasks)
        document.tasks[index] = updated
        self._sync_dispatcher()
        self.persist()
        return updated

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        document = self._ensure_document()
        current_ids = [task.id for task in document.tasks]
        if set(current_ids) != set(ordered_task_ids) or len(current_ids) != len(ordered_task_ids):
            raise ValueError("Task reorder must include every roadmap task exactly once")

        by_id = {task.id: task for task in document.tasks}
        document.tasks = [by_id[task_id] for task_id in ordered_task_ids]
        self._sync_dispatcher()
        self.persist()
        return document

    def _sync_dispatcher(self, *, concurrency_limit: int | None = None) -> None:
        if self.document is None:
            return
        if self.dispatcher is None:
            self.dispatcher = TaskDispatcher(self.document.tasks, concurrency_limit=concurrency_limit or 1)
            return
        if concurrency_limit is not None:
            self.dispatcher.concurrency_limit = concurrency_limit
        self.dispatcher.reconcile_tasks(self.document.tasks)


def _apply_task_definition(existing: TaskInfo, incoming: TaskInfo) -> None:
    existing.title = incoming.title
    existing.acceptance_criteria = list(incoming.acceptance_criteria)
    existing.prompt = incoming.prompt
    existing.skills = list(incoming.skills)
    existing.dependencies = list(incoming.dependencies)
    existing.priority = incoming.priority
    existing.branch = incoming.branch or existing.branch
