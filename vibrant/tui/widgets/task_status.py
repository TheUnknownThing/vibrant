"""Task-status panel for the vibing screen."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import ProgressBar, Static

from ...agents.utils import extract_error_message, extract_text_from_progress_item
from ...models.task import TaskInfo, TaskStatus
from ...providers.base import CanonicalEvent
from ...type_defs import JSONValue

if TYPE_CHECKING:
    from ...orchestrator import OrchestratorFacade


@dataclass(slots=True)
class _RoadmapProgress:
    total: int = 0
    accepted: int = 0
    completed: int = 0
    running: int = 0
    queued: int = 0
    pending: int = 0
    failed: int = 0
    escalated: int = 0

    @property
    def finished(self) -> int:
        return self.accepted + self.completed


@dataclass(slots=True)
class _ExecutionSnapshot:
    active_instance: object | None = None
    latest_instance: object | None = None
    latest_run: object | None = None
    output: object | None = None
    thinking_text: str | None = None
    thinking_status: str = "idle"
    live_response: str | None = None
    recent_activity: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _RunEventCacheEntry:
    canonical_event_log: str
    file_size: int
    mtime_ns: int
    events: list[CanonicalEvent]


class TaskStatusView(Static):
    """Display roadmap progress and details for the selected task."""

    DEFAULT_CSS = """
    TaskStatusView {
        height: 1fr;
        border: round $primary-background;
        background: $surface;
        padding: 0;
    }

    #task-status-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-style: bold;
    }

    #task-status-state {
        height: auto;
        padding: 0 1 1 1;
        background: $primary-background;
        color: $text-muted;
    }

    #task-status-empty-state {
        height: 1fr;
        align: center middle;
        padding: 2;
    }

    #task-status-empty-message {
        width: 1fr;
        content-align: center middle;
        text-align: center;
        color: $text-muted;
    }

    #task-status-body {
        height: 1fr;
    }

    #task-status-progress-region {
        height: auto;
        padding: 1 1 0 1;
    }

    #task-status-progress-copy {
        height: auto;
        padding: 0 1 1 1;
        color: $text-muted;
        border-bottom: solid $primary-background;
    }

    #task-status-scroll {
        height: 1fr;
        padding: 1;
        scrollbar-size: 1 1;
    }

    .task-status-section {
        height: auto;
        margin-bottom: 1;
        padding: 1;
        border: round $primary-background;
        background: $surface;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._facade: OrchestratorFacade | None = None
        self._is_loading = True
        self._empty_message = "Task status will appear here once the roadmap is ready."
        self._tasks: tuple[TaskInfo, ...] = ()
        self._tasks_by_id: dict[str, TaskInfo] = {}
        self._selected_task_id: str | None = None
        self._state_text = ""
        self._progress_summary_text = ""
        self._task_details_text = ""
        self._execution_text = ""
        self._activity_text = ""
        self._run_event_cache: dict[str, _RunEventCacheEntry] = {}

    def compose(self) -> ComposeResult:
        yield Static("Task Status", id="task-status-header")
        yield Static("", id="task-status-state")
        with Vertical(id="task-status-empty-state"):
            yield Static("", id="task-status-empty-message")
        with Vertical(id="task-status-body"):
            with Vertical(id="task-status-progress-region"):
                yield ProgressBar(total=1, show_percentage=False, show_eta=False, id="task-status-progress-bar")
            yield Static("", id="task-status-progress-copy")
            with VerticalScroll(id="task-status-scroll"):
                yield Static("", id="task-status-task-details", classes="task-status-section", markup=False)
                yield Static("", id="task-status-execution-details", classes="task-status-section", markup=False)
                yield Static("", id="task-status-activity-details", classes="task-status-section", markup=False)

    def on_mount(self) -> None:
        self._refresh_view()

    @property
    def selected_task_id(self) -> str | None:
        """Return the currently selected task id."""

        return self._selected_task_id

    def bind(self, facade: OrchestratorFacade | None) -> None:
        """Bind the panel to the current orchestrator facade."""

        self._facade = facade
        if self._tasks and not self._is_loading:
            self._refresh_view()

    def sync(self, tasks: Sequence[TaskInfo], *, selected_task_id: str | None = None) -> str | None:
        """Refresh the panel from the latest roadmap tasks."""

        self._is_loading = False
        self._tasks = tuple(tasks)
        self._tasks_by_id = {task.id: task for task in self._tasks}
        self._selected_task_id = self._resolve_selected_task_id(selected_task_id)
        if not self._tasks:
            self._empty_message = "No roadmap tasks found."
        self._refresh_view()
        return self._selected_task_id

    def clear_tasks(self, message: str = "No roadmap tasks found.") -> None:
        """Clear the panel and show a contextual empty state."""

        self._is_loading = False
        self._tasks = ()
        self._tasks_by_id = {}
        self._selected_task_id = None
        self._empty_message = message
        self._refresh_view()

    def set_generating_roadmap(self, is_loading: bool) -> None:
        """Toggle the roadmap-loading placeholder."""

        self._is_loading = is_loading
        if is_loading:
            self._empty_message = "Task status will appear here once the roadmap is ready."
        self._refresh_view()

    def select_task(self, task_id: str | None) -> str | None:
        """Select one task by id and refresh the panel."""

        if task_id is None or task_id not in self._tasks_by_id:
            return self._selected_task_id
        if self._selected_task_id == task_id:
            return self._selected_task_id
        self._selected_task_id = task_id
        self._refresh_view()
        return self._selected_task_id

    def get_progress_summary_text(self) -> str:
        """Return the rendered progress summary, for tests and previews."""

        return self._progress_summary_text

    def get_task_details_text(self) -> str:
        """Return the rendered task-details block, for tests and previews."""

        return self._task_details_text

    def get_execution_text(self) -> str:
        """Return the rendered execution-details block, for tests and previews."""

        return self._execution_text

    def get_activity_text(self) -> str:
        """Return the rendered recent-activity block, for tests and previews."""

        return self._activity_text

    def _refresh_view(self) -> None:
        if self._is_loading:
            self._state_text = "Generating roadmap"
            self._progress_summary_text = ""
            self._task_details_text = ""
            self._execution_text = ""
            self._activity_text = ""
            self._show_empty_state(self._empty_message)
            return

        if not self._tasks:
            self._state_text = "No tasks available"
            self._progress_summary_text = ""
            self._task_details_text = ""
            self._execution_text = ""
            self._activity_text = ""
            self._show_empty_state(self._empty_message)
            return

        task = self._selected_task()
        if task is None:
            self._state_text = "No task selected"
            self._progress_summary_text = ""
            self._task_details_text = ""
            self._execution_text = ""
            self._activity_text = ""
            self._show_empty_state("Select a task from the task list to inspect its execution state.")
            return

        progress = _build_progress(self._tasks)
        execution = self._execution_snapshot(task)

        self._state_text = f"Selected task: {task.id}"
        self._progress_summary_text = _render_progress_summary(progress)
        self._task_details_text = _render_task_details(task, progress, self._tasks)
        self._execution_text = _render_execution_details(task, execution)
        self._activity_text = _render_recent_activity(task, execution)
        self._show_task_body(progress)

    def _show_empty_state(self, message: str) -> None:
        if not self.is_mounted:
            return

        self.query_one("#task-status-state", Static).update(self._state_text)
        self.query_one("#task-status-empty-message", Static).update(message)
        self.query_one("#task-status-empty-state", Vertical).display = True
        self.query_one("#task-status-body", Vertical).display = False

    def _show_task_body(self, progress: _RoadmapProgress) -> None:
        if not self.is_mounted:
            return

        self.query_one("#task-status-state", Static).update(self._state_text)
        self.query_one("#task-status-empty-state", Vertical).display = False
        self.query_one("#task-status-body", Vertical).display = True

        progress_bar = self.query_one("#task-status-progress-bar", ProgressBar)
        progress_bar.update(total=max(progress.total, 1), progress=progress.finished)

        self.query_one("#task-status-progress-copy", Static).update(self._progress_summary_text)
        self.query_one("#task-status-task-details", Static).update(self._task_details_text)
        self.query_one("#task-status-execution-details", Static).update(self._execution_text)
        self.query_one("#task-status-activity-details", Static).update(self._activity_text)

    def _resolve_selected_task_id(self, preferred_task_id: str | None) -> str | None:
        if preferred_task_id in self._tasks_by_id:
            return preferred_task_id
        if self._selected_task_id in self._tasks_by_id:
            return self._selected_task_id

        active_task_id = self._active_task_id()
        if active_task_id is not None:
            return active_task_id

        for status in (
            TaskStatus.IN_PROGRESS,
            TaskStatus.QUEUED,
            TaskStatus.PENDING,
            TaskStatus.FAILED,
            TaskStatus.COMPLETED,
            TaskStatus.ACCEPTED,
            TaskStatus.ESCALATED,
        ):
            for task in self._tasks:
                if task.status is status:
                    return task.id

        return self._tasks[0].id if self._tasks else None

    def _active_task_id(self) -> str | None:
        facade = self._facade
        if facade is None:
            return None

        try:
            active_instances = facade.list_instances(active_only=True)
        except Exception:
            return None

        for instance in active_instances:
            task_id = _instance_task_id(instance)
            if task_id in self._tasks_by_id:
                return task_id
        return None

    def _selected_task(self) -> TaskInfo | None:
        if self._selected_task_id is None:
            return None
        return self._tasks_by_id.get(self._selected_task_id)

    def _execution_snapshot(self, task: TaskInfo) -> _ExecutionSnapshot:
        snapshot = _ExecutionSnapshot()
        facade = self._facade
        if facade is None:
            return snapshot

        instances = _list_task_instances(facade, task.id)
        snapshot.active_instance = next((instance for instance in instances if _instance_active(instance)), None)
        snapshot.latest_instance = snapshot.active_instance or _latest_instance(instances)
        snapshot.latest_run = _latest_run_for_task(facade, task.id)

        output_agent_id = (
            _instance_agent_id(snapshot.active_instance)
            or _instance_agent_id(snapshot.latest_instance)
            or _run_agent_id(snapshot.latest_run)
        )
        snapshot.output = _resolve_agent_output(facade, output_agent_id, snapshot.active_instance, snapshot.latest_instance)

        events = self._events_for_run(snapshot.latest_run, snapshot.latest_instance)

        thinking_text, thinking_status = _thinking_from_events(events)
        output_thinking = _agent_output_thinking(snapshot.output)
        if output_thinking:
            thinking_text = output_thinking
            thinking_status = _agent_output_thinking_status(snapshot.output)

        snapshot.thinking_text = thinking_text or None
        snapshot.thinking_status = thinking_status
        live_response = _agent_output_partial_text(snapshot.output)
        if live_response:
            snapshot.live_response = live_response
        snapshot.recent_activity = _recent_activity_from_events(events)
        return snapshot

    def _events_for_run(self, run: object | None, instance: object | None) -> list[CanonicalEvent]:
        facade = self._facade
        if facade is None:
            return []

        run_id = _run_id(run)
        canonical_event_log = _run_canonical_event_log(run) or _instance_canonical_event_log(instance)
        file_stats = self._canonical_event_log_stats(canonical_event_log)
        if run_id is not None:
            cached = self._run_event_cache.get(run_id)
            if (
                cached is not None
                and file_stats is not None
                and cached.canonical_event_log == canonical_event_log
                and cached.file_size == file_stats[0]
                and cached.mtime_ns == file_stats[1]
            ):
                return cached.events

        events = _read_canonical_event_log(canonical_event_log) if canonical_event_log else []

        if file_stats is not None and canonical_event_log and run_id is not None:
            self._run_event_cache[run_id] = _RunEventCacheEntry(
                canonical_event_log=canonical_event_log,
                file_size=file_stats[0],
                mtime_ns=file_stats[1],
                events=events,
            )
        return events

    @staticmethod
    def _canonical_event_log_stats(canonical_event_log: str | None) -> tuple[int, int] | None:
        if not canonical_event_log:
            return None
        try:
            stats = Path(canonical_event_log).stat()
        except OSError:
            return None
        return stats.st_size, stats.st_mtime_ns


def _list_task_instances(facade: OrchestratorFacade, task_id: str) -> list[object]:
    try:
        instances = facade.list_instances()
    except Exception:
        return []
    return [instance for instance in instances if _instance_task_id(instance) == task_id]


def _latest_run_for_task(facade: OrchestratorFacade, task_id: str) -> object | None:
    try:
        runs = facade.list_runs(task_id=task_id)
    except Exception:
        return None
    return runs[-1] if runs else None


def _resolve_agent_output(
    facade: object,
    agent_id: str | None,
    active_instance: object | None,
    latest_instance: object | None,
) -> object | None:
    for instance in (active_instance, latest_instance):
        output = _instance_output(instance)
        if output is not None:
            return output

    if agent_id is None:
        return None

    agent_output = getattr(facade, "agent_output", None)
    if callable(agent_output):
        try:
            output = agent_output(agent_id)
        except Exception:
            output = None
        if output is not None:
            return output

    if hasattr(facade, "instances"):
        output_for_agent = getattr(facade.instances, "output", None)
        if callable(output_for_agent):
            try:
                return output_for_agent(agent_id)
            except Exception:
                return None

    return None


def _build_progress(tasks: Sequence[TaskInfo]) -> _RoadmapProgress:
    progress = _RoadmapProgress(total=len(tasks))
    for task in tasks:
        if task.status is TaskStatus.ACCEPTED:
            progress.accepted += 1
        elif task.status is TaskStatus.COMPLETED:
            progress.completed += 1
        elif task.status is TaskStatus.IN_PROGRESS:
            progress.running += 1
        elif task.status is TaskStatus.QUEUED:
            progress.queued += 1
        elif task.status is TaskStatus.PENDING:
            progress.pending += 1
        elif task.status is TaskStatus.FAILED:
            progress.failed += 1
        elif task.status is TaskStatus.ESCALATED:
            progress.escalated += 1
    return progress


def _render_progress_summary(progress: _RoadmapProgress) -> str:
    lines = [
        f"Roadmap progress: {progress.finished} of {progress.total} tasks finished",
        (
            f"Accepted {progress.accepted} | Awaiting review {progress.completed} | "
            f"Running {progress.running} | Queued {progress.queued} | Pending {progress.pending}"
        ),
    ]
    if progress.failed or progress.escalated:
        lines.append(f"Failed {progress.failed} | Escalated {progress.escalated}")
    return "\n".join(lines)


def _render_task_details(task: TaskInfo, progress: _RoadmapProgress, tasks: Sequence[TaskInfo]) -> str:
    position = next((index for index, candidate in enumerate(tasks, start=1) if candidate.id == task.id), 1)
    dependents = [candidate.id for candidate in tasks if task.id in candidate.dependencies]
    lines = [
        "Selected Task",
        f"{task.id} - {task.title}",
        "",
        f"Roadmap position: {position} / {max(progress.total, 1)}",
        f"Status: {task.status.value}",
        f"Role: {task.agent_role or 'unassigned'}",
        f"Priority: {_format_priority(task.priority)}",
        f"Branch: {task.branch or f'vibrant/{task.id}'}",
        f"Retries: {task.retry_count} / {task.max_retries}",
        f"Dependencies: {', '.join(task.dependencies) if task.dependencies else 'none'}",
        f"Dependents: {', '.join(dependents) if dependents else 'none'}",
        f"Skills: {', '.join(task.skills) if task.skills else 'none'}",
    ]

    if task.failure_reason:
        lines.append(f"Failure reason: {task.failure_reason}")

    lines.extend(["", "Acceptance Criteria"])
    if task.acceptance_criteria:
        lines.extend(f"- {criterion}" for criterion in task.acceptance_criteria)
    else:
        lines.append("- No acceptance criteria defined")

    if task.prompt:
        lines.extend(["", "Prompt Preview", _indent_block(_truncate_block(task.prompt, max_lines=5))])

    return "\n".join(lines)


def _render_execution_details(task: TaskInfo, execution: _ExecutionSnapshot) -> str:
    active_or_latest = execution.active_instance or execution.latest_instance
    run = execution.latest_run

    lines = ["Execution"]
    if active_or_latest is None and run is None:
        lines.append(f"State: {_humanize_state(task.status.value)}")
        lines.append(_idle_execution_message(task))
        if task.failure_reason and task.status is TaskStatus.FAILED:
            lines.append(f"Failure reason: {task.failure_reason}")
        return "\n".join(lines)

    state = _instance_state(active_or_latest) or _run_state(run)
    status = _instance_status(active_or_latest) or _run_status(run) or task.status.value
    started_at = _instance_started_at(active_or_latest) or _run_started_at(run)
    finished_at = _instance_finished_at(active_or_latest) or _run_finished_at(run)
    agent_id = _instance_agent_id(active_or_latest) or _run_agent_id(run)
    role = _instance_role(active_or_latest) or getattr(task, "agent_role", None)
    worktree_path = _instance_worktree_path(active_or_latest) or _run_worktree_path(run)
    thread_id = _instance_thread_id(active_or_latest) or _run_thread_id(run)
    awaiting_input = _instance_awaiting_input(active_or_latest) or _run_awaiting_input(run)
    summary = _instance_summary(active_or_latest) or _run_summary(run)
    error = _instance_error(active_or_latest) or _run_error(run)

    lines.extend(
        [
            f"State: {_humanize_state(state or status)}",
            f"Agent: {agent_id or 'not assigned'} ({role or 'unassigned'})",
        ]
    )

    run_id = _run_id(run)
    if run_id:
        lines.append(f"Run: {run_id}")
    if started_at is not None:
        lines.append(f"Started: {_format_timestamp(started_at)}")
    if finished_at is not None:
        lines.append(f"Finished: {_format_timestamp(finished_at)}")
    if worktree_path:
        lines.append(f"Worktree: {worktree_path}")
    if thread_id:
        lines.append(f"Provider thread: {thread_id}")
    lines.append(f"Awaiting input: {'yes' if awaiting_input else 'no'}")

    if execution.thinking_text:
        lines.extend(
            [
                "",
                "Current reasoning",
                _indent_block(_truncate_block(execution.thinking_text, max_lines=4)),
            ]
        )

    if execution.live_response:
        lines.extend(
            [
                "",
                "Streaming response",
                _indent_block(_truncate_block(execution.live_response, max_lines=4)),
            ]
        )

    if summary:
        lines.extend(
            [
                "",
                "Latest summary",
                _indent_block(_truncate_block(summary, max_lines=4)),
            ]
        )
    if error:
        lines.append(f"Error: {error}")

    return "\n".join(lines)


def _render_recent_activity(task: TaskInfo, execution: _ExecutionSnapshot) -> str:
    lines = ["Recent Activity"]
    if execution.recent_activity:
        lines.extend(f"- {entry}" for entry in execution.recent_activity[-8:])
        return "\n".join(lines)

    lines.append(f"- {_idle_activity_message(task)}")
    return "\n".join(lines)


def _latest_instance(instances: Sequence[object]) -> object | None:
    if not instances:
        return None
    ordered = sorted(instances, key=_instance_sort_key)
    return ordered[-1]


def _instance_sort_key(instance: object) -> tuple[float, str]:
    timestamp = _instance_started_at(instance) or _instance_finished_at(instance)
    if timestamp is None:
        latest_run = getattr(instance, "latest_run", None)
        timestamp = _run_started_at(latest_run) or _run_finished_at(latest_run)
    if timestamp is None:
        return (0.0, _instance_agent_id(instance) or "")
    return (timestamp.timestamp(), _instance_agent_id(instance) or "")


def _thinking_from_events(events: Sequence[CanonicalEvent]) -> tuple[str, str]:
    thinking_text = ""
    thinking_status = "idle"
    for event in events:
        event_type = str(event.get("type") or "")
        if event_type == "reasoning.summary.delta":
            delta = str(event.get("delta") or "")
            if delta:
                thinking_text = f"{thinking_text}{delta}"
                thinking_status = "running"
            continue

        if event_type != "task.progress":
            continue

        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() != "reasoning":
            continue

        summary = item.get("summary")
        if isinstance(summary, list):
            parts = [str(part).strip() for part in summary if str(part).strip()]
            if parts:
                thinking_text = "\n".join(parts)
                thinking_status = "completed"
                continue
        if isinstance(summary, str) and summary.strip():
            thinking_text = summary.strip()
            thinking_status = "completed"
            continue
        text = extract_text_from_progress_item(item)
        if text.strip():
            thinking_text = text.strip()
            thinking_status = "completed"
    return thinking_text.strip(), thinking_status


def _recent_activity_from_events(events: Sequence[CanonicalEvent]) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines.extend(_activity_lines_from_event(event))

    deduped: list[str] = []
    for line in lines:
        if line and (not deduped or deduped[-1] != line):
            deduped.append(line)
    return deduped


def _request_activity_label(event: CanonicalEvent) -> str:
    request_kind = str(event.get("request_kind") or "request").strip().lower()
    if request_kind == "approval":
        return "Approval requested"
    if request_kind == "user-input":
        return "User input requested"
    return "Request opened"


def _activity_lines_from_event(event: CanonicalEvent) -> list[str]:
    event_type = str(event.get("type") or "")
    if event_type == "turn.started":
        return ["Turn started"]
    if event_type == "turn.completed":
        return ["Turn completed"]
    if event_type == "task.completed":
        return ["Task completed"]
    if event_type in {"request.opened", "user-input.requested"}:
        return [_request_activity_label(event)]
    if event_type == "runtime.error":
        return [f"Error: {extract_error_message(event)}"]
    if event_type != "task.progress":
        return []

    item = event.get("item")
    if not isinstance(item, dict):
        return []

    item_type = str(item.get("type") or "").strip().lower()
    if item_type == "reasoning":
        return []
    if item_type in {"commandexecution", "command_execution"}:
        command = str(item.get("command") or "").strip()
        command_top_line = command.splitlines()[0]
        exit_code = item.get("exitCode")
        duration_ms = item.get("durationMs")
        status = "running" if exit_code is None else "ok" if exit_code == 0 else "failed"
        line = f"Command [{status}] {command_top_line}"
        if isinstance(duration_ms, int):
            line = f"{line} ({duration_ms}ms)"
        return [line]
    if item_type in {"filechange", "file_change"}:
        path = item.get("filename") or item.get("path")
        return [f"Modified {path}" if path else "Modified a file"]
    if item_type in {"fileread", "file_read"}:
        path = item.get("filename") or item.get("path")
        return [f"Read {path}" if path else "Read a file"]

    progress_text = extract_text_from_progress_item(item)
    if progress_text.strip():
        prefix = "Agent"
        if item_type == "usermessage":
            prefix = "User"
        return [f"{prefix}: {_truncate_single_line(progress_text)}"]
    return []


def _idle_execution_message(task: TaskInfo) -> str:
    if task.status is TaskStatus.QUEUED:
        return "Task is queued and waiting for a worker slot."
    if task.status is TaskStatus.PENDING:
        return "Task has not been queued yet."
    if task.status is TaskStatus.ACCEPTED:
        return "Task has been accepted and merged."
    if task.status is TaskStatus.COMPLETED:
        return "Task finished execution and is waiting for review."
    if task.status is TaskStatus.ESCALATED:
        return "Task was escalated to the user."
    if task.status is TaskStatus.FAILED:
        return "Latest execution attempt failed."
    return "Execution details will appear once the task starts."


def _idle_activity_message(task: TaskInfo) -> str:
    if task.status is TaskStatus.QUEUED:
        return "Waiting in the execution queue."
    if task.status is TaskStatus.PENDING:
        return "Waiting for dependencies or roadmap dispatch."
    if task.status is TaskStatus.ACCEPTED:
        return "Task is already accepted."
    if task.status is TaskStatus.COMPLETED:
        return "Task is waiting for Gatekeeper review."
    if task.status is TaskStatus.ESCALATED:
        return "Task needs user intervention."
    if task.status is TaskStatus.FAILED:
        return "No newer activity since the last failure."
    return "No execution activity recorded yet."


def _format_priority(priority: int | None) -> str:
    return {
        0: "critical",
        1: "high",
        2: "medium",
        3: "low",
    }.get(priority, "low")


def _humanize_state(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").strip().title()


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _truncate_block(text: str, *, max_lines: int) -> str:
    normalized_lines = [line.rstrip() for line in text.strip().splitlines()]
    if len(normalized_lines) <= max_lines:
        return "\n".join(normalized_lines)
    visible = normalized_lines[:max_lines]
    visible.append("...")
    return "\n".join(visible)


def _truncate_single_line(text: str, *, max_length: int = 96) -> str:
    compact = " ".join(part for part in text.splitlines() if part).strip()
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def _indent_block(text: str, prefix: str = "  ") -> str:
    return "\n".join(f"{prefix}{line}" if line else prefix.rstrip() for line in text.splitlines())


def _instance_task_id(instance: object | None) -> str | None:
    return _nested_attr(instance, ("identity", "task_id")) or _string_attr(instance, "task_id")


def _instance_agent_id(instance: object | None) -> str | None:
    return _nested_attr(instance, ("identity", "agent_id")) or _string_attr(instance, "agent_id")


def _instance_role(instance: object | None) -> str | None:
    return _nested_attr(instance, ("identity", "role")) or _string_attr(instance, "role")


def _instance_state(instance: object | None) -> str | None:
    return _nested_attr(instance, ("runtime", "state"))


def _instance_status(instance: object | None) -> str | None:
    return _nested_attr(instance, ("runtime", "status"))


def _instance_active(instance: object | None) -> bool:
    return bool(_nested_attr(instance, ("runtime", "active"), default=False))


def _instance_awaiting_input(instance: object | None) -> bool:
    return bool(_nested_attr(instance, ("runtime", "awaiting_input"), default=False))


def _instance_started_at(instance: object | None) -> datetime | None:
    return _datetime_attr(_nested_attr(instance, ("runtime", "started_at")))


def _instance_finished_at(instance: object | None) -> datetime | None:
    return _datetime_attr(_nested_attr(instance, ("runtime", "finished_at")))


def _instance_worktree_path(instance: object | None) -> str | None:
    return _nested_attr(instance, ("workspace", "worktree_path"))


def _instance_thread_id(instance: object | None) -> str | None:
    return _nested_attr(instance, ("provider", "thread_id")) or _nested_attr(
        instance,
        ("provider", "provider_thread_id"),
    )


def _instance_canonical_event_log(instance: object | None) -> str | None:
    return _nested_attr(instance, ("provider", "canonical_event_log"))


def _instance_summary(instance: object | None) -> str | None:
    return _nested_attr(instance, ("outcome", "summary"))


def _instance_error(instance: object | None) -> str | None:
    return _nested_attr(instance, ("outcome", "error"))


def _instance_output(instance: object | None) -> object | None:
    return _nested_attr(instance, ("outcome", "output"))


def _run_id(run: object | None) -> str | None:
    return _string_attr(run, "run_id")


def _run_agent_id(run: object | None) -> str | None:
    return _string_attr(run, "agent_id")


def _run_state(run: object | None) -> str | None:
    return _nested_attr(run, ("runtime", "state"))


def _run_status(run: object | None) -> str | None:
    lifecycle_status = _nested_attr(run, ("lifecycle", "status"))
    if lifecycle_status is not None:
        return _text_value(lifecycle_status)
    return None


def _run_started_at(run: object | None) -> datetime | None:
    return _datetime_attr(_nested_attr(run, ("lifecycle", "started_at")))


def _run_finished_at(run: object | None) -> datetime | None:
    return _datetime_attr(_nested_attr(run, ("lifecycle", "finished_at")))


def _run_worktree_path(run: object | None) -> str | None:
    return _nested_attr(run, ("workspace", "worktree_path"))


def _run_thread_id(run: object | None) -> str | None:
    return _nested_attr(run, ("provider", "thread_id"))


def _run_canonical_event_log(run: object | None) -> str | None:
    return _nested_attr(run, ("provider", "canonical_event_log"))


def _run_awaiting_input(run: object | None) -> bool:
    return bool(_nested_attr(run, ("runtime", "awaiting_input"), default=False))


def _run_summary(run: object | None) -> str | None:
    return _string_attr(run, "summary")


def _run_error(run: object | None) -> str | None:
    return _string_attr(run, "error")


def _agent_output_partial_text(output: object | None) -> str | None:
    return _string_attr(output, "partial_text")


def _agent_output_thinking(output: object | None) -> str | None:
    return _nested_attr(output, ("thinking", "text"))


def _agent_output_thinking_status(output: object | None) -> str:
    return _nested_attr(output, ("thinking", "status")) or "idle"


def _read_canonical_event_log(path: str) -> list[CanonicalEvent]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    events: list[CanonicalEvent] = []
    for line in lines:
        normalized = line.strip()
        if not normalized:
            continue
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            continue
        event_type = str(payload.get("event") or "").strip()
        if not event_type:
            continue
        event: dict[str, JSONValue] = {"type": event_type}
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            event["timestamp"] = timestamp
        data = payload.get("data")
        if isinstance(data, dict):
            event.update(data)
        events.append(event)
    return events


def _nested_attr(instance: object | None, path: tuple[str, ...], default: object = None) -> object:
    current = instance
    for part in path:
        if current is None:
            return default
        current = getattr(current, part, None)
    return current if current is not None else default


def _string_attr(instance: object | None, attr: str) -> str | None:
    value = getattr(instance, attr, None)
    if value is None:
        return None
    return _text_value(value)


def _datetime_attr(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _text_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)
