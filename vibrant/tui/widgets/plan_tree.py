"""Plan / task tree widget for the Phase 6 TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static, Tree

from ...models.task import TaskInfo, TaskStatus


STATUS_ICONS = {
    TaskStatus.PENDING: "○",
    TaskStatus.QUEUED: "○",
    TaskStatus.IN_PROGRESS: "⟳",
    TaskStatus.COMPLETED: "✓",
    TaskStatus.ACCEPTED: "✓",
    TaskStatus.FAILED: "✗",
    TaskStatus.ESCALATED: "✗",
}

PRIORITY_STYLES = {
    0: "red",
    1: "dark_orange3",
    2: "yellow3",
}


@dataclass(slots=True)
class TaskTreeNodeData:
    """Payload attached to a tree node representing a roadmap task."""

    task: TaskInfo
    agent_summary: str | None = None


class TaskDetailScreen(ModalScreen[None]):
    """Modal overlay showing one task's full details."""

    CSS = """
    TaskDetailScreen {
        align: center middle;
    }

    #task-detail-modal {
        width: 70%;
        height: 75%;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }

    #task-detail-body {
        height: 1fr;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "close_modal", "Close"),
        Binding("enter", "close_modal", "Close", show=False),
    ]

    def __init__(self, task: TaskInfo, *, agent_summary: str | None = None) -> None:
        super().__init__(id="task-detail-screen")
        self._task_info = task
        self._agent_summary = agent_summary

    def compose(self) -> ComposeResult:
        yield Markdown(self._render_markdown(), id="task-detail-modal")

    def action_close_modal(self) -> None:
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)

    def _render_markdown(self) -> str:
        acceptance_lines = (
            "\n".join(f"- [ ] {criterion}" for criterion in self._task_info.acceptance_criteria)
            if self._task_info.acceptance_criteria
            else "- [ ] No acceptance criteria defined"
        )
        dependencies = ", ".join(self._task_info.dependencies) if self._task_info.dependencies else "none"
        skills = ", ".join(self._task_info.skills) if self._task_info.skills else "none"
        branch = self._task_info.branch or f"vibrant/{self._task_info.id}"
        prompt = self._task_info.prompt or "No prompt recorded."
        summary = self._agent_summary or "No agent summary recorded yet."
        priority = _format_priority(self._task_info.priority)

        return "\n".join(
            [
                f"# {self._task_info.id} — {self._task_info.title}",
                f"**Status**: `{self._task_info.status.value}`",
                f"**Priority**: `{priority}`",
                f"**Branch**: `{branch}`",
                f"**Dependencies**: `{dependencies}`",
                f"**Skills**: `{skills}`",
                "",
                "## Prompt",
                prompt,
                "",
                "## Acceptance Criteria",
                acceptance_lines,
                "",
                "## Agent Summary",
                summary,
            ]
        )


class PlanTree(Static):
    """Tree view of roadmap tasks with detail overlays and live refresh support."""

    class TaskHighlighted(Message):
        """Raised when the tree cursor moves onto a task node."""

        def __init__(self, task: TaskInfo) -> None:
            super().__init__()
            self.task = task

    class TaskSelected(Message):
        """Raised when the user explicitly selects a task node."""

        def __init__(self, task: TaskInfo) -> None:
            super().__init__()
            self.task = task

    BINDINGS = [
        Binding("enter", "open_selected_task", "Details", show=False),
    ]

    DEFAULT_CSS = """
    PlanTree {
        border: round $primary-background;
        background: $surface;
        padding: 0;
    }

    #plan-tree-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-align: center;
    }

    #plan-tree-widget {
        height: 1fr;
        padding: 0 1 1 1;
        margin: 1 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tree: Tree[TaskTreeNodeData | None] | None = None
        self._tasks_by_id: dict[str, TaskInfo] = {}
        self._summaries_by_task_id: dict[str, str] = {}
        self._node_ids_by_task_id: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield Static("[b]Plan / Tasks[/b]", id="plan-tree-header", markup=True)
        self._tree = Tree("Roadmap", id="plan-tree-widget")
        self._tree.show_root = False
        self._tree.guide_depth = 2
        self._tree.root.expand()
        yield self._tree

    def update_tasks(
        self,
        tasks: list[TaskInfo],
        *,
        agent_summaries: dict[str, str] | None = None,
        selected_task_id: str | None = None,
    ) -> None:
        """Refresh the tree from the latest roadmap tasks."""

        self._tasks_by_id = {task.id: task for task in tasks}
        self._summaries_by_task_id = dict(agent_summaries or {})
        self._rebuild_tree(tasks)
        if selected_task_id:
            self.select_task(selected_task_id)

    def clear_tasks(self, message: str = "No roadmap tasks found.") -> None:
        """Clear the tree and show a single informational row."""

        self._tasks_by_id = {}
        self._summaries_by_task_id = {}
        if self._tree is None:
            return
        self._tree.root.remove_children()
        self._tree.root.add_leaf(Text(message, style="dim"), data=None)
        self._tree.root.expand()
        self._node_ids_by_task_id = {}

    def action_open_selected_task(self) -> None:
        node = self._tree.cursor_node if self._tree is not None else None
        if node is None:
            return
        self._open_node_details(node)

    def open_task_details(self, task_id: str) -> None:
        """Open the modal overlay for a specific task id."""

        task = self._tasks_by_id.get(task_id)
        if task is None:
            return
        self.app.push_screen(TaskDetailScreen(task, agent_summary=self._summaries_by_task_id.get(task_id)))

    def select_task(self, task_id: str) -> None:
        """Move the tree cursor to a specific task id."""

        if self._tree is None:
            return
        node_id = self._node_ids_by_task_id.get(task_id)
        if node_id is None:
            return
        node = self._tree.get_node_by_id(node_id)
        if node is None:
            return
        self._tree.move_cursor(node, animate=False)

    def get_task_label(self, task_id: str) -> str | None:
        """Return the plain-text label for a task, for testing and diagnostics."""

        if self._tree is None:
            return None
        node_id = self._node_ids_by_task_id.get(task_id)
        if node_id is None:
            return None
        node = self._tree.get_node_by_id(node_id)
        if node is None:
            return None
        label = node.label
        return label.plain if isinstance(label, Text) else str(label)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[TaskTreeNodeData | None]) -> None:
        data = getattr(event.node, "data", None)
        if not isinstance(data, TaskTreeNodeData):
            return
        self.post_message(self.TaskHighlighted(data.task))

    def on_tree_node_selected(self, event: Tree.NodeSelected[TaskTreeNodeData | None]) -> None:
        data = getattr(event.node, "data", None)
        if not isinstance(data, TaskTreeNodeData):
            return
        self.post_message(self.TaskSelected(data.task))

    def _open_node_details(self, node: Any) -> None:
        data = getattr(node, "data", None)
        if not isinstance(data, TaskTreeNodeData):
            return
        self.app.push_screen(TaskDetailScreen(data.task, agent_summary=data.agent_summary))

    def _rebuild_tree(self, tasks: list[TaskInfo]) -> None:
        if self._tree is None:
            return

        self._tree.root.remove_children()
        self._node_ids_by_task_id = {}
        if not tasks:
            self._tree.root.add_leaf(Text("No roadmap tasks found.", style="dim"), data=None)
            self._tree.root.expand()
            return

        task_map = {task.id: task for task in tasks}
        children_map: dict[str, list[TaskInfo]] = {task.id: [] for task in tasks}
        roots: list[TaskInfo] = []

        for task in tasks:
            parent_id = task.dependencies[0] if task.dependencies else None
            if parent_id and parent_id in children_map:
                children_map[parent_id].append(task)
            else:
                roots.append(task)

        for task in roots:
            self._add_task_node(self._tree.root, task, children_map)
        self._tree.root.expand()

    def _add_task_node(
        self,
        parent: Any,
        task: TaskInfo,
        children_map: dict[str, list[TaskInfo]],
    ) -> None:
        summary = self._summaries_by_task_id.get(task.id)
        node = parent.add(
            self._render_task_label(task),
            data=TaskTreeNodeData(task=task, agent_summary=summary),
            expand=bool(children_map.get(task.id)),
            allow_expand=bool(children_map.get(task.id)),
        )
        self._node_ids_by_task_id[task.id] = node.id

        for child in children_map.get(task.id, []):
            self._add_task_node(node, child, children_map)

    def _render_task_label(self, task: TaskInfo) -> Text:
        icon = STATUS_ICONS.get(task.status, "○")
        color = PRIORITY_STYLES.get(task.priority, "default")
        label = Text()
        label.append(f"{icon} ", style=color)
        label.append(task.id, style=f"bold {color}")
        label.append(" — ")
        label.append(task.title)
        if task.status is TaskStatus.IN_PROGRESS:
            label.append("  [running]", style="italic cyan")
        elif task.status is TaskStatus.FAILED:
            label.append("  [failed]", style="italic red")
        elif task.status is TaskStatus.ESCALATED:
            label.append("  [user]", style="italic red")
        elif task.status is TaskStatus.ACCEPTED:
            label.append("  [accepted]", style="italic green")
        return label


def _format_priority(priority: int | None) -> str:
    return {
        0: "critical",
        1: "high",
        2: "medium",
        3: "low",
    }.get(priority, "low")
