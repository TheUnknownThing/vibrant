from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult

from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.tui.screens import VibingScreen
from vibrant.tui.widgets.task_status import TaskStatusView


class TaskStatusHarness(App[None]):
    orchestrator_facade = None

    def compose(self) -> ComposeResult:
        yield TaskStatusView()


class VibingHarness(App[None]):
    orchestrator_facade = None

    def compose(self) -> ComposeResult:
        yield VibingScreen()


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
        instances: list[SimpleNamespace]
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
        events_by_run: dict[str, list[dict[str, object]]],
    ) -> None:
        self._runs_by_task = runs_by_task
        self._events_by_run = events_by_run
        self.events_calls = 0

    def latest_for_task(self, task_id: str, *, role: str | None = None) -> SimpleNamespace | None:
        run = self._runs_by_task.get(task_id)
        if run is None:
            return None
        if role is not None and getattr(run, "role", None) != role:
            return None
        return run

    def events(self, run_id: str) -> list[dict[str, object]]:
        self.events_calls += 1
        return list(self._events_by_run.get(run_id, ()))


def _facade(
    *,
    instances_by_task: dict[str, list[SimpleNamespace]],
    outputs_by_agent: dict[str, SimpleNamespace],
    runs_by_task: dict[str, SimpleNamespace],
    events_by_run: dict[str, list[dict[str, object]]],
) -> SimpleNamespace:
    return SimpleNamespace(
        instances=_InstancesAPI(instances_by_task, outputs_by_agent),
        runs=_RunsAPI(runs_by_task, events_by_run),
    )


def _task(task_id: str, title: str, *, status: TaskStatus, **kwargs: object) -> TaskInfo:
    defaults = {
        "id": task_id,
        "title": title,
        "status": status,
        "branch": f"vibrant/{task_id}",
        "priority": 2,
        "acceptance_criteria": [f"{title} works"],
        "prompt": f"Implement {title}.\nKeep the UI focused.",
    }
    defaults.update(kwargs)
    return TaskInfo(**defaults)


def _instance(
    *,
    agent_id: str,
    task_id: str,
    role: str = "code",
    state: str,
    status: str,
    active: bool,
    awaiting_input: bool = False,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    worktree_path: str | None = None,
    thread_id: str | None = None,
    summary: str | None = None,
    error: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        identity=SimpleNamespace(task_id=task_id, agent_id=agent_id, role=role),
        runtime=SimpleNamespace(
            state=state,
            status=status,
            active=active,
            done=not active,
            awaiting_input=awaiting_input,
            started_at=started_at,
            finished_at=finished_at,
        ),
        workspace=SimpleNamespace(worktree_path=worktree_path, branch=f"vibrant/{task_id}"),
        provider=SimpleNamespace(thread_id=thread_id),
        outcome=SimpleNamespace(summary=summary, error=error),
        agent_id=agent_id,
        role=role,
        latest_run=SimpleNamespace(started_at=started_at, finished_at=finished_at),
    )


def _run(
    *,
    run_id: str,
    agent_id: str,
    task_id: str,
    role: str = "code",
    lifecycle_status: str,
    runtime_state: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    worktree_path: str | None = None,
    thread_id: str | None = None,
    awaiting_input: bool = False,
    summary: str | None = None,
    error: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        agent_id=agent_id,
        task_id=task_id,
        role=role,
        lifecycle=SimpleNamespace(status=lifecycle_status, started_at=started_at, finished_at=finished_at),
        runtime=SimpleNamespace(state=runtime_state, awaiting_input=awaiting_input),
        workspace=SimpleNamespace(worktree_path=worktree_path, branch=f"vibrant/{task_id}"),
        provider=SimpleNamespace(thread_id=thread_id),
        summary=summary,
        error=error,
    )


def _output(*, thinking: str = "", thinking_status: str = "idle", partial_text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        thinking=SimpleNamespace(text=thinking, status=thinking_status),
        partial_text=partial_text,
    )


