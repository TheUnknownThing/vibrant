"""Tests for the Phase 6.5 TUI layout assembly."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, Static

from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapDocument
from vibrant.models import AppSettings, ConsensusStatus, OrchestratorStatus
from vibrant.orchestrator import OrchestratorEngine
from vibrant.project_init import initialize_project
from vibrant.tui.app import HelpScreen, InitializationScreen, VibrantApp
from vibrant.tui.widgets.multiselect import Multiselect
from vibrant.tui.widgets.path_autocomplete import PathAutocomplete


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
async def test_uninitialized_workspace_shows_initialization_screen(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(settings=settings, cwd=str(repo), session_manager=FakeSessionManager())

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, InitializationScreen)
        options = app.screen.query_one("#initialization-options", Multiselect)
        assert options.show_frame is True
        assert options.active_style == "bold yellow"
        assert options.entries == [
            "Initialize Project Here",
            "Initialize Project (Select Directory)",
            "Exit",
        ]


@pytest.mark.asyncio
async def test_initialization_screen_can_initialize_current_workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    settings = AppSettings(default_cwd=str(repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(settings=settings, cwd=str(repo), session_manager=FakeSessionManager(), lifecycle_factory=ExecutingLifecycle)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, InitializationScreen)

        await app.screen.action_initialize_here()
        await pilot.pause()

        assert (repo / ".vibrant").is_dir()
        assert not isinstance(app.screen, InitializationScreen)
        assert app._lifecycle is not None  # noqa: SLF001 - verifies app reloaded lifecycle


@pytest.mark.asyncio
async def test_initialization_screen_can_initialize_selected_workspace(tmp_path: Path):
    current_repo = tmp_path / "current"
    target_repo = tmp_path / "target"
    current_repo.mkdir()
    target_repo.mkdir()

    settings = AppSettings(default_cwd=str(current_repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(settings=settings, cwd=str(current_repo), session_manager=FakeSessionManager(), lifecycle_factory=ExecutingLifecycle)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, InitializationScreen)

        app.screen._on_directory_selected(target_repo)  # noqa: SLF001 - verifies callback wiring
        await pilot.pause()
        await pilot.pause()

        assert (target_repo / ".vibrant").is_dir()
        assert not isinstance(app.screen, InitializationScreen)
        assert app._project_root == target_repo  # noqa: SLF001 - verifies selected directory became active


@pytest.mark.asyncio
async def test_directory_selection_screen_autocompletes_directories_only(tmp_path: Path):
    current_repo = tmp_path / "current"
    target_alpha = tmp_path / "target-alpha"
    target_beta = tmp_path / "target-beta"
    ignored_file = tmp_path / "target-file.txt"
    current_repo.mkdir()
    target_alpha.mkdir()
    target_beta.mkdir()
    ignored_file.write_text("ignore me", encoding="utf-8")

    settings = AppSettings(default_cwd=str(current_repo), history_dir=str(tmp_path / "history"))
    app = VibrantApp(settings=settings, cwd=str(current_repo), session_manager=FakeSessionManager())

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, InitializationScreen)

        await app.screen.action_select_directory()
        await pilot.pause()

        path_input = app.screen.query_one("#directory-selection-input", PathAutocomplete)
        input_widget = path_input.query_one(Input)
        option_list = path_input.query_one(OptionList)

        input_widget.value = str(tmp_path / "target")
        await pilot.pause()

        prompts = [str(option.prompt) for option in option_list.options]
        assert option_list.display is True
        assert f"{target_alpha.resolve()}{os.sep}" in prompts
        assert f"{target_beta.resolve()}{os.sep}" in prompts
        assert str(ignored_file.resolve()) not in prompts

        await pilot.press("tab")
        await pilot.pause()

        assert Path(path_input.value).resolve() == target_alpha.resolve()


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
