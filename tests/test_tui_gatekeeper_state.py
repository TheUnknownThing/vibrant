from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from vibrant.tui.app import VibrantApp


@pytest.mark.asyncio
async def test_gatekeeper_message_completion_refreshes_input_state_after_task_clears() -> None:
    app = VibrantApp()
    refresh_busy_states: list[bool] = []

    async def submit_gatekeeper_message(text: str) -> object:
        assert text == "hello"
        return object()

    app.orchestrator_facade = SimpleNamespace(submit_gatekeeper_message=submit_gatekeeper_message)
    app.orchestrator = SimpleNamespace(gatekeeper_busy=False)
    app._current_pending_gatekeeper_question = lambda: None
    app._refresh_project_views = lambda: None
    app._persist_gatekeeper_thread = lambda: None
    app._maybe_sync_post_planning_transition = lambda: False
    app.notify = lambda *args, **kwargs: None
    app._set_status = lambda *args, **kwargs: None
    app._start_automatic_workflow_if_needed = lambda: None
    app._refresh_gatekeeper_state = lambda *args, **kwargs: refresh_busy_states.append(app._gatekeeper_is_busy())

    task = asyncio.create_task(app._start_gatekeeper_message("hello"))
    app._gatekeeper_request_task = task
    await task

    assert refresh_busy_states
    assert refresh_busy_states[-1] is False
