from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from vibrant.tui.widgets.task_status import _activity_lines_from_event
from vibrant.tui.widgets.task_status import TaskStatusView
from vibrant.models.task import TaskInfo, TaskStatus


def test_activity_lines_report_approval_requests_distinctly() -> None:
    event = {"type": "request.opened", "request_kind": "approval"}

    assert _activity_lines_from_event(event) == ["Approval requested"]


def test_activity_lines_preserve_user_input_request_label() -> None:
    event = {"type": "request.opened", "request_kind": "user-input"}

    assert _activity_lines_from_event(event) == ["User input requested"]


def test_task_status_refresh_selected_task_execution_reads_appended_event_tail(tmp_path: Path) -> None:
    log_path = tmp_path / "canonical.ndjson"
    log_path.write_text(
        _canonical_event("turn.started") + "\n",
        encoding="utf-8",
    )
    widget = TaskStatusView()
    widget.bind(_FakeFacade(run=_run_snapshot(log_path)))
    widget.sync(
        [TaskInfo(id="task-1", title="Profile task status", status=TaskStatus.IN_PROGRESS)],
        selected_task_id="task-1",
        refresh_execution=True,
    )

    assert "Turn started" in widget.get_activity_text()

    log_path.write_text(
        log_path.read_text(encoding="utf-8") + _canonical_event(
            "task.progress",
            item={
                "type": "command_execution",
                "command": "uv run pytest tests/test_task_status_view.py",
                "exitCode": 0,
                "durationMs": 12,
            },
        )
        + "\n",
        encoding="utf-8",
    )

    widget.refresh_selected_task_execution()

    activity = widget.get_activity_text()
    assert "Turn started" in activity
    assert "Command [ok] uv run pytest tests/test_task_status_view.py (12ms)" in activity


def test_task_status_prefers_narrow_latest_run_lookup() -> None:
    class _StrictFacade:
        def __init__(self) -> None:
            self.requested_task_ids: list[str] = []

        def list_instances(self, *, active_only: bool = False) -> list[object]:
            return []

        def latest_run_for_task(self, task_id: str) -> object | None:
            self.requested_task_ids.append(task_id)
            return None

        def list_runs(self, *, task_id: str | None = None) -> list[object]:
            raise AssertionError("task status should not enumerate all runs for a single task refresh")

    facade = _StrictFacade()
    widget = TaskStatusView()
    widget.bind(facade)  # type: ignore[arg-type]

    widget.sync(
        [TaskInfo(id="task-1", title="Profile task status", status=TaskStatus.IN_PROGRESS)],
        selected_task_id="task-1",
        refresh_execution=True,
    )

    assert facade.requested_task_ids == ["task-1"]


def _canonical_event(event_type: str, **data: object) -> str:
    payload: dict[str, object] = {
        "event": event_type,
        "timestamp": "2026-03-19T00:00:00Z",
    }
    if data:
        payload["data"] = data
    return json.dumps(payload)


def _run_snapshot(log_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run-1",
        agent_id="agent-1",
        runtime=SimpleNamespace(
            state="running",
            status="running",
            awaiting_input=False,
        ),
        workspace=SimpleNamespace(worktree_path=str(log_path.parent / "worktree")),
        provider=SimpleNamespace(
            canonical_event_log=str(log_path),
            thread_id="thread-1",
        ),
    )


class _FakeFacade:
    def __init__(self, *, run: object | None) -> None:
        self._run = run

    def list_instances(self, *, active_only: bool = False) -> list[object]:
        return []

    def latest_run_for_task(self, task_id: str) -> object | None:
        return self._run