def _sample_state() -> tuple[list[TaskInfo], SimpleNamespace]:
    accepted = _task("task-001", "Ship planner shell", status=TaskStatus.ACCEPTED, priority=1)
    running = _task(
        "task-002",
        "Build task-status panel",
        status=TaskStatus.IN_PROGRESS,
        priority=0,
        acceptance_criteria=[
            "Selected task is rendered in the main panel",
            "Roadmap progress is visible",
            "Execution details update while the task is running",
        ],
        prompt="Read docs/tui.md and docs/tui-todo.md.\nWire the task-status panel to real state.",
    )
    completed = _task("task-003", "Backfill task selection", status=TaskStatus.COMPLETED)
    queued = _task("task-004", "Preview task list in the frontend", status=TaskStatus.QUEUED, dependencies=["task-002"])
    tasks = [accepted, running, completed, queued]

    started_at = datetime(2026, 3, 13, 15, 45, tzinfo=UTC)
    running_instance = _instance(
        agent_id="agent-task-002",
        task_id="task-002",
        state="running",
        status="running",
        active=True,
        started_at=started_at,
        worktree_path="/tmp/vibrant-preview/task-002",
        thread_id="thread-task-002",
        summary="Wiring the task-status panel to live task state.",
    )
    completed_instance = _instance(
        agent_id="agent-task-003",
        task_id="task-003",
        state="completed",
        status="completed",
        active=False,
        started_at=datetime(2026, 3, 13, 14, 30, tzinfo=UTC),
        finished_at=datetime(2026, 3, 13, 14, 55, tzinfo=UTC),
        worktree_path="/tmp/vibrant-preview/task-003",
        thread_id="thread-task-003",
        summary="Selection state is now shared by the screen.",
    )

    running_run = _run(
        run_id="run-task-002-live",
        agent_id="agent-task-002",
        task_id="task-002",
        lifecycle_status="running",
        runtime_state="running",
        started_at=started_at,
        worktree_path="/tmp/vibrant-preview/task-002",
        thread_id="thread-task-002",
        summary="Wiring the task-status panel to live task state.",
    )
    completed_run = _run(
        run_id="run-task-003-done",
        agent_id="agent-task-003",
        task_id="task-003",
        lifecycle_status="completed",
        runtime_state="completed",
        started_at=datetime(2026, 3, 13, 14, 30, tzinfo=UTC),
        finished_at=datetime(2026, 3, 13, 14, 55, tzinfo=UTC),
        worktree_path="/tmp/vibrant-preview/task-003",
        thread_id="thread-task-003",
        summary="Selection state is now shared by the screen.",
    )

    facade = _facade(
        instances_by_task={
            "task-002": [running_instance],
            "task-003": [completed_instance],
        },
        outputs_by_agent={
            "agent-task-002": _output(
                thinking="Inspecting the canonical event projection and task selection flow.",
                thinking_status="streaming",
                partial_text="Rendering the latest execution details in the panel now.",
            )
        },
        runs_by_task={
            "task-002": running_run,
            "task-003": completed_run,
        },
        events_by_run={
            "run-task-002-live": [
                {"type": "turn.started"},
                {"type": "task.progress", "item": {"type": "fileRead", "path": "docs/tui.md"}},
                {"type": "reasoning.summary.delta", "delta": "Inspecting the current stub."},
                {
                    "type": "task.progress",
                    "item": {"type": "commandExecution", "command": "uv run pytest tests/test_task_status_widget.py"},
                },
                {
                    "type": "task.progress",
                    "item": {"type": "fileChange", "path": "vibrant/tui/widgets/task_status.py"},
                },
                {
                    "type": "task.progress",
                    "item": {"type": "agentMessage", "text": "Task tree selection now controls the main status panel."},
                },
            ],
            "run-task-003-done": [
                {"type": "turn.started"},
                {"type": "task.progress", "item": {"type": "fileChange", "path": "vibrant/tui/screens/vibing.py"}},
                {"type": "turn.completed"},
                {"type": "task.completed"},
            ],
        },
    )
    return tasks, facade


