"""Tests for the Phase 6.5 TUI layout assembly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Static

from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapDocument
from vibrant.history import HistoryStore
from vibrant.models import AppSettings, ConsensusStatus, OrchestratorStatus
from vibrant.orchestrator import OrchestratorEngine
from vibrant.project_init import initialize_project
from vibrant.tui.app import HelpScreen, VibrantApp
from vibrant.tui.widgets.chat_panel import ChatPanel
from vibrant.tui.widgets.input_bar import InputBar


class FakeSessionManager:
    def __init__(self) -> None:
        self._threads = {}
        self.listeners = []

    def add_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    def list_threads(self) -> list:
        return []

    def get_thread(self, thread_id: str):
        return self._threads.get(thread_id)

    async def stop_session(self, thread_id: str) -> None:
        return None

    async def stop_all(self) -> None:
        return None

    async def approve_request(self, thread_id: str, jsonrpc_id, approved: bool) -> None:
        return None

    def get_provider_log_paths(self, thread_id: str) -> tuple[str | None, str | None]:
        return (None, None)


class ExecutingLifecycle:
    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        self.project_root = Path(project_root)
        self.on_canonical_event = on_canonical_event
        self.engine = OrchestratorEngine.load(self.project_root)
        self.gatekeeper = object()
        self._ensure_status(ConsensusStatus.EXECUTING, OrchestratorStatus.EXECUTING)

    def _ensure_status(self, consensus_status: ConsensusStatus, orchestrator_status: OrchestratorStatus) -> None:
        if self.engine.state.status is OrchestratorStatus.INIT:
            self.engine.transition_to(OrchestratorStatus.PLANNING)
        if self.engine.state.status is OrchestratorStatus.PLANNING and orchestrator_status is OrchestratorStatus.EXECUTING:
            self.engine.transition_to(OrchestratorStatus.EXECUTING)

        document = self.engine.consensus or ConsensusParser().parse_file(self.project_root / ".vibrant" / "consensus.md")
        updated = document.model_copy(deep=True)
        updated.status = consensus_status
        self.engine.consensus = ConsensusWriter().write(self.project_root / ".vibrant" / "consensus.md", updated)
        self.engine.refresh_from_disk()

    def reload_from_disk(self) -> RoadmapDocument:
        self.engine.refresh_from_disk()
        return RoadmapDocument(project=self.project_root.name, tasks=[])


class EscalationLifecycle(ExecutingLifecycle):
    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        super().__init__(project_root, on_canonical_event=on_canonical_event)
        document = self.engine.consensus.model_copy(deep=True)
        document.questions = ["Should auth use OAuth or API keys?"]
        self.engine.consensus = ConsensusWriter().write(self.project_root / ".vibrant" / "consensus.md", document)
        self.engine.refresh_from_disk()


class PlanningLifecycle:
    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        self.project_root = Path(project_root)
        self.on_canonical_event = on_canonical_event
        self.engine = OrchestratorEngine.load(self.project_root)
        self.gatekeeper = object()

    def reload_from_disk(self) -> RoadmapDocument:
        self.engine.refresh_from_disk()
        return RoadmapDocument(project=self.project_root.name, tasks=[])

    async def submit_gatekeeper_message(self, text: str):
        self.engine.state.status = OrchestratorStatus.PLANNING
        return SimpleNamespace(transcript="Plan drafted")


@pytest.mark.asyncio
async def test_app_mounts_four_panels_and_help_binding(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(settings=settings, cwd=str(repo), session_manager=FakeSessionManager(), lifecycle_factory=ExecutingLifecycle)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#plan-panel") is not None
        assert app.query_one("#agent-output-panel") is not None
        assert app.query_one("#consensus-panel") is not None
        assert app.query_one("#conversation-panel") is not None

        keymap = {binding.key: binding.action for binding in app.BINDINGS}
        assert keymap["f1"] == "open_help"
        assert keymap["f2"] == "toggle_pause"
        assert keymap["f3"] == "open_consensus_overlay"
        assert keymap["f5"] == "cycle_agent_output"
        assert keymap["f10"] == "quit_app"

        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_app_f2_toggles_pause_and_updates_consensus(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(settings=settings, cwd=str(repo), session_manager=FakeSessionManager(), lifecycle_factory=ExecutingLifecycle)

    async with app.run_test() as pilot:
        await pilot.pause()
        engine = app._lifecycle.engine  # noqa: SLF001 - verifying app wiring
        assert engine.state.status is OrchestratorStatus.EXECUTING
        assert ConsensusParser().parse_file(repo / ".vibrant" / "consensus.md").status is ConsensusStatus.EXECUTING

        await pilot.press("f2")
        await pilot.pause()
        assert engine.state.status is OrchestratorStatus.PAUSED
        assert ConsensusParser().parse_file(repo / ".vibrant" / "consensus.md").status is ConsensusStatus.PAUSED

        await pilot.press("f2")
        await pilot.pause()
        assert engine.state.status is OrchestratorStatus.EXECUTING
        assert ConsensusParser().parse_file(repo / ".vibrant" / "consensus.md").status is ConsensusStatus.EXECUTING


@pytest.mark.asyncio
async def test_notification_banner_appears_on_gatekeeper_escalation(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(settings=settings, cwd=str(repo), session_manager=FakeSessionManager(), lifecycle_factory=EscalationLifecycle)

    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one("#notification-banner", Static)
        assert banner.display is True
        assert "Gatekeeper needs your input" in (app.get_banner_text() or "")


@pytest.mark.asyncio
async def test_app_restores_persisted_gatekeeper_thread_on_reload(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    history_dir = tmp_path / "history"
    settings = AppSettings(default_cwd=str(repo), history_dir=str(history_dir))

    first_app = VibrantApp(
        settings=settings,
        cwd=str(repo),
        session_manager=FakeSessionManager(),
        lifecycle_factory=PlanningLifecycle,
    )
    async with first_app.run_test() as pilot:
        await pilot.pause()
        await first_app.on_input_bar_message_submitted(InputBar.MessageSubmitted("Build an auth MVP."))
        await pilot.pause()

    stored_gatekeeper = HistoryStore(str(history_dir)).load_thread(ChatPanel.GATEKEEPER_THREAD_ID)
    assert stored_gatekeeper is not None
    assert [turn.items[0].content for turn in stored_gatekeeper.turns] == ["Build an auth MVP.", "Plan drafted"]

    reloaded_app = VibrantApp(
        settings=settings,
        cwd=str(repo),
        session_manager=FakeSessionManager(),
        lifecycle_factory=ExecutingLifecycle,
    )
    async with reloaded_app.run_test() as pilot:
        await pilot.pause()
        panel = reloaded_app.query_one(ChatPanel)
        gatekeeper_thread = panel.get_gatekeeper_thread()
        assert gatekeeper_thread is not None
        assert [turn.items[0].content for turn in gatekeeper_thread.turns] == ["Build an auth MVP.", "Plan drafted"]
        assert reloaded_app._conversation_threads()[0].id == ChatPanel.GATEKEEPER_THREAD_ID  # noqa: SLF001
