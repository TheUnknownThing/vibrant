"""Plan / task tree widget for the Phase 6 TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static, Tree
from textual.widgets.tree import TreeNode

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


TaskNodeRelation = Literal["default", "focused", "dependency", "dependent"]


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

    def __init__(self, **widget_kwargs: object) -> None:
        super().__init__(**widget_kwargs)
        self._tree: Tree[TaskTreeNodeData | None] | None = None
        self._tasks_by_id: dict[str, TaskInfo] = {}
        self._summaries_by_task_id: dict[str, str] = {}
        self._node_ids_by_task_id: dict[str, int] = {}
        self._task_order: tuple[str, ...] = ()
        self._visual_signatures_by_task_id: dict[str, tuple[tuple[TaskStatus, int | None, str], TaskNodeRelation]] = {}
        self._focused_task_id: str | None = None
        self._pending_tasks: tuple[TaskInfo, ...] = ()
        self._pending_selected_task_id: str | None = None
        self._pending_agent_summaries: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Static("[b]Plan / Tasks[/b]", id="plan-tree-header", markup=True)
        self._tree = Tree("Roadmap", id="plan-tree-widget")
        self._tree.show_root = False
        self._tree.guide_depth = 1
        self._tree.root.expand()
        yield self._tree

    def on_mount(self) -> None:
        if self._pending_tasks:
            self.update_tasks(
                list(self._pending_tasks),
                agent_summaries=self._pending_agent_summaries,
                selected_task_id=self._pending_selected_task_id,
            )
        else:
            self.clear_tasks()

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
        self._pending_tasks = tuple(tasks)
        self._pending_selected_task_id = selected_task_id
        self._pending_agent_summaries = dict(agent_summaries or {})
        if not tasks:
            self.clear_tasks()
            return

        task_order = tuple(task.id for task in tasks)
        structure_changed = task_order != self._task_order
        needs_rebuild = structure_changed or not self._node_ids_by_task_id

        if needs_rebuild:
            self._rebuild_tree(tasks)
        else:
            for task in tasks:
                self._update_task_node(task)

        self._task_order = task_order
        self._refresh_dependency_labels(selected_task_id)
        if selected_task_id:
            if needs_rebuild:
                self.call_after_refresh(self.select_task, selected_task_id)
            else:
                self.select_task(selected_task_id)

    def clear_tasks(self, message: str = "No roadmap tasks found.") -> None:
        """Clear the tree and show a single informational row."""

        self._tasks_by_id = {}
        self._summaries_by_task_id = {}
        self._pending_tasks = ()
        self._pending_selected_task_id = None
        self._pending_agent_summaries = {}
        self._task_order = ()
        self._visual_signatures_by_task_id = {}
        self._focused_task_id = None
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
        if self._tree.cursor_node is node:
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
        self._refresh_dependency_labels(data.task.id)
        self.post_message(self.TaskHighlighted(data.task))

    def on_tree_node_selected(self, event: Tree.NodeSelected[TaskTreeNodeData | None]) -> None:
        data = getattr(event.node, "data", None)
        if not isinstance(data, TaskTreeNodeData):
            return
        self._refresh_dependency_labels(data.task.id)
        self.post_message(self.TaskSelected(data.task))

    def _open_node_details(self, node: TreeNode[TaskTreeNodeData | None]) -> None:
        data = getattr(node, "data", None)
        if not isinstance(data, TaskTreeNodeData):
            return
        self.app.push_screen(TaskDetailScreen(data.task, agent_summary=data.agent_summary))

    def _rebuild_tree(self, tasks: list[TaskInfo]) -> None:
        if self._tree is None:
            return

        self._tree.root.remove_children()
        self._node_ids_by_task_id = {}
        self._visual_signatures_by_task_id = {}
        if not tasks:
            self._tree.root.add_leaf(Text("No roadmap tasks found.", style="dim"), data=None)
            self._tree.root.expand()
            return

        for task in tasks:
            self._add_task_node(self._tree.root, task)
        self._tree.root.expand()

    def _add_task_node(
        self,
        parent: TreeNode[TaskTreeNodeData | None],
        task: TaskInfo,
    ) -> None:
        summary = self._summaries_by_task_id.get(task.id)
        node = parent.add(
            self._render_task_label(task, relation="default"),
            data=TaskTreeNodeData(task=task, agent_summary=summary),
            expand=False,
            allow_expand=False,
        )
        self._node_ids_by_task_id[task.id] = node.id
        self._visual_signatures_by_task_id[task.id] = (self._task_label_signature(task), "default")

    def _update_task_node(self, task: TaskInfo) -> None:
        if self._tree is None:
            return
        node_id = self._node_ids_by_task_id.get(task.id)
        if node_id is None:
            return
        node = self._tree.get_node_by_id(node_id)
        if node is None:
            return
        summary = self._summaries_by_task_id.get(task.id)
        node.data = TaskTreeNodeData(task=task, agent_summary=summary)

    def _refresh_dependency_labels(self, focused_task_id: str | None) -> None:
        if self._tree is None:
            return

        dependency_ids, dependent_ids = self._dependency_sets(focused_task_id)
        self._focused_task_id = focused_task_id if focused_task_id in self._tasks_by_id else None

        for task_id in self._task_order:
            task = self._tasks_by_id.get(task_id)
            if task is None:
                continue
            relation = self._relation_for_task(
                task_id,
                focused_task_id=self._focused_task_id,
                dependency_ids=dependency_ids,
                dependent_ids=dependent_ids,
            )
            visual_signature = (self._task_label_signature(task), relation)
            if self._visual_signatures_by_task_id.get(task_id) == visual_signature:
                continue
            node_id = self._node_ids_by_task_id.get(task_id)
            if node_id is None:
                continue
            node = self._tree.get_node_by_id(node_id)
            if node is None:
                continue
            node.set_label(self._render_task_label(task, relation=relation))
            self._visual_signatures_by_task_id[task_id] = visual_signature

    def _dependency_sets(self, focused_task_id: str | None) -> tuple[set[str], set[str]]:
        if focused_task_id is None or focused_task_id not in self._tasks_by_id:
            return set(), set()

        focused_task = self._tasks_by_id[focused_task_id]
        dependency_ids = {dependency_id for dependency_id in focused_task.dependencies if dependency_id in self._tasks_by_id}
        dependent_ids = {
            task.id
            for task in self._tasks_by_id.values()
            if focused_task_id in task.dependencies
        }
        return dependency_ids, dependent_ids

    def _relation_for_task(
        self,
        task_id: str,
        *,
        focused_task_id: str | None,
        dependency_ids: set[str],
        dependent_ids: set[str],
    ) -> TaskNodeRelation:
        if focused_task_id is None:
            return "default"
        if task_id == focused_task_id:
            return "focused"
        if task_id in dependency_ids:
            return "dependency"
        if task_id in dependent_ids:
            return "dependent"
        return "default"

    def _render_task_label(self, task: TaskInfo, *, relation: TaskNodeRelation) -> Text:
        icon = STATUS_ICONS.get(task.status, "○")
        color = PRIORITY_STYLES.get(task.priority, "default")
        label = Text()
        label.append(f"{icon} ", style=color)
        label.append(task.id, style=self._task_id_style(color, relation))
        label.append(" — ")
        label.append(task.title, style=self._task_title_style(relation))
        if task.status is TaskStatus.IN_PROGRESS:
            label.append("  [running]", style="italic cyan")
        elif task.status is TaskStatus.FAILED:
            label.append("  [failed]", style="italic red")
        elif task.status is TaskStatus.ESCALATED:
            label.append("  [user]", style="italic red")
        elif task.status is TaskStatus.ACCEPTED:
            label.append("  [accepted]", style="italic green")
        if relation == "focused":
            label.append("  [selected]", style="bold cyan")
        elif relation == "dependency":
            label.append("  [dependency]", style="bold yellow")
        elif relation == "dependent":
            label.append("  [dependent]", style="bold green")
        return label

    @staticmethod
    def _task_label_signature(task: TaskInfo) -> tuple[TaskStatus, int | None, str]:
        return task.status, task.priority, task.title

    @staticmethod
    def _task_id_style(color: str, relation: TaskNodeRelation) -> str:
        if relation == "focused":
            return f"bold {color} reverse"
        if relation == "dependency":
            return f"bold underline {color}"
        if relation == "dependent":
            return f"bold italic {color}"
        return f"bold {color}"

    @staticmethod
    def _task_title_style(relation: TaskNodeRelation) -> str:
        if relation == "focused":
            return "bold"
        if relation == "dependency":
            return "yellow"
        if relation == "dependent":
            return "green"
        return "default"


def _format_priority(priority: int | None) -> str:
    return {
        0: "critical",
        1: "high",
        2: "medium",
        3: "low",
    }.get(priority, "low")
