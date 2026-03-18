from __future__ import annotations

from vibrant.tui.widgets.task_status import _activity_lines_from_event


def test_activity_lines_report_approval_requests_distinctly() -> None:
    event = {"type": "request.opened", "request_kind": "approval"}

    assert _activity_lines_from_event(event) == ["Approval requested"]


def test_activity_lines_preserve_user_input_request_label() -> None:
    event = {"type": "request.opened", "request_kind": "user-input"}

    assert _activity_lines_from_event(event) == ["User input requested"]
