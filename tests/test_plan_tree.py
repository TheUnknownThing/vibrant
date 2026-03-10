"""Tests for the Phase 6.1 plan tree widget and GUI wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Tree

from vibrant.consensus import RoadmapParser
from vibrant.models.state import GatekeeperStatus, OrchestratorState, OrchestratorStatus
from vibrant.models.task import TaskStatus
from vibrant.orchestrator import CodeAgentLifecycleResult
from vibrant.project_init import initialize_project
from vibrant.tui.app import VibrantApp
from vibrant.tui.widgets.plan_tree import PlanTree, TaskDetailScreen


SAMPLE_ROADMAP = """# Roadmap — Project Vibrant

### Task task-001 — Build the plan tree
- **Status**: pending
- **Priority**: high
- **Dependencies**: none
- **Skills**: ui
- **Branch**: vibrant/task-001
- **Prompt**: Render Panel A and show task details.
- **Retry Count**: 0
- **Max Retries**: 3

**Acceptance Criteria**:
- [ ] Show roadmap tasks in a tree
- [ ] Open a detail overlay

### Task task-002 — Refresh live updates
- **Status**: pending
- **Priority**: medium
- **Dependencies**: task-001
- **Skills**: none
- **Branch**: vibrant/task-002
- **Retry Count**: 0
- **Max Retries**: 3

**Acceptance Criteria**:
- [ ] Refresh on status changes
"""


class PlanTreeHarness(App):
    def __init__(self, tasks, *, summaries: dict[str, str] | None = None) -> None:
        super().__init__()
        self._tasks = tasks
        self._summaries = summaries or {}

    def compose(self) -> ComposeResult:
        yield PlanTree(id="plan")

    async def on_mount(self) -> None:
        self.query_one(PlanTree).update_tasks(self._tasks, agent_summaries=self._summaries)


class FakeLifecycle:
    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        self.project_root = Path(project_root)
        self.roadmap_path = self.project_root / ".vibrant" / "roadmap.md"
        self.on_canonical_event = on_canonical_event
        self.engine = SimpleNamespace(
            agents={},
            USER_INPUT_BANNER="⚠ Gatekeeper needs your input — see Chat panel",
            state=OrchestratorState(
                session_id="session-1",
                status=OrchestratorStatus.EXECUTING,
                gatekeeper_status=GatekeeperStatus.IDLE,
            ),
        )
        self._parser = RoadmapParser()

    def reload_from_disk(self):
        return self._parser.parse_file(self.roadmap_path)

    async def execute_next_task(self):
        roadmap = self._parser.parse_file(self.roadmap_path)
        task = roadmap.tasks[0]
        task.status = TaskStatus.IN_PROGRESS
        self._parser.write(self.roadmap_path, roadmap)
        if self.on_canonical_event is not None:
            await self.on_canonical_event({"type": "turn.started", "task_id": task.id})

        task.status = TaskStatus.ACCEPTED
        self._parser.write(self.roadmap_path, roadmap)
        if self.on_canonical_event is not None:
            await self.on_canonical_event({"type": "turn.completed", "task_id": task.id})

        return CodeAgentLifecycleResult(
            task_id=task.id,
            outcome="accepted",
            task_status=task.status,
        )


def _task_label(app: App, task_id: str) -> str:
    return app.query_one(PlanTree).get_task_label(task_id) or ""


def _write_roadmap(repo: Path) -> None:
    RoadmapParser().write(repo / ".vibrant" / "roadmap.md", RoadmapParser().parse(SAMPLE_ROADMAP))


async def _wait_for(assertion, pilot, *, attempts: int = 10) -> None:
    for _ in range(attempts):
        if assertion():
            return
        await pilot.pause()
    raise AssertionError("Timed out waiting for plan tree update")


@pytest.mark.asyncio
async def test_plan_tree_displays_icons_and_priority_styling():
    tasks = RoadmapParser().parse(SAMPLE_ROADMAP).tasks
    tasks[0].status = TaskStatus.IN_PROGRESS
    tasks[1].status = TaskStatus.FAILED

    app = PlanTreeHarness(tasks)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        first_node = tree.root.children[0]
        second_node = first_node.children[0]

        assert _task_label(app, "task-001").startswith("⟳ task-001")
        assert _task_label(app, "task-002").startswith("✗ task-002")
        assert first_node.label.spans[0].style == "dark_orange3"
        assert second_node.label.spans[0].style == "yellow3"


def test_task_detail_screen_renders_overlay_content():
    task = RoadmapParser().parse(SAMPLE_ROADMAP).tasks[0]
    screen = TaskDetailScreen(task, agent_summary="Implemented the panel and tests.")

    rendered = screen._render_markdown()

    assert "Build the plan tree" in rendered
    assert "Render Panel A and show task details." in rendered
    assert "Implemented the panel and tests." in rendered
    assert "Show roadmap tasks in a tree" in rendered


@pytest.mark.asyncio
async def test_plan_tree_live_updates_when_task_status_changes():
    tasks = RoadmapParser().parse(SAMPLE_ROADMAP).tasks
    app = PlanTreeHarness(tasks)

    async with app.run_test() as pilot:
        assert _task_label(app, "task-001").startswith("○ task-001")

        updated_tasks = RoadmapParser().parse(SAMPLE_ROADMAP).tasks
        updated_tasks[0].status = TaskStatus.ACCEPTED
        app.query_one(PlanTree).update_tasks(updated_tasks)

        assert _task_label(app, "task-001").startswith("✓ task-001")


@pytest.mark.asyncio
async def test_app_wires_plan_tree_and_run_next_task_into_gui(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)
    _write_roadmap(repo)

    app = VibrantApp(cwd=str(repo), lifecycle_factory=FakeLifecycle)
    async with app.run_test() as pilot:
        assert _task_label(app, "task-001").startswith("○ task-001")

        await pilot.press("f6")
        await _wait_for(
            lambda: _task_label(app, "task-001").startswith("✓ task-001")
            and not app._task_execution_in_progress  # noqa: SLF001 - verify runner cleanup
            and app._roadmap_runner_task is None,  # noqa: SLF001 - verify runner cleanup
            pilot,
        )
        await pilot.pause()

        assert _task_label(app, "task-001").startswith("✓ task-001")
        roadmap = RoadmapParser().parse_file(repo / ".vibrant" / "roadmap.md")
        assert roadmap.tasks[0].status is TaskStatus.ACCEPTED
