"""Main Textual application for Vibrant."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
from typing import Any, Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Static

from ..config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, find_project_root, resolve_project_path
from ..gatekeeper import PLANNING_COMPLETE_MCP_SENTINEL, PLANNING_COMPLETE_MCP_TOOL
from ..history import HistoryStore
from ..models import AppSettings, ConsensusStatus, OrchestratorStatus, ThreadInfo
from ..orchestrator import CodeAgentLifecycle, CodeAgentLifecycleResult, OrchestratorFacade
from ..project_init import ensure_project_files, initialize_project
from .screens import HelpScreen, InitializationScreen, PlanningScreen, VibingScreen
from .widgets.chat_panel import ChatPanel
from .widgets.input_bar import InputBar
from .widgets.settings_panel import SettingsPanel

logger = logging.getLogger(__name__)
LifecycleFactory = Callable[..., CodeAgentLifecycle]
WorkspaceScreen = PlanningScreen | VibingScreen

class VibrantApp(App):
    """Terminal UI for managing roadmap execution and Gatekeeper conversations."""

    TITLE = "Vibrant"
    SUB_TITLE = "Multi-agent orchestration control plane"

    CSS = """
    #workspace-host {
        height: 1fr;
    }

    #notification-banner {
        display: none;
        height: auto;
        padding: 0 1;
        background: $warning;
        color: $text;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $primary-background;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("f1", "open_help", "Help", show=True),
        Binding("f2", "toggle_pause", "Pause", show=True),
        Binding("f3", "open_consensus_overlay", "Consensus", show=True),
        Binding("f5", "cycle_agent_output", "Switch Agent", show=True),
        Binding("f10", "quit_app", "Quit", show=True),
        Binding("ctrl+s", "open_settings", "Settings", show=False),
        Binding("f6", "run_next_task", "Run Task", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=True),
    ]

    def __init__(
        self,
        settings: AppSettings | None = None,
        cwd: str | None = None,
        *,
        lifecycle_factory: LifecycleFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or AppSettings()
        if cwd:
            self._settings.default_cwd = cwd

        self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._history = HistoryStore(self._resolve_history_dir(self._settings.history_dir))
        self._lifecycle_factory = lifecycle_factory or CodeAgentLifecycle
        self._lifecycle: CodeAgentLifecycle | None = None
        self._orchestrator: OrchestratorFacade | None = None
        self._workspace_screen: WorkspaceScreen | None = None
        self._task_execution_in_progress = False
        self._task_refresh_loop: asyncio.Task[None] | None = None
        self._roadmap_runner_task: asyncio.Task[None] | None = None
        self._gatekeeper_request_task: asyncio.Task[None] | None = None
        self._known_pending_questions: tuple[str, ...] = ()
        self._paused_return_status: OrchestratorStatus | None = None
        self._banner_text: str | None = None
        self._todo_exit_message: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="notification-banner")
        yield Vertical(id="workspace-host")
        yield Static("Ready", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        """Load project state and route to the correct workspace screen."""

        self.theme = "catppuccin-mocha"
        self._initialize_project_lifecycle()
        self._sync_workspace_screen()
        self.call_after_refresh(self._restore_saved_gatekeeper_thread)
        self.call_after_refresh(self._refresh_project_views)

        if not self._project_has_vibrant_state():
            self._set_status("Project not initialized")
            self.push_screen(InitializationScreen(self._project_root))
            return

        self.call_after_refresh(self._focus_primary_input)

    async def on_unmount(self) -> None:
        for task in (self._gatekeeper_request_task, self._roadmap_runner_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        await self._stop_project_refresh_loop()

    async def action_open_settings(self) -> None:
        result = await self.push_screen_wait(SettingsPanel(self._settings))
        if not result:
            return

        self._settings = result
        self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._history = HistoryStore(self._resolve_history_dir(self._settings.history_dir))
        self._initialize_project_lifecycle()
        self._sync_workspace_screen()
        self.call_after_refresh(self._restore_saved_gatekeeper_thread)
        self.call_after_refresh(self._refresh_project_views)

        if not self._project_has_vibrant_state():
            self._set_status("Project not initialized")
            self.push_screen(InitializationScreen(self._project_root))
            return

        self._focus_primary_input()
        self._set_status("Settings updated")

    async def initialize_project_at(self, target_path: str | Path) -> bool:
        try:
            vibrant_dir = initialize_project(target_path)
        except Exception as exc:
            logger.exception("Failed to initialize Vibrant project")
            self.notify(f"Failed to initialize project: {exc}", severity="error")
            self._set_status(f"Initialization failed: {exc}")
            return False

        project_root = vibrant_dir.parent
        self._settings.default_cwd = str(project_root)
        self._project_root = project_root
        self._history = HistoryStore(self._resolve_history_dir(self._settings.history_dir))
        self._initialize_project_lifecycle()
        self._sync_workspace_screen()
        self.call_after_refresh(self._refresh_project_views)
        self._set_status(f"Initialized Vibrant project in {project_root}")
        self.notify(f"Initialized Vibrant project in {project_root}")
        self.call_after_refresh(self._focus_primary_input)
        return True

    def action_open_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_toggle_pause(self) -> None:
        orchestrator = self._orchestrator
        if orchestrator is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return

        current_status = orchestrator.workflow_status()
        normalized_status = _normalize_orchestrator_status(current_status)
        if normalized_status is OrchestratorStatus.PAUSED:
            next_status = self._paused_return_status or self._infer_resume_status()
        elif normalized_status in {OrchestratorStatus.PLANNING, OrchestratorStatus.EXECUTING}:
            self._paused_return_status = normalized_status
            next_status = OrchestratorStatus.PAUSED
        else:
            label = normalized_status.value if normalized_status is not None else str(current_status)
            self.notify(f"Cannot toggle pause from {label}.", severity="warning")
            return

        try:
            self._transition_workflow_state(next_status)
        except Exception as exc:
            logger.exception("Failed to toggle workflow pause state")
            self.notify(f"Failed to update workflow state: {exc}", severity="error")
            self._set_status(f"Workflow update failed: {exc}")
            return

        if next_status is OrchestratorStatus.PAUSED:
            self._set_status("Workflow paused")
            self.notify("Workflow paused.")
        else:
            self._paused_return_status = None
            self._set_status(f"Workflow resumed ({next_status.value})")
            self.notify(f"Workflow resumed ({next_status.value}).")

        self._refresh_project_views()
        if next_status is not OrchestratorStatus.PAUSED:
            self._start_automatic_workflow_if_needed()

    def action_cycle_agent_output(self) -> None:
        vibing_screen = self._vibing_screen()
        if vibing_screen is None:
            self.notify("Agent logs are only available in the vibing screen.", severity="warning")
            return
        vibing_screen.agent_output.action_cycle_agent()

    def action_open_consensus_overlay(self) -> None:
        vibing_screen = self._vibing_screen()
        if vibing_screen is None:
            self.notify("Consensus view is only available in the vibing screen.", severity="warning")
            return
        vibing_screen.consensus_view.action_open_full_consensus()

    async def action_run_next_task(self) -> None:
        if self._lifecycle is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return
        if self._task_execution_in_progress:
            self.notify("A roadmap task is already running.", severity="warning")
            return

        self._launch_roadmap_runner(notify_when_idle=True)

    async def _run_roadmap_tasks(self, *, notify_when_idle: bool) -> None:
        assert self._lifecycle is not None

        automatic = self._roadmap_execution_mode() is RoadmapExecutionMode.AUTOMATIC
        if automatic and not callable(getattr(self._lifecycle, "execute_until_blocked", None)):
            automatic = False

        self._task_execution_in_progress = True
        self._set_status("Running roadmap workflow…" if automatic else "Running next roadmap task…")
        self._start_project_refresh_loop()
        self._refresh_project_views()

        try:
            if automatic:
                execute_until_blocked = getattr(self._lifecycle, "execute_until_blocked", None)
                if callable(execute_until_blocked):
                    results = await execute_until_blocked()
                    if results:
                        self._handle_task_results(results)
                    elif notify_when_idle:
                        self._handle_task_result(None)
                else:
                    result = await self._lifecycle.execute_next_task()
                    if result is not None:
                        self._handle_task_result(result)
                    elif notify_when_idle:
                        self._handle_task_result(None)
            else:
                result = await self._lifecycle.execute_next_task()
                if result is not None:
                    self._handle_task_result(result)
                elif notify_when_idle:
                    self._handle_task_result(None)
        except Exception as exc:
            logger.exception("Roadmap task execution failed")
            self.notify(f"Task execution failed: {exc}", severity="error")
            self._set_status(f"Task execution failed: {exc}")
        finally:
            self._task_execution_in_progress = False
            self._roadmap_runner_task = None
            await self._stop_project_refresh_loop()
            self._refresh_project_views()

    def _start_automatic_workflow_if_needed(self) -> None:
        orchestrator = self._orchestrator
        if orchestrator is None or self._task_execution_in_progress or self._is_planning_mode():
            return
        if self._roadmap_execution_mode() is not RoadmapExecutionMode.AUTOMATIC:
            return

        snapshot = orchestrator.snapshot()
        if snapshot.pending_questions or snapshot.status in {OrchestratorStatus.PAUSED, OrchestratorStatus.COMPLETED}:
            return

        self._launch_roadmap_runner(notify_when_idle=False)

    def _launch_roadmap_runner(self, *, notify_when_idle: bool) -> None:
        if self._roadmap_runner_task is not None and not self._roadmap_runner_task.done():
            return
        self._roadmap_runner_task = asyncio.create_task(
            self._run_roadmap_tasks(notify_when_idle=notify_when_idle),
            name="vibrant-roadmap-runner",
        )

    def _launch_gatekeeper_message(self, text: str) -> None:
        if self._gatekeeper_request_task is not None and not self._gatekeeper_request_task.done():
            self.notify("Gatekeeper is already running.", severity="warning")
            return
        self._gatekeeper_request_task = asyncio.create_task(
            self._start_gatekeeper_message(text),
            name="vibrant-gatekeeper-message",
        )

    async def _start_gatekeeper_message(self, text: str) -> None:
        assert self._orchestrator is not None
        pending_question = self._current_pending_gatekeeper_question()
        try:
            if pending_question is not None:
                result = await self._orchestrator.answer_pending_question(text, question=pending_question)
            else:
                result = await self._orchestrator.submit_gatekeeper_message(text)
            self._sync_gatekeeper_storage_thread_id(
                getattr(getattr(result, "agent_record", None), "agent_id", None)
            )
            gatekeeper_text = _render_gatekeeper_result_text(result)
            if gatekeeper_text:
                chat_panel = self._chat_panel()
                if chat_panel is not None:
                    chat_panel.record_gatekeeper_response(gatekeeper_text)
            self._persist_gatekeeper_thread()
            if self._maybe_handle_planning_completion_request(result):
                return

            self._refresh_project_views()
            self.notify("Message sent to Gatekeeper.")
            self._set_status("Gatekeeper updated the plan")
            self._start_automatic_workflow_if_needed()
        except Exception as exc:
            self.notify(f"Error: {exc}", severity="error")
            self._set_status(f"Gatekeeper request failed: {exc}")
            self._refresh_gatekeeper_state()
        finally:
            self._gatekeeper_request_task = None

    def _roadmap_execution_mode(self) -> RoadmapExecutionMode:
        if self._lifecycle is None:
            return RoadmapExecutionMode.AUTOMATIC
        mode = getattr(self._lifecycle, "execution_mode", RoadmapExecutionMode.AUTOMATIC)
        if isinstance(mode, RoadmapExecutionMode):
            return mode
        return RoadmapExecutionMode(str(mode).strip().lower())

    def _handle_task_results(self, results: list[CodeAgentLifecycleResult]) -> None:
        for result in results:
            self._handle_task_result(result)

    async def action_quit_app(self) -> None:
        self._persist_gatekeeper_thread()
        self.exit()

    async def on_input_bar_message_submitted(self, event: InputBar.MessageSubmitted) -> None:
        if self._lifecycle is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return

        chat_panel = self._chat_panel()
        input_bar = self._input_bar()
        if chat_panel is None or input_bar is None:
            return

        if self._gatekeeper_is_busy():
            self.notify("Gatekeeper is already running.", severity="warning")
            return

        input_bar.set_enabled(False)
        input_bar.set_context("gatekeeper", "sending…")
        self._set_status("Sending message to Gatekeeper…")
        chat_panel.record_gatekeeper_user_message(
            event.text,
            question=self._current_pending_gatekeeper_question(),
        )
        self._launch_gatekeeper_message(event.text)
        self._refresh_gatekeeper_state()

    async def on_input_bar_slash_command(self, event: InputBar.SlashCommand) -> None:
        cmd = event.command.lower()
        if cmd == "model":
            if event.args:
                self._settings.default_model = event.args
                self._set_status(f"Model set to {event.args}")
            else:
                self.notify(f"Current model: {self._settings.default_model}")
        elif cmd == "settings":
            await self.action_open_settings()
        elif cmd in {"run", "next", "task"}:
            await self.action_run_next_task()
        elif cmd == "refresh":
            self._refresh_project_views()
            self._set_status("Refreshed project views")
        elif cmd == "history":
            vibing_screen = self._vibing_screen()
            if vibing_screen is None:
                self.notify("Gatekeeper chat is already visible.")
                return
            vibing_screen.show_chat_history()
            self._set_status("Opened Gatekeeper chat history")
        elif cmd == "logs":
            vibing_screen = self._vibing_screen()
            if vibing_screen is None:
                self.notify("Agent logs are only available in the vibing screen.", severity="warning")
                return
            vibing_screen.show_agent_logs()
            self._set_status("Opened Agent Logs tab")
        elif cmd == "help":
            self.notify(
                "/model <name> - Set model\n"
                "/run - Execute the next roadmap task\n"
                "/refresh - Reload project state\n"
                "/settings - Open settings\n"
                "/history - Open Gatekeeper chat history\n"
                "/logs - Open Agent Logs\n"
                "/help - Show this help"
            )
        elif cmd == "vibe":
            if self._transition_to_vibing(prefer_chat_history=True):
                self.notify("Entered the vibing phase.")
        else:
            self.notify(f"Unknown command: /{cmd}", severity="warning")

    async def on_initialization_screen_initialize_requested(
        self,
        event: InitializationScreen.InitializeRequested,
    ) -> None:
        if await self.initialize_project_at(event.target_path) and isinstance(self.screen, InitializationScreen):
            self.screen.dismiss(None)

    def on_initialization_screen_exit_requested(self, _: InitializationScreen.ExitRequested) -> None:
        self.exit()

    async def _on_lifecycle_canonical_event(self, event: dict[str, Any]) -> None:
        if _event_requests_planning_completion(event):
            self._transition_to_vibing(prefer_chat_history=True)
            return

        vibing_screen = self._vibing_screen()
        if vibing_screen is not None:
            try:
                vibing_screen.agent_output.ingest_canonical_event(event)
            except Exception:
                logger.exception("Failed to update agent output panel")

        self._handle_gatekeeper_canonical_event(event)

        event_type = str(event.get("type") or "")
        if event_type in {
            "turn.started",
            "turn.completed",
            "runtime.error",
            "task.progress",
            "user-input.requested",
            "gatekeeper.result.applied",
        }:
            self._refresh_project_views()

        if event_type == "turn.started":
            self._set_status(f"Running {event.get('task_id', 'task')}…")
        elif event_type == "turn.completed":
            self._set_status(f"Completed {event.get('task_id', 'task')}")
        elif event_type == "runtime.error":
            self._set_status(str(event.get("error") or "Task failed"))
        elif event_type == "user-input.requested":
            self._refresh_gatekeeper_state(force_flash=True)
        elif event_type == "gatekeeper.result.applied":
            gatekeeper_text = _render_gatekeeper_event_text(event)
            if gatekeeper_text:
                chat_panel = self._chat_panel()
                if chat_panel is not None:
                    chat_panel.record_gatekeeper_response(gatekeeper_text)
                    self._persist_gatekeeper_thread()
            self._refresh_gatekeeper_state(force_flash=bool(event.get("questions")))
            if event.get("error"):
                self.notify(f"Gatekeeper error: {event['error']}", severity="error")
                self._set_status(str(event["error"]))
            else:
                self.notify("Gatekeeper updated the plan.")
                self._set_status("Gatekeeper updated the plan")
            self._start_automatic_workflow_if_needed()

    def _handle_gatekeeper_canonical_event(self, event: dict[str, Any]) -> None:
        if not _is_gatekeeper_event(event):
            return

        chat_panel = self._chat_panel()
        if chat_panel is None:
            return

        if self._sync_gatekeeper_storage_thread_id(event.get("agent_id")):
            self._persist_gatekeeper_thread()

        event_type = str(event.get("type") or "")
        if event_type == "turn.started":
            chat_panel.clear_gatekeeper_streaming_text()
            return
        if event_type == "turn.completed":
            return
        if event_type == "runtime.error":
            streamed_text = chat_panel.get_gatekeeper_streaming_text().strip()
            if not streamed_text:
                error_text = _error_text_from_event(event)
                if error_text:
                    chat_panel.record_gatekeeper_response(f"Error: {error_text}")
                    self._persist_gatekeeper_thread()
            chat_panel.clear_gatekeeper_streaming_text()
            return
        if event_type != "content.delta":
            return

        delta = str(event.get("delta") or "")
        if not delta:
            return

        previous = chat_panel.get_gatekeeper_streaming_text()
        chat_panel.update_gatekeeper_streaming_text(f"{previous}{delta}")
        self._set_status("Gatekeeper is responding…")

    def _initialize_project_lifecycle(self) -> None:
        project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._project_root = project_root
        vibrant_dir = project_root / DEFAULT_CONFIG_DIR
        if not vibrant_dir.exists():
            self._lifecycle = None
            self._orchestrator = None
            return

        try:
            ensure_project_files(project_root)
            self._lifecycle = self._lifecycle_factory(project_root, on_canonical_event=self._on_lifecycle_canonical_event)
            self._orchestrator = OrchestratorFacade(self._lifecycle)
        except Exception as exc:
            logger.exception("Failed to initialize project lifecycle")
            self._lifecycle = None
            self._orchestrator = None
            self.notify(f"Failed to load project state: {exc}", severity="error")

    def _restore_saved_gatekeeper_thread(self) -> None:
        chat_panel = self._chat_panel()
        if chat_panel is None:
            return

        for thread in self._history.list_threads():
            if not self._is_gatekeeper_history_thread(thread):
                continue
            if not self._gatekeeper_history_matches_project(thread):
                continue
            chat_panel.restore_gatekeeper_thread(thread)
            break

    def _project_has_vibrant_state(self) -> bool:
        return (self._project_root / DEFAULT_CONFIG_DIR).exists()

    def _focus_primary_input(self) -> None:
        with suppress(Exception):
            if self._workspace_screen is not None:
                self._workspace_screen.focus_primary_input()

    def _workspace_host(self) -> Vertical:
        return self.query_one("#workspace-host", Vertical)

    def _mount_workspace(self, workspace: WorkspaceScreen) -> None:
        host = self._workspace_host()
        previous_chat = self._chat_panel()
        gatekeeper_thread = None
        if previous_chat is not None:
            gatekeeper_thread = previous_chat.get_persisted_gatekeeper_thread() or previous_chat.get_gatekeeper_thread()

        host.remove_children()
        host.mount(workspace)
        self._workspace_screen = workspace

        if gatekeeper_thread is not None:
            self.call_after_refresh(self._restore_workspace_gatekeeper_thread, workspace, gatekeeper_thread)


    def _restore_workspace_gatekeeper_thread(self, workspace: WorkspaceScreen, thread: ThreadInfo) -> None:
        if self._workspace_screen is not workspace:
            return
        with suppress(Exception):
            workspace.chat_panel.restore_gatekeeper_thread(thread)

    def _apply_workspace_placeholder(self, placeholder: str) -> None:
        if self._workspace_screen is None:
            return
        with suppress(Exception):
            self._workspace_screen.set_input_placeholder(placeholder)

    def _sync_workspace_screen(self, *, prefer_chat_history: bool = False) -> None:
        planning_mode = self._lifecycle is None or self._is_planning_mode()
        self.set_class(planning_mode, "planning-mode")
        self.set_class(not planning_mode, "vibing-mode")

        if planning_mode:
            if not isinstance(self._workspace_screen, PlanningScreen):
                self._mount_workspace(PlanningScreen())
        else:
            initial_tab = "chat-history" if prefer_chat_history else "task-status"
            if isinstance(self._workspace_screen, VibingScreen):
                if prefer_chat_history:
                    self._workspace_screen.show_chat_history()
            else:
                self._mount_workspace(VibingScreen(initial_tab=initial_tab))

        if self._workspace_screen is None:
            return

        placeholder = "Tell me what you want to build" if planning_mode else InputBar.DEFAULT_PLACEHOLDER
        self.call_after_refresh(self._apply_workspace_placeholder, placeholder)

    def _transition_to_vibing(self, *, prefer_chat_history: bool) -> bool:
        orchestrator = self._orchestrator
        if orchestrator is None:
            self.notify("Initialize a project before entering the vibing phase.", severity="warning")
            return False

        try:
            for _ in range(2):
                current_status = _normalize_orchestrator_status(orchestrator.workflow_status())
                if current_status not in {OrchestratorStatus.INIT, OrchestratorStatus.PLANNING}:
                    break
                next_status = (
                    OrchestratorStatus.PLANNING
                    if current_status is OrchestratorStatus.INIT
                    else OrchestratorStatus.EXECUTING
                )
                self._transition_workflow_state(next_status)
        except Exception as exc:
            logger.exception("Failed to enter vibing phase")
            self.notify(f"Failed to enter vibing phase: {exc}", severity="error")
            self._set_status(f"Failed to enter vibing phase: {exc}")
            return False

        self._todo_exit_message = None
        self._sync_workspace_screen(prefer_chat_history=prefer_chat_history)
        self.call_after_refresh(self._refresh_project_views)
        self._set_status("Entered vibing phase")
        self._start_automatic_workflow_if_needed()
        return True

    def _resolve_history_dir(self, history_dir: str) -> str:
        return str(resolve_project_path(history_dir, project_root=self._project_root))

    def _refresh_project_views(self) -> None:
        self._sync_workspace_screen()
        vibing_screen = self._vibing_screen()
        orchestrator = self._orchestrator
        if orchestrator is None:
            if vibing_screen is not None:
                try:
                    vibing_screen.plan_tree.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
                    vibing_screen.agent_output.clear_agents("No `.vibrant/roadmap.md` found for this workspace.")
                    vibing_screen.consensus_view.clear_summary("No `.vibrant/consensus.md` found for this workspace.")
                    vibing_screen.set_roadmap_loading(True)
                except NoMatches:
                    pass
            self._refresh_gatekeeper_state()
            return

        snapshot = orchestrator.snapshot()
        if vibing_screen is not None:
            try:
                vibing_screen.agent_output.sync_agents(snapshot.agent_records)
            except NoMatches:
                vibing_screen = None

        consensus_document = snapshot.consensus
        consensus_path = snapshot.consensus_path
        try:
            roadmap = orchestrator.reload_from_disk()
            refreshed = orchestrator.snapshot()
            consensus_document = refreshed.consensus
            consensus_path = refreshed.consensus_path
        except Exception as exc:
            logger.exception("Failed to refresh roadmap view")
            if vibing_screen is not None:
                try:
                    vibing_screen.plan_tree.clear_tasks(f"Failed to load roadmap: {exc}")
                    vibing_screen.consensus_view.update_consensus(
                        consensus_document,
                        source_path=consensus_path,
                    )
                except NoMatches:
                    pass
            self._refresh_gatekeeper_state()
            return

        if vibing_screen is not None:
            try:
                vibing_screen.plan_tree.update_tasks(
                    roadmap.tasks,
                    agent_summaries=self._collect_task_summaries(),
                )
                vibing_screen.consensus_view.update_consensus(
                    consensus_document,
                    tasks=roadmap.tasks,
                    source_path=consensus_path,
                )
                vibing_screen.set_roadmap_loading(not bool(roadmap.tasks))
            except NoMatches:
                pass

        self._refresh_gatekeeper_state()

    def _collect_task_summaries(self) -> dict[str, str]:
        if self._orchestrator is None:
            return {}
        return self._orchestrator.task_summaries()

    def _handle_task_result(self, result: CodeAgentLifecycleResult | None) -> None:
        orchestrator = self._orchestrator
        if result is None:
            if orchestrator and orchestrator.workflow_status() is OrchestratorStatus.COMPLETED:
                self.notify("Workflow completed.")
                self._set_status("Workflow completed")
            elif orchestrator and orchestrator.pending_questions():
                banner = orchestrator.user_input_banner()
                self.notify(banner, severity="warning")
                self._set_status(banner)
            else:
                self._notify_no_ready_task()
            return

        if result.gatekeeper_result is not None:
            gatekeeper_text = _render_gatekeeper_result_text(result.gatekeeper_result)
            if gatekeeper_text:
                chat_panel = self._chat_panel()
                if chat_panel is not None:
                    chat_panel.record_gatekeeper_response(gatekeeper_text)
                    self._persist_gatekeeper_thread()

        if result.outcome == "accepted":
            completed = bool(orchestrator and orchestrator.workflow_status() is OrchestratorStatus.COMPLETED)
            if completed:
                self.notify(f"Task {result.task_id} accepted and merged. Workflow completed.")
                self._set_status(f"Task {result.task_id} accepted · workflow completed")
            else:
                self.notify(f"Task {result.task_id} accepted and merged.")
                self._set_status(f"Task {result.task_id} accepted and merged")
        elif result.outcome == "retried":
            self.notify(f"Task {result.task_id} queued for retry.", severity="warning")
            self._set_status(f"Task {result.task_id} queued for retry")
        elif result.outcome == "escalated":
            self.notify(f"Task {result.task_id} escalated to the user.", severity="warning")
            self._set_status(f"Task {result.task_id} escalated to the user")
        elif result.outcome == "awaiting_user":
            self.notify(
                orchestrator.user_input_banner() if orchestrator else "User input required.",
                severity="warning",
            )
        else:
            self._set_status(f"Task result: {result.outcome}")

    def _notify_no_ready_task(self) -> None:
        self.notify("No ready roadmap task found.", severity="information")
        self._set_status("No ready roadmap task found")

    def _persist_gatekeeper_thread(self) -> None:
        chat_panel = self._chat_panel()
        if chat_panel is None:
            return

        gatekeeper_thread = chat_panel.get_persisted_gatekeeper_thread()
        if gatekeeper_thread is None or not gatekeeper_thread.turns:
            return

        gatekeeper_thread.cwd = str(self._project_root)
        self._history.save_thread(gatekeeper_thread)

    def _sync_gatekeeper_storage_thread_id(self, thread_id: str | None) -> bool:
        chat_panel = self._chat_panel()
        if chat_panel is None:
            return False
        return chat_panel.set_gatekeeper_storage_thread_id(thread_id)

    @staticmethod
    def _is_gatekeeper_history_thread(thread: ThreadInfo) -> bool:
        return thread.id == ChatPanel.GATEKEEPER_THREAD_ID or thread.model == "gatekeeper"

    def _gatekeeper_history_matches_project(self, thread: ThreadInfo) -> bool:
        if thread.id == ChatPanel.GATEKEEPER_THREAD_ID:
            return True
        if not thread.cwd:
            return False
        return Path(thread.cwd).expanduser().resolve() == self._project_root

    def _pending_gatekeeper_questions(self) -> list[str]:
        if self._orchestrator is None:
            return []
        return self._orchestrator.pending_questions()

    def _current_pending_gatekeeper_question(self) -> str | None:
        if self._orchestrator is None:
            return None
        return self._orchestrator.current_pending_question()

    def _gatekeeper_is_busy(self) -> bool:
        return bool(
            self._gatekeeper_request_task is not None and not self._gatekeeper_request_task.done()
        ) or bool(getattr(self._lifecycle, "gatekeeper_busy", False))

    def _refresh_gatekeeper_state(self, *, force_flash: bool = False) -> None:
        chat_panel = self._chat_panel()
        input_bar = self._input_bar()
        if chat_panel is None or input_bar is None:
            return

        questions = self._pending_gatekeeper_questions()
        status = self._orchestrator.workflow_status() if self._orchestrator is not None else None

        normalized_status = _normalize_orchestrator_status(status)
        if normalized_status in {OrchestratorStatus.PLANNING, OrchestratorStatus.EXECUTING}:
            self._paused_return_status = normalized_status

        new_questions = [question for question in questions if question not in self._known_pending_questions]
        flash = force_flash or bool(new_questions)
        chat_panel.set_gatekeeper_state(
            status=normalized_status or status,
            pending_questions=questions,
            flash=flash,
        )
        chat_panel.show_gatekeeper_thread()

        if questions and not self._gatekeeper_is_busy():
            banner = (
                self._orchestrator.user_input_banner()
                if self._orchestrator is not None
                else "⚠ Gatekeeper needs your input — see Chat panel"
            )
            self._set_banner(banner)
            input_bar.set_enabled(True)
            input_bar.set_context("gatekeeper", "awaiting answer")
            if flash:
                self.notify(banner, severity="warning")
                self._set_status(banner)
                if self._orchestrator is not None and self._orchestrator.notification_bell_enabled():
                    with suppress(Exception):
                        self.bell()
        elif self._gatekeeper_is_busy():
            self._set_banner("Gatekeeper is responding…")
            input_bar.set_enabled(False)
            input_bar.set_context("gatekeeper", "running…")
        else:
            self._set_banner(None)
            if normalized_status is OrchestratorStatus.INIT:
                input_bar.set_enabled(True)
                input_bar.set_context("gatekeeper", "describe your goal")
            elif normalized_status is OrchestratorStatus.PLANNING:
                input_bar.set_enabled(True)
                input_bar.set_context("gatekeeper", "planning")
            elif normalized_status is OrchestratorStatus.PAUSED:
                input_bar.set_enabled(True)
                input_bar.set_context("workflow", "paused")
            else:
                input_bar.set_enabled(True)
                input_bar.set_context("gatekeeper", "feedback")

        self._known_pending_questions = tuple(questions)

    def _chat_panel(self) -> ChatPanel | None:
        if self._workspace_screen is not None:
            with suppress(Exception):
                return self._workspace_screen.chat_panel
        with suppress(Exception):
            return self.query_one(ChatPanel)
        return None

    def _input_bar(self) -> InputBar | None:
        if self._workspace_screen is not None:
            with suppress(Exception):
                return self._workspace_screen.input_bar
        with suppress(Exception):
            return self.query_one(InputBar)
        return None

    def _vibing_screen(self) -> VibingScreen | None:
        if isinstance(self._workspace_screen, VibingScreen):
            return self._workspace_screen
        return None

    def _infer_resume_status(self) -> OrchestratorStatus:
        orchestrator = self._orchestrator
        if orchestrator is None:
            return OrchestratorStatus.EXECUTING

        consensus = orchestrator.consensus_document()
        if consensus is not None:
            mapped = {
                ConsensusStatus.PLANNING: OrchestratorStatus.PLANNING,
                ConsensusStatus.EXECUTING: OrchestratorStatus.EXECUTING,
            }.get(consensus.status)
            if mapped is not None:
                return mapped

        roadmap_document = orchestrator.roadmap_document
        if roadmap_document is None:
            roadmap_document = orchestrator.reload_from_disk()
        return OrchestratorStatus.EXECUTING if getattr(roadmap_document, "tasks", None) else OrchestratorStatus.PLANNING

    def _transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        orchestrator = self._orchestrator
        if orchestrator is None:
            raise RuntimeError("Project lifecycle is not initialized")

        current_status = _normalize_orchestrator_status(orchestrator.workflow_status())
        if current_status is next_status:
            return
        if next_status is OrchestratorStatus.PAUSED:
            orchestrator.pause_workflow()
            return
        if current_status is OrchestratorStatus.PAUSED and next_status is OrchestratorStatus.EXECUTING:
            orchestrator.resume_workflow()
            return
        orchestrator.transition_workflow_state(next_status)

    def _set_banner(self, text: str | None) -> None:
        self._banner_text = text.strip() if text else None
        try:
            banner = self.query_one("#notification-banner", Static)
        except Exception:
            return

        if self._banner_text:
            banner.update(self._banner_text)
            banner.display = True
        else:
            banner.update("")
            banner.display = False

    def get_banner_text(self) -> str | None:
        return self._banner_text

    def _set_status(self, text: str) -> None:
        with suppress(Exception):
            self.query_one("#status-bar", Static).update(text)

    def _start_project_refresh_loop(self) -> None:
        if self._task_refresh_loop is not None and not self._task_refresh_loop.done():
            return
        self._task_refresh_loop = asyncio.create_task(self._project_refresh_loop(), name="vibrant-project-refresh")

    async def _stop_project_refresh_loop(self) -> None:
        if self._task_refresh_loop is None:
            return
        self._task_refresh_loop.cancel()
        with suppress(asyncio.CancelledError):
            await self._task_refresh_loop
        self._task_refresh_loop = None

    async def _project_refresh_loop(self) -> None:
        try:
            while self._task_execution_in_progress:
                self._refresh_project_views()
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise

    def _is_planning_mode(self) -> bool:
        if self._orchestrator is None:
            return False
        status = _normalize_orchestrator_status(self._orchestrator.workflow_status())
        return status in {OrchestratorStatus.INIT, OrchestratorStatus.PLANNING}

    def _maybe_handle_planning_completion_request(self, result: object) -> bool:
        if _extract_planning_completion_request(result) is None:
            return False
        self._todo_exit_message = None
        return self._transition_to_vibing(prefer_chat_history=True)

    def get_todo_exit_message(self) -> str | None:
        return self._todo_exit_message


def _normalize_orchestrator_status(status: object) -> OrchestratorStatus | None:
    if isinstance(status, OrchestratorStatus):
        return status
    if isinstance(status, str):
        normalized = status.strip().lower()
        try:
            return OrchestratorStatus(normalized)
        except ValueError:
            return None
    return None


def _is_gatekeeper_event(event: dict[str, Any]) -> bool:
    agent_id = event.get("agent_id")
    if isinstance(agent_id, str) and agent_id.startswith("gatekeeper-"):
        return True

    task_id = event.get("task_id")
    return isinstance(task_id, str) and task_id.startswith("gatekeeper-")


def _error_text_from_event(event: dict[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "").strip()


def _render_gatekeeper_event_text(event: dict[str, Any]) -> str:
    transcript = event.get("transcript")
    if isinstance(transcript, str) and transcript.strip():
        return transcript.strip()

    verdict = event.get("verdict")
    if isinstance(verdict, str) and verdict.strip():
        return f"Verdict: {verdict.strip()}"

    return ""


def _render_gatekeeper_result_text(result: object) -> str:
    transcript = getattr(result, "transcript", None)
    if isinstance(transcript, str) and transcript.strip():
        return transcript.strip()

    verdict = getattr(result, "verdict", None)
    if isinstance(verdict, str) and verdict.strip():
        return f"Verdict: {verdict.strip()}"

    return "Gatekeeper updated the plan."


def _extract_planning_completion_request(result: object) -> str | None:
    transcript = getattr(result, "transcript", None)
    if isinstance(transcript, str):
        for line in transcript.splitlines():
            if line.strip() == PLANNING_COMPLETE_MCP_SENTINEL:
                return PLANNING_COMPLETE_MCP_TOOL

    events = getattr(result, "events", None)
    if isinstance(events, list):
        for event in events:
            if _event_requests_planning_completion(event):
                return PLANNING_COMPLETE_MCP_TOOL

    return None


def _event_requests_planning_completion(event: object) -> bool:
    if not isinstance(event, dict):
        return False

    candidates = [
        event.get("tool_name"),
        event.get("tool"),
        event.get("name"),
        event.get("endpoint"),
        event.get("method"),
    ]
    return any(isinstance(candidate, str) and candidate.strip() == PLANNING_COMPLETE_MCP_TOOL for candidate in candidates)
