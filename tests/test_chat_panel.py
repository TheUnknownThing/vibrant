"""Tests for the Panel D chat / Q&A widget and wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult

from vibrant.config import RoadmapExecutionMode
from vibrant.consensus import RoadmapDocument
from vibrant.models import AppSettings, ItemInfo, ItemType, ThreadInfo, ThreadStatus, TurnInfo, TurnRole
from vibrant.models.state import GatekeeperStatus, OrchestratorState, OrchestratorStatus
from vibrant.project_init import initialize_project
from vibrant.tui.app import VibrantApp
from vibrant.tui.widgets.chat_panel import ChatPanel
from vibrant.tui.widgets.input_bar import InputBar


class ChatPanelHarness(App):
    def compose(self) -> ComposeResult:
        yield ChatPanel(id="chat-panel")


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

    def reload_from_disk(self) -> RoadmapDocument:
        return RoadmapDocument(project=self.project_root.name, tasks=[])

    async def submit_gatekeeper_message(self, text: str):
        self.messages.append(text)
        self.engine.state.status = OrchestratorStatus.PLANNING
        return SimpleNamespace(transcript="Plan drafted")

    async def execute_until_blocked(self):
        self.execute_until_blocked_calls += 1
        return []


class FakeAutomaticPlanningLifecycle(FakePlanningLifecycle):
    execution_mode = RoadmapExecutionMode.AUTOMATIC


class FakeStreamingPlanningLifecycle(FakePlanningLifecycle):
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
            await asyncio.sleep(0.05)
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


@pytest.mark.asyncio
async def test_chat_panel_gatekeeper_messages_include_sender_labels():
    app = ChatPanelHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ChatPanel)
        panel.set_gatekeeper_state(
            status=OrchestratorStatus.EXECUTING,
            pending_questions=["Should auth use OAuth or API keys?"],
        )
        panel.record_gatekeeper_answer(
            "Should auth use OAuth or API keys?",
            "Use API keys for v1.",
        )
        await pilot.pause()

        summary = panel.get_question_summary_text()
        assert "Gatekeeper → User" in summary
        assert "Q: Should auth use OAuth or API keys?" in summary
        assert "You → Gatekeeper" in summary
        assert "A: Use API keys for v1." in summary


@pytest.mark.asyncio
async def test_chat_panel_question_notification_flashes_panel():
    app = ChatPanelHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ChatPanel)
        panel.set_gatekeeper_state(
            status=OrchestratorStatus.EXECUTING,
            pending_questions=["Should auth use OAuth or API keys?"],
            flash=True,
        )
        await pilot.pause()

        assert panel.notification_active is True

        await pilot.pause(ChatPanel.FLASH_DURATION_SECONDS + 0.2)
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

    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ChatPanel)
        assert "Gatekeeper → User" in panel.get_question_summary_text()

        await app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Use API keys for v1."))
        await pilot.pause()

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

    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ChatPanel)

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert panel.current_thread_id == "thread-1"

        await pilot.press("ctrl+t")
        await pilot.pause()
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

    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ChatPanel)
        assert panel.current_thread_id == ChatPanel.GATEKEEPER_THREAD_ID

        await app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Build an auth MVP."))
        await pilot.pause()

        lifecycle = app._lifecycle  # noqa: SLF001 - verify wiring
        assert lifecycle is not None
        assert lifecycle.messages == ["Build an auth MVP."]
        assert session_manager.sent_messages == []
        assert panel.current_thread_id == ChatPanel.GATEKEEPER_THREAD_ID
        gatekeeper_thread = panel.get_gatekeeper_thread()
        assert gatekeeper_thread is not None
        assert [turn.items[0].content for turn in gatekeeper_thread.turns] == ["Build an auth MVP.", "Plan drafted"]


@pytest.mark.asyncio
async def test_app_automatic_mode_runs_workflow_after_gatekeeper_update(tmp_path: Path):
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

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Build an auth MVP."))
        await pilot.pause(0.2)

        lifecycle = app._lifecycle  # noqa: SLF001 - verify wiring
        assert lifecycle is not None
        assert lifecycle.execute_until_blocked_calls == 1


@pytest.mark.asyncio
async def test_app_streams_gatekeeper_response_live_during_planning(tmp_path: Path):
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

    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ChatPanel)

        task = asyncio.create_task(
            app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Build an auth MVP."))
        )
        await pilot.pause(0.02)

        assert panel.get_gatekeeper_streaming_text() == "Plan draft in progress"
        gatekeeper_thread = panel.get_gatekeeper_thread()
        assert gatekeeper_thread is not None
        assert [turn.items[0].content for turn in gatekeeper_thread.turns] == ["Build an auth MVP."]

        await task
        await pilot.pause()

        assert panel.get_gatekeeper_streaming_text() == ""
        gatekeeper_thread = panel.get_gatekeeper_thread()
        assert gatekeeper_thread is not None
        assert [turn.items[0].content for turn in gatekeeper_thread.turns] == [
            "Build an auth MVP.",
            "Plan drafted",
        ]
