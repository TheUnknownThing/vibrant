"""Standalone preview for the vibing task-status panel."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding

from vibrant.models.task import TaskInfo
from vibrant.tui.screens.vibing import VibingScreen


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "task_status_preview.json"
_DATETIME_KEYS = {"started_at", "finished_at"}


class _InstancesAPI:
    def __init__(
        self,
        instances_by_task: dict[str, list[SimpleNamespace]],
        outputs_by_agent: dict[str, SimpleNamespace],
    ) -> None:
        self._instances_by_task = instances_by_task
        self._outputs_by_agent = outputs_by_agent

    def active(self) -> list[SimpleNamespace]:
        active: list[SimpleNamespace] = []
        for instances in self._instances_by_task.values():
            active.extend(instance for instance in instances if instance.runtime.active)
        return active

    def list(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[SimpleNamespace]:
        if task_id is None:
            instances = [instance for task_instances in self._instances_by_task.values() for instance in task_instances]
        else:
            instances = list(self._instances_by_task.get(task_id, ()))

        if role is not None:
            instances = [instance for instance in instances if instance.role == role]
        if active_only:
            instances = [instance for instance in instances if instance.runtime.active]
        if not include_completed:
            instances = [instance for instance in instances if not instance.runtime.done]
        return instances

    def output(self, agent_id: str) -> SimpleNamespace | None:
        return self._outputs_by_agent.get(agent_id)


class _RunsAPI:
    def __init__(
        self,
        runs_by_task: dict[str, SimpleNamespace],
        events_by_run: dict[str, list[dict[str, Any]]],
    ) -> None:
        self._runs_by_task = runs_by_task
        self._events_by_run = events_by_run

    def latest_for_task(self, task_id: str, *, role: str | None = None) -> SimpleNamespace | None:
        run = self._runs_by_task.get(task_id)
        if run is None:
            return None
        if role is not None and getattr(run, "role", None) != role:
            return None
        return run

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return list(self._events_by_run.get(run_id, ()))


class PreviewFacade(SimpleNamespace):
    """Small stable-surface stub for the task-status preview."""

    def get_consensus_document(self) -> None:
        return None

    def get_consensus_source_path(self) -> None:
        return None


class TaskStatusPreviewApp(App[None]):
    """Preview app that mounts the real vibing screen with fixture data."""

    TITLE = "Vibrant Task Status Preview"
    SUB_TITLE = "Fixture-backed frontend validation"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, *, fixture_path: Path = FIXTURE_PATH) -> None:
        super().__init__()
        self._fixture_path = fixture_path
        self._tasks: list[TaskInfo] = []
        self._agent_summaries: dict[str, str] = {}
        self._facade = PreviewFacade()
        self.orchestrator_facade: PreviewFacade | None = None

    def compose(self) -> ComposeResult:
        yield VibingScreen(initial_tab="task-status")

    def on_mount(self) -> None:
        self.theme = "catppuccin-mocha"
        self._tasks, self._agent_summaries, self._facade = _load_preview_fixture(self._fixture_path)
        self.orchestrator_facade = self._facade
        screen = self.query_one(VibingScreen)
        screen.sync_task_views(self._tasks, facade=self._facade, agent_summaries=self._agent_summaries)
        screen.set_roadmap_loading(False)
        screen.set_input_placeholder("Preview mode. Click a task in the left panel to inspect it.")
        screen.plan_tree.focus()


def _load_preview_fixture(path: Path) -> tuple[list[TaskInfo], dict[str, str], PreviewFacade]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = [TaskInfo.model_validate(task) for task in payload.get("tasks", ())]
    agent_summaries = {
        str(task_id): str(summary)
        for task_id, summary in dict(payload.get("agent_summaries", {})).items()
    }
    instances_by_task = {
        str(task_id): [_materialize(instance) for instance in instances]
        for task_id, instances in dict(payload.get("instances_by_task", {})).items()
    }
    outputs_by_agent = {
        str(agent_id): _materialize(output)
        for agent_id, output in dict(payload.get("outputs_by_agent", {})).items()
    }
    runs_by_task = {
        str(task_id): _materialize(run)
        for task_id, run in dict(payload.get("runs_by_task", {})).items()
    }
    events_by_run = {
        str(run_id): [_materialize_event(event) for event in events]
        for run_id, events in dict(payload.get("events_by_run", {})).items()
    }
    facade = PreviewFacade(
        instances=_InstancesAPI(instances_by_task, outputs_by_agent),
        runs=_RunsAPI(runs_by_task, events_by_run),
    )
    return tasks, agent_summaries, facade


def _materialize(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{name: _materialize(item, key=name) for name, item in value.items()})
    if isinstance(value, list):
        return [_materialize(item, key=key) for item in value]
    if key in _DATETIME_KEYS and isinstance(value, str) and value:
        return _parse_datetime(value)
    return value


def _materialize_event(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {name: _materialize_event(item, key=name) for name, item in value.items()}
    if isinstance(value, list):
        return [_materialize_event(item, key=key) for item in value]
    if key in _DATETIME_KEYS and isinstance(value, str) and value:
        return _parse_datetime(value)
    return value


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


if __name__ == "__main__":
    TaskStatusPreviewApp().run()