@pytest.mark.asyncio
async def test_task_status_view_renders_selected_task_progress_and_execution_details() -> None:
    tasks, facade = _sample_state()
    app = TaskStatusHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(TaskStatusView)
        widget.bind(facade)
        widget.sync(tasks)
        await pilot.pause()

        assert widget.selected_task_id == "task-002"
        assert "Roadmap progress: 2 of 4 tasks finished" in widget.get_progress_summary_text()
        assert "task-002 - Build task-status panel" in widget.get_task_details_text()
        assert "Roadmap position: 2 / 4" in widget.get_task_details_text()
        assert "Acceptance Criteria" in widget.get_task_details_text()
        assert "State: Running" in widget.get_execution_text()
        assert "Agent: agent-task-002 (code)" in widget.get_execution_text()
        assert "Provider thread: thread-task-002" in widget.get_execution_text()
        assert "Current reasoning" in widget.get_execution_text()
        assert "Streaming response" in widget.get_execution_text()
        assert "Read docs/tui.md" in widget.get_activity_text()
        assert "Command [running] uv run pytest tests/test_task_status_widget.py" in widget.get_activity_text()
        assert "Modified vibrant/tui/widgets/task_status.py" in widget.get_activity_text()


@pytest.mark.asyncio
async def test_task_status_view_can_switch_to_a_queued_task() -> None:
    tasks, facade = _sample_state()
    app = TaskStatusHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(TaskStatusView)
        widget.bind(facade)
        widget.sync(tasks)
        widget.select_task("task-004")
        await pilot.pause()

        assert widget.selected_task_id == "task-004"
        assert "task-004 - Preview task list in the frontend" in widget.get_task_details_text()
        assert "State: Queued" in widget.get_execution_text()
        assert "Task is queued and waiting for a worker slot." in widget.get_execution_text()
        assert "Waiting in the execution queue." in widget.get_activity_text()


@pytest.mark.asyncio
async def test_task_status_view_reuses_cached_run_events_until_log_changes(tmp_path: Path) -> None:
    tasks, facade = _sample_state()
    run_log = tmp_path / "run-task-002-live.ndjson"
    run_log.write_text('{"type":"turn.started"}\n', encoding="utf-8")

    run = facade.runs._runs_by_task["task-002"]
    run.provider = SimpleNamespace(thread_id=run.provider.thread_id, canonical_event_log=str(run_log))

    app = TaskStatusHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(TaskStatusView)
        widget.bind(facade)

        widget.sync(tasks)
        await pilot.pause()
        assert facade.runs.events_calls == 1

        widget.sync(tasks)
        await pilot.pause()
        assert facade.runs.events_calls == 1

        run_log.write_text('{"type":"turn.started"}\n{"type":"task.progress"}\n', encoding="utf-8")
        current_stats = run_log.stat()
        os.utime(run_log, ns=(current_stats.st_atime_ns, current_stats.st_mtime_ns + 1_000))

        widget.sync(tasks)
        await pilot.pause()
        assert facade.runs.events_calls == 2


@pytest.mark.asyncio
async def test_vibing_screen_task_messages_keep_tree_and_status_panel_in_sync() -> None:
    tasks, facade = _sample_state()
    app = VibingHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.query_one(VibingScreen)
        screen.sync_task_views(tasks, facade=facade)
        await pilot.pause()

        screen.show_agent_logs()
        await pilot.pause()

        screen.on_plan_tree_task_highlighted(SimpleNamespace(task=tasks[3]))
        await pilot.pause()

        assert screen.active_tab == "agent-logs"
        assert screen.task_status.selected_task_id == "task-004"

        screen.on_plan_tree_task_selected(SimpleNamespace(task=tasks[1]))
        await pilot.pause()

        assert screen.active_tab == "task-status"
        assert screen.task_status.selected_task_id == "task-002"
