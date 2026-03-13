"""Roadmap document store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vibrant.consensus.roadmap import RoadmapDocument, RoadmapParser
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.json_store import read_json, write_json
from vibrant.orchestrator.types import TaskState


_STATE_TO_STATUS = {
    TaskState.PENDING: TaskStatus.PENDING,
    TaskState.READY: TaskStatus.QUEUED,
    TaskState.ACTIVE: TaskStatus.IN_PROGRESS,
    TaskState.REVIEW_PENDING: TaskStatus.COMPLETED,
    TaskState.BLOCKED: TaskStatus.FAILED,
    TaskState.ACCEPTED: TaskStatus.ACCEPTED,
    TaskState.ESCALATED: TaskStatus.ESCALATED,
}


class RoadmapStore:
    """Persist roadmap markdown plus task-definition sidecar metadata."""

    def __init__(self, path: str | Path, *, project_name: str) -> None:
        self.path = Path(path)
        self.project_name = project_name
        self.meta_path = self.path.with_name("roadmap.meta.json")
        self.parser = RoadmapParser()

    def load(self) -> RoadmapDocument:
        if not self.path.exists():
            document = RoadmapDocument(project=self.project_name, tasks=[])
            self.write(document)
            return document
        document = self.parser.parse_file(self.path)
        self._ensure_meta(document.tasks)
        return document

    def write(self, document: RoadmapDocument) -> RoadmapDocument:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.parser.write(self.path, document)
        self._ensure_meta(document.tasks)
        return document

    def get_task(self, task_id: str) -> TaskInfo | None:
        for task in self.load().tasks:
            if task.id == task_id:
                return task
        return None

    def add_task(self, task: TaskInfo, index: int | None = None) -> RoadmapDocument:
        document = self.load()
        if any(existing.id == task.id for existing in document.tasks):
            raise ValueError(f"Task already exists: {task.id}")

        insertion_index = len(document.tasks) if index is None else max(0, min(index, len(document.tasks)))
        document.tasks.insert(insertion_index, task)
        written = self.write(document)

        meta = self._load_meta()
        meta.setdefault(task.id, {"definition_version": 1, "active_attempt_id": None})
        self._save_meta(meta)
        return written

    def update_task_definition(self, task_id: str, patch: dict[str, Any] | None = None, **kwargs: Any) -> TaskInfo:
        document = self.load()
        task = _require_task(document, task_id)
        allowed_fields = {
            "title",
            "acceptance_criteria",
            "branch",
            "prompt",
            "skills",
            "dependencies",
            "priority",
            "max_retries",
        }
        changed = False
        combined_patch = dict(patch or {})
        combined_patch.update({key: value for key, value in kwargs.items() if value is not None})
        for key, value in combined_patch.items():
            if key not in allowed_fields:
                continue
            setattr(task, key, value)
            changed = True

        if not changed:
            return task

        self.parser.validate_dependency_graph(document.tasks)
        self.write(document)

        meta = self._load_meta()
        entry = meta.setdefault(task_id, {"definition_version": 1, "active_attempt_id": None})
        entry["definition_version"] = int(entry.get("definition_version", 1)) + 1
        self._save_meta(meta)
        return _require_task(self.load(), task_id)

    def definition_version(self, task_id: str) -> int:
        meta = self._load_meta()
        entry = meta.get(task_id, {})
        value = entry.get("definition_version", 1)
        return value if isinstance(value, int) and value >= 1 else 1

    def active_attempt_id(self, task_id: str) -> str | None:
        meta = self._load_meta()
        value = meta.get(task_id, {}).get("active_attempt_id")
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return None

    def record_task_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        active_attempt_id: str | None = None,
        failure_reason: str | None = None,
    ) -> TaskInfo:
        document = self.load()
        task = _require_task(document, task_id)
        next_status = _STATE_TO_STATUS[state]
        if task.status is not next_status:
            if task.can_transition_to(next_status):
                task.transition_to(next_status, failure_reason=failure_reason)
            else:
                task.status = next_status
                task.failure_reason = failure_reason

        self.write(document)

        meta = self._load_meta()
        entry = meta.setdefault(task_id, {"definition_version": 1, "active_attempt_id": None})
        entry["active_attempt_id"] = active_attempt_id
        self._save_meta(meta)
        return _require_task(self.load(), task_id)

    def reorder_tasks(self, task_ids: list[str]) -> RoadmapDocument:
        document = self.load()
        by_id = {task.id: task for task in document.tasks}
        if set(task_ids) != set(by_id):
            raise ValueError("Reordered task ids must match the current roadmap exactly")
        document.tasks = [by_id[task_id] for task_id in task_ids]
        self.write(document)
        return document

    def _ensure_meta(self, tasks: list[TaskInfo]) -> None:
        meta = self._load_meta()
        task_ids = {task.id for task in tasks}
        changed = False

        for task_id in task_ids:
            if task_id not in meta:
                meta[task_id] = {"definition_version": 1, "active_attempt_id": None}
                changed = True

        stale_ids = [task_id for task_id in meta if task_id not in task_ids]
        for stale_id in stale_ids:
            meta.pop(stale_id, None)
            changed = True

        if changed or not self.meta_path.exists():
            self._save_meta(meta)

    def replace(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        document = RoadmapDocument(project=project or self.project_name, tasks=list(tasks))
        self.parser.validate_dependency_graph(document.tasks)
        return self.write(document)

    def build_task_prompt(self, *, task_id: str, consensus: ConsensusDocument | None) -> str:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        consensus_document = consensus or ConsensusDocument(project=self.project_name)
        return self.parser.build_task_prompt(task, consensus_document)

    def _load_meta(self) -> dict[str, dict[str, Any]]:
        raw = read_json(self.meta_path, default={})
        if not isinstance(raw, dict):
            return {}
        return {
            task_id: payload
            for task_id, payload in raw.items()
            if isinstance(task_id, str) and isinstance(payload, dict)
        }

    def _save_meta(self, meta: dict[str, dict[str, Any]]) -> None:
        write_json(self.meta_path, meta)


def _require_task(document: RoadmapDocument, task_id: str) -> TaskInfo:
    for task in document.tasks:
        if task.id == task_id:
            return task
    raise KeyError(f"Task not found: {task_id}")
