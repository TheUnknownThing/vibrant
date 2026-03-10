"""Roadmap orchestration services."""

from __future__ import annotations

from pathlib import Path

from vibrant.consensus import RoadmapDocument, RoadmapParser
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.task_dispatch import TaskDispatcher


class RoadmapService:
    """Load, persist, and merge roadmap state for the orchestrator."""

    def __init__(
        self,
        roadmap_path: str | Path,
        *,
        parser: RoadmapParser | None = None,
    ) -> None:
        self.roadmap_path = Path(roadmap_path)
        self.parser = parser or RoadmapParser()
        self.document: RoadmapDocument | None = None
        self.dispatcher: TaskDispatcher | None = None

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

        if self.document is None:
            return
        self.parser.write(self.roadmap_path, self.document)

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


def _apply_task_definition(existing: TaskInfo, incoming: TaskInfo) -> None:
    existing.title = incoming.title
    existing.acceptance_criteria = list(incoming.acceptance_criteria)
    existing.prompt = incoming.prompt
    existing.skills = list(incoming.skills)
    existing.dependencies = list(incoming.dependencies)
    existing.priority = incoming.priority
    existing.branch = incoming.branch or existing.branch
