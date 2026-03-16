from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.tui.widgets.plan_tree import PlanTree
from vibrant.tui.widgets.task_status import TaskStatusView


class PlanTreeHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield PlanTree()


def _tasks_for_dependency_view() -> list[TaskInfo]:
    return [
        TaskInfo(id="task-a", title="Design schema"),
        TaskInfo(id="task-b", title="Build parser"),
        TaskInfo(
            id="task-c",
            title="Render roadmap",
            dependencies=["task-a", "task-b"],
            status=TaskStatus.IN_PROGRESS,
        ),
    ]


@pytest.mark.asyncio
async def test_plan_tree_flattens_dag_tasks_and_marks_dependencies() -> None:
    app = PlanTreeHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(PlanTree)
        widget.update_tasks(_tasks_for_dependency_view(), selected_task_id="task-c")
        await pilot.pause()

        tree = widget._tree
        assert tree is not None

        root_children = list(tree.root.children)
        assert [child.data.task.id for child in root_children] == ["task-a", "task-b", "task-c"]
        assert all(child.parent is tree.root for child in root_children)
        assert all(len(list(child.children)) == 0 for child in root_children)

        assert "[dependency]" in (widget.get_task_label("task-a") or "")
        assert "[dependency]" in (widget.get_task_label("task-b") or "")
        assert "[selected]" in (widget.get_task_label("task-c") or "")

        widget.select_task("task-a")
        await pilot.pause()

        assert "[selected]" in (widget.get_task_label("task-a") or "")
        assert "[dependent]" in (widget.get_task_label("task-c") or "")


@pytest.mark.asyncio
async def test_plan_tree_noop_refresh_preserves_existing_nodes() -> None:
    app = PlanTreeHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(PlanTree)
        widget.update_tasks(_tasks_for_dependency_view(), selected_task_id="task-c")
        await pilot.pause()

        original_node_ids = dict(widget._node_ids_by_task_id)

        widget.update_tasks(_tasks_for_dependency_view(), selected_task_id="task-c")
        await pilot.pause()

        assert widget._node_ids_by_task_id == original_node_ids


@pytest.mark.asyncio
async def test_plan_tree_updates_labels_in_place_when_task_changes() -> None:
    app = PlanTreeHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(PlanTree)
        widget.update_tasks(_tasks_for_dependency_view(), selected_task_id="task-c")
        await pilot.pause()

        original_node_id = widget._node_ids_by_task_id["task-c"]

        updated_tasks = [
            TaskInfo(id="task-a", title="Design schema"),
            TaskInfo(id="task-b", title="Build parser"),
            TaskInfo(
                id="task-c",
                title="Render refreshed roadmap",
                dependencies=["task-a", "task-b"],
                status=TaskStatus.ACCEPTED,
            ),
        ]
        widget.update_tasks(updated_tasks, selected_task_id="task-c")
        await pilot.pause()

        assert widget._node_ids_by_task_id["task-c"] == original_node_id
        task_c_label = widget.get_task_label("task-c") or ""
        assert "Render refreshed roadmap" in task_c_label
        assert "[accepted]" in task_c_label


def test_task_status_details_include_dependents() -> None:
    widget = TaskStatusView()
    widget.sync(_tasks_for_dependency_view(), selected_task_id="task-a")

    details = widget.get_task_details_text()

    assert "Dependencies: none" in details
    assert "Dependents: task-c" in details
