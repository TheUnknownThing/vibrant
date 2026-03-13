from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from vibrant.models import OrchestratorStatus
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


@pytest.mark.asyncio
async def test_interrupt_gatekeeper_action_delegates_to_facade() -> None:
    app = VibrantApp()
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    statuses: list[str] = []
    interrupt_calls = 0

    async def interrupt_gatekeeper() -> bool:
        nonlocal interrupt_calls
        interrupt_calls += 1
        return True

    app.orchestrator_facade = SimpleNamespace(interrupt_gatekeeper=interrupt_gatekeeper)
    app.orchestrator = SimpleNamespace(gatekeeper_busy=True)
    app.notify = lambda *args, **kwargs: notifications.append((args, kwargs))
    app._set_status = lambda text: statuses.append(text)
    app._refresh_gatekeeper_state = lambda *args, **kwargs: None

    await app.action_interrupt_gatekeeper()

    assert interrupt_calls == 1
    assert statuses[-1] == "Interrupting Gatekeeper…"
    assert notifications[-1][0] == ("Interrupt requested for Gatekeeper.",)


def test_refresh_gatekeeper_state_shows_interrupt_hint_while_busy() -> None:
    app = VibrantApp()

    class _FakeChatPanel:
        def bind(self, facade) -> None:
            self.facade = facade

        def sync(self, *, flash: bool = False) -> None:
            self.flash = flash

    class _FakeInputBar:
        def __init__(self) -> None:
            self.enabled: bool | None = None
            self.context: tuple[str | None, str] | None = None
            self.placeholder: str | None = None

        def set_enabled(self, enabled: bool) -> None:
            self.enabled = enabled

        def set_context(self, model: str | None = None, status: str = "") -> None:
            self.context = (model, status)

        def set_placeholder(self, text: str) -> None:
            self.placeholder = text

    chat_panel = _FakeChatPanel()
    input_bar = _FakeInputBar()
    banners: list[str | None] = []

    app.orchestrator_facade = SimpleNamespace(get_workflow_status=lambda: OrchestratorStatus.PLANNING)
    app.orchestrator = SimpleNamespace(gatekeeper_busy=True)
    app._chat_panel = lambda: chat_panel
    app._input_bar = lambda: input_bar
    app._pending_gatekeeper_questions = lambda: []
    app._set_banner = banners.append

    app._refresh_gatekeeper_state()

    assert banners[-1] == "Gatekeeper is responding…"
    assert input_bar.enabled is False
    assert input_bar.context == ("gatekeeper", "running… · Esc to interrupt")
    assert input_bar.placeholder == "Gatekeeper is responding… Press Esc to interrupt."
