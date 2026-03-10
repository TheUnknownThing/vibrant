"""Tests for the Panel D chat / Q&A widget and wiring."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import RoadmapExecutionMode
from vibrant.consensus import RoadmapDocument
from vibrant.models import AppSettings, ItemInfo, ItemType, ThreadInfo, ThreadStatus, TurnInfo, TurnRole
from vibrant.models.state import GatekeeperStatus, OrchestratorState, OrchestratorStatus
from vibrant.project_init import initialize_project
from vibrant.tui.app import VibrantApp
from vibrant.tui.widgets.chat_panel import ChatPanel
from vibrant.tui.widgets.input_bar import InputBar


class FakeSessionManager:
    def __init__(self, threads: list[ThreadInfo] | None = None) -> None:
        self._threads = {thread.id: thread for thread in threads or []}
        self.listeners = []
        self.sent_messages: list[tuple[str, str]] = []

    def add_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    def get_thread(self, thread_id: str) -> ThreadInfo | None:
        return self._threads.get(thread_id)

    def list_threads(self) -> list[ThreadInfo]:
        return list(self._threads.values())

    async def send_message(self, thread_id: str, text: str) -> None:
        self.sent_messages.append((thread_id, text))

    async def stop_session(self, thread_id: str) -> None:
        return None

    async def stop_all(self) -> None:
        return None

    async def approve_request(self, thread_id: str, jsonrpc_id, approved: bool) -> None:
        return None

    def get_provider_log_paths(self, thread_id: str) -> tuple[str | None, str | None]:
        return (None, None)


class FakeAnswerEngine:
    USER_INPUT_BANNER = "⚠ Gatekeeper needs your input — see Chat panel"

    def __init__(self) -> None:
        self.agents = {}
        self.consensus = None
        self.consensus_path = None
        self.notification_bell_enabled = False
        self.answer_calls: list[dict[str, str]] = []
        self.state = OrchestratorState(
            session_id="session-1",
            status=OrchestratorStatus.EXECUTING,
            gatekeeper_status=GatekeeperStatus.AWAITING_USER,
            pending_questions=["Should auth use OAuth or API keys?"],
        )

    async def answer_pending_question(self, gatekeeper, *, answer: str, question: str | None = None):
        self.answer_calls.append({"answer": answer, "question": question or ""})
        self.state.pending_questions = []
        self.state.gatekeeper_status = GatekeeperStatus.IDLE
        return SimpleNamespace(verdict="accepted")


class FakeLifecycle:
    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        self.project_root = Path(project_root)
        self.on_canonical_event = on_canonical_event
        self.engine = FakeAnswerEngine()
        self.gatekeeper = object()

    def reload_from_disk(self) -> RoadmapDocument:
        return RoadmapDocument(project=self.project_root.name, tasks=[])


class FakePlanningEngine:
    USER_INPUT_BANNER = "⚠ Gatekeeper needs your input — see Chat panel"

    def __init__(self) -> None:
        self.agents = {}
        self.consensus = None
        self.consensus_path = None
        self.notification_bell_enabled = False
        self.state = OrchestratorState(
            session_id="session-2",
            status=OrchestratorStatus.INIT,
            gatekeeper_status=GatekeeperStatus.IDLE,
            pending_questions=[],
        )


class FakePlanningLifecycle:
    execution_mode = RoadmapExecutionMode.MANUAL

    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        self.project_root = Path(project_root)
        self.on_canonical_event = on_canonical_event
        self.engine = FakePlanningEngine()
        self.gatekeeper = object()
        self.messages: list[str] = []
        self.execute_until_blocked_calls = 0
        self.gatekeeper_message_submitted = asyncio.Event()
        self.workflow_run_completed = asyncio.Event()

    def reload_from_disk(self) -> RoadmapDocument:
        return RoadmapDocument(project=self.project_root.name, tasks=[])

    async def submit_gatekeeper_message(self, text: str):
        self.messages.append(text)
        self.engine.state.status = OrchestratorStatus.PLANNING
        self.gatekeeper_message_submitted.set()
        return SimpleNamespace(transcript="Plan drafted")

    async def execute_until_blocked(self):
        self.execute_until_blocked_calls += 1
        self.workflow_run_completed.set()
        return []


class FakeAutomaticPlanningLifecycle(FakePlanningLifecycle):
    execution_mode = RoadmapExecutionMode.AUTOMATIC


class FakeStreamingPlanningLifecycle(FakePlanningLifecycle):
    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        super().__init__(project_root, on_canonical_event=on_canonical_event)
        self.stream_started = asyncio.Event()
        self.allow_completion = asyncio.Event()

    async def submit_gatekeeper_message(self, text: str):
        self.messages.append(text)
        self.engine.state.status = OrchestratorStatus.PLANNING
        if self.on_canonical_event is not None:
            await self.on_canonical_event(
                {
                    "type": "turn.started",
                    "agent_id": "gatekeeper-project_start-test",
                    "task_id": "gatekeeper-project_start",
                    "turn": {"id": "turn-gatekeeper-1"},
                }
            )
            await self.on_canonical_event(
                {
                    "type": "content.delta",
                    "agent_id": "gatekeeper-project_start-test",
                    "task_id": "gatekeeper-project_start",
                    "delta": "Plan draft in progress",
                }
            )
            self.stream_started.set()
            await self.allow_completion.wait()
            await self.on_canonical_event(
                {
                    "type": "turn.completed",
                    "agent_id": "gatekeeper-project_start-test",
                    "task_id": "gatekeeper-project_start",
                    "turn": {"id": "turn-gatekeeper-1"},
                }
            )
        return SimpleNamespace(transcript="Plan drafted")



def _thread(thread_id: str, user_text: str, assistant_text: str) -> ThreadInfo:
    return ThreadInfo(
        id=thread_id,
        title=f"Thread {thread_id}",
        status=ThreadStatus.IDLE,
        model="gpt-5.3-codex",
        turns=[
            TurnInfo(
                role=TurnRole.USER,
                items=[ItemInfo(type=ItemType.TEXT, content=user_text)],
            ),
            TurnInfo(
                role=TurnRole.ASSISTANT,
                items=[ItemInfo(type=ItemType.TEXT, content=assistant_text)],
            ),
        ],
    )


async def _wait_for(assertion, *, attempts: int = 50) -> None:
    for _ in range(attempts):
        if assertion():
            return
        await asyncio.sleep(0)
    raise AssertionError("Timed out waiting for chat panel update")


async def _shutdown_default_executor() -> None:
    loop = asyncio.get_running_loop()
    executor = getattr(loop, "_default_executor", None)
    if executor is None:
        return
    executor.shutdown(wait=True, cancel_futures=True)
    loop._default_executor = None


@asynccontextmanager
async def _run_test(app):
    async with app.run_test() as pilot:
        yield pilot
    await _shutdown_default_executor()


def test_chat_panel_gatekeeper_messages_include_sender_labels():
    panel = ChatPanel()
    panel.set_gatekeeper_state(
        status=OrchestratorStatus.EXECUTING,
        pending_questions=["Should auth use OAuth or API keys?"],
    )
    panel.record_gatekeeper_answer(
        "Should auth use OAuth or API keys?",
        "Use API keys for v1.",
    )

    summary = panel.get_question_summary_text()
    assert "Gatekeeper → User" in summary
    assert "Q: Should auth use OAuth or API keys?" in summary
    assert "You → Gatekeeper" in summary
    assert "A: Use API keys for v1." in summary


def test_chat_panel_question_notification_flashes_panel(monkeypatch: pytest.MonkeyPatch):
    panel = ChatPanel()
    timer_callbacks: list[object] = []

    def _capture_timer(delay: float, callback):
        timer_callbacks.append(callback)
        return None

    monkeypatch.setattr(panel, "set_timer", _capture_timer)

    panel.set_gatekeeper_state(
        status=OrchestratorStatus.EXECUTING,
        pending_questions=["Should auth use OAuth or API keys?"],
        flash=True,
    )

    assert panel.notification_active is True
    assert len(timer_callbacks) == 1

    timer_callbacks[0]()
    assert panel.notification_active is False


@pytest.mark.asyncio
async def test_app_forwards_pending_question_answer_to_gatekeeper(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    session_manager = FakeSessionManager()
    app = VibrantApp(
        settings=settings,
        cwd=str(repo),
        session_manager=session_manager,
        lifecycle_factory=FakeLifecycle,
    )

    async with _run_test(app):
        panel = app.query_one(ChatPanel)
        assert "Gatekeeper → User" in panel.get_question_summary_text()

        await app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Use API keys for v1."))

        engine = app._lifecycle.engine  # noqa: SLF001 - test inspects wiring
        assert engine.answer_calls == [
            {
                "answer": "Use API keys for v1.",
                "question": "Should auth use OAuth or API keys?",
            }
        ]
        assert session_manager.sent_messages == []
        assert "You → Gatekeeper" in panel.get_question_summary_text()
        assert "A: Use API keys for v1." in panel.get_question_summary_text()


@pytest.mark.asyncio
async def test_app_thread_switching_updates_chat_panel(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    thread_one = _thread("thread-1", "first user prompt", "first assistant reply")
    thread_two = _thread("thread-2", "second user prompt", "second assistant reply")

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    session_manager = FakeSessionManager([thread_one, thread_two])
    app = VibrantApp(settings=settings, cwd=str(repo), session_manager=session_manager)

    async with _run_test(app) as pilot:
        panel = app.query_one(ChatPanel)

        await pilot.press("ctrl+t")
        assert panel.current_thread_id == "thread-1"

        await pilot.press("ctrl+t")
        assert panel.current_thread_id == "thread-2"



@pytest.mark.asyncio
async def test_app_routes_initial_prompt_to_gatekeeper_on_init(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    thread_one = _thread("thread-1", "first user prompt", "first assistant reply")
    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    session_manager = FakeSessionManager([thread_one])
    app = VibrantApp(
        settings=settings,
        cwd=str(repo),
        session_manager=session_manager,
        lifecycle_factory=FakePlanningLifecycle,
    )

    async with _run_test(app):
        panel = app.query_one(ChatPanel)
        assert panel.current_thread_id == ChatPanel.GATEKEEPER_THREAD_ID

        await app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Build an auth MVP."))
        await _wait_for(
            lambda: panel.get_gatekeeper_thread() is not None
            and [turn.items[0].content for turn in panel.get_gatekeeper_thread().turns] == ["Build an auth MVP.", "Plan drafted"],
        )

        lifecycle = app._lifecycle  # noqa: SLF001 - verify wiring
        assert lifecycle is not None
        await asyncio.wait_for(lifecycle.gatekeeper_message_submitted.wait(), timeout=1.0)
        assert lifecycle.messages == ["Build an auth MVP."]
        assert session_manager.sent_messages == []
        assert panel.current_thread_id == ChatPanel.GATEKEEPER_THREAD_ID
        gatekeeper_thread = panel.get_gatekeeper_thread()
        assert gatekeeper_thread is not None
        assert [turn.items[0].content for turn in gatekeeper_thread.turns] == ["Build an auth MVP.", "Plan drafted"]


@pytest.mark.asyncio
async def test_app_automatic_mode_runs_workflow_after_gatekeeper_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(
        settings=settings,
        cwd=str(repo),
        session_manager=FakeSessionManager(),
        lifecycle_factory=FakeAutomaticPlanningLifecycle,
    )
    launches: list[bool] = []

    def _record_launch(*, notify_when_idle: bool) -> None:
        launches.append(notify_when_idle)

    monkeypatch.setattr(app, "_launch_roadmap_runner", _record_launch)

    async with _run_test(app):
        await app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Build an auth MVP."))

        lifecycle = app._lifecycle  # noqa: SLF001 - verify wiring
        assert lifecycle is not None
        assert lifecycle.execute_until_blocked_calls == 0
        assert launches == [False]


def test_app_streams_gatekeeper_response_live_during_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(
        settings=settings,
        cwd=str(repo),
        session_manager=FakeSessionManager(),
        lifecycle_factory=FakeStreamingPlanningLifecycle,
    )
    panel = ChatPanel()
    panel.record_gatekeeper_user_message("Build an auth MVP.")

    def _query_one(selector, *args, **kwargs):
        if selector is ChatPanel:
            return panel
        raise AssertionError(f"Unexpected query: {selector!r}")

    monkeypatch.setattr(app, "query_one", _query_one)
    monkeypatch.setattr(app, "_persist_gatekeeper_thread", lambda: None)
    monkeypatch.setattr(app, "_refresh_thread_list", lambda: None)
    monkeypatch.setattr(app, "_set_status", lambda text: None)

    app._handle_gatekeeper_canonical_event(  # noqa: SLF001 - exercising app event wiring directly
        {
            "type": "turn.started",
            "agent_id": "gatekeeper-project_start-test",
            "task_id": "gatekeeper-project_start",
            "turn": {"id": "turn-gatekeeper-1"},
        }
    )
    app._handle_gatekeeper_canonical_event(  # noqa: SLF001 - exercising app event wiring directly
        {
            "type": "content.delta",
            "agent_id": "gatekeeper-project_start-test",
            "task_id": "gatekeeper-project_start",
            "delta": "Plan draft in progress",
        }
    )

    assert panel.get_gatekeeper_streaming_text() == "Plan draft in progress"
    gatekeeper_thread = panel.get_gatekeeper_thread()
    assert gatekeeper_thread is not None
    assert [turn.items[0].content for turn in gatekeeper_thread.turns] == ["Build an auth MVP."]

    panel.record_gatekeeper_response("Plan drafted")
    app._handle_gatekeeper_canonical_event(  # noqa: SLF001 - exercising app event wiring directly
        {
            "type": "turn.completed",
            "agent_id": "gatekeeper-project_start-test",
            "task_id": "gatekeeper-project_start",
            "turn": {"id": "turn-gatekeeper-1"},
        }
    )

    assert panel.get_gatekeeper_streaming_text() == ""
    gatekeeper_thread = panel.get_gatekeeper_thread()
    assert gatekeeper_thread is not None
    assert [turn.items[0].content for turn in gatekeeper_thread.turns] == [
        "Build an auth MVP.",
        "Plan drafted",
    ]
