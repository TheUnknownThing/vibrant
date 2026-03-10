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
from textual.widgets import Footer, Header, Static

from ..config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, find_project_root, resolve_project_path
from ..consensus import ConsensusParser, ConsensusWriter
from ..gatekeeper import PLANNING_COMPLETE_MCP_SENTINEL, PLANNING_COMPLETE_MCP_TOOL
from ..history import HistoryStore
from ..models import AppSettings, ConsensusStatus, OrchestratorStatus, ThreadInfo, ThreadStatus
from ..orchestrator import CodeAgentLifecycle, CodeAgentLifecycleResult
from ..project_init import ensure_project_files, initialize_project
from ..session_manager import (
    ApprovalRequested,
    ItemAdded,
    SessionEvent,
    SessionManager,
    StreamingDelta,
    ThreadCreated,
    ThreadStatusChanged,
    TurnCompleted,
)
from .screens import HelpScreen, InitializationScreen, PlanningScreen, VibingScreen
from .widgets.agent_output import AgentOutput
from .widgets.chat_panel import ChatPanel
from .widgets.consensus_view import ConsensusView
from .widgets.input_bar import InputBar
from .widgets.plan_tree import PlanTree
from .widgets.settings_panel import SettingsPanel
from .widgets.task_status import TaskStatusView

logger = logging.getLogger(__name__)
LifecycleFactory = Callable[..., CodeAgentLifecycle]

_WORKFLOW_TO_CONSENSUS = {
    OrchestratorStatus.INIT: ConsensusStatus.INIT,
    OrchestratorStatus.PLANNING: ConsensusStatus.PLANNING,
    OrchestratorStatus.EXECUTING: ConsensusStatus.EXECUTING,
    OrchestratorStatus.PAUSED: ConsensusStatus.PAUSED,
    OrchestratorStatus.COMPLETED: ConsensusStatus.COMPLETED,
}


class VibrantApp(App):
    """Terminal UI for managing roadmap execution and Codex conversations."""

    TITLE = "Vibrant"
    SUB_TITLE = "Multi-agent orchestration control plane"

    CSS = """
    #workspace-host {
        height: 1fr;
    }

    #empty-state {
        height: 100%;
        content-align: center middle;
        text-align: center;
        padding: 4;
    }

    #conversation-scroll {
        height: 1fr;
        padding: 0 1;
    }

    MessageBubble {
        margin: 1 0;
        padding: 0 1;
    }

    .user-msg {
        background: $primary 15%;
        border-left: tall $primary;
    }

    .assistant-msg {
        background: $secondary 10%;
        border-left: tall $secondary;
    }

    .msg-role {
        margin-bottom: 0;
    }

    .msg-content {
        margin-top: 0;
    }

    .msg-command {
        background: $surface;
        padding: 0 1;
        margin: 0;
    }

    .msg-command-header {
        background: $primary-background;
        padding: 0 1;
        margin: 0;
    }

    .msg-command-output {
        background: $surface;
        padding: 0 1;
        margin: 0;
        color: $text-muted;
        max-height: 20;
        overflow-y: auto;
    }

    .msg-reasoning {
        margin: 0;
        padding: 0 1;
    }

    .msg-file {
        margin: 0;
    }

    #streaming-wrapper {
        margin: 1 0;
        padding: 0 1;
    }

    #input-panel {
        height: auto;
        max-height: 8;
        border-top: solid $primary-background;
        padding: 0 1;
        background: $surface;
    }

    #input-context {
        height: 1;
        padding: 0 1;
    }

    #message-input {
        margin: 0 0 1 0;
    }

    .command-collapsible,
    .reasoning-collapsible {
        margin: 0;
        padding: 0;
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
        session_manager: SessionManager | None = None,
        lifecycle_factory: LifecycleFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or AppSettings()
        if cwd:
            self._settings.default_cwd = cwd
        self._session_manager = session_manager or SessionManager()
        self._active_thread_id: str | None = None
        self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._history = HistoryStore(self._resolve_history_dir(self._settings.history_dir))
        self._lifecycle_factory = lifecycle_factory or CodeAgentLifecycle
        self._lifecycle: CodeAgentLifecycle | None = None
        self._task_execution_in_progress = False
        self._task_refresh_loop: asyncio.Task[None] | None = None
        self._roadmap_runner_task: asyncio.Task[None] | None = None
        self._gatekeeper_request_task: asyncio.Task[None] | None = None
        self._known_pending_questions: tuple[str, ...] = ()
        self._paused_return_status: OrchestratorStatus | None = None
        self._banner_text: str | None = None
        self._gatekeeper_focus_initialized = False
        self._todo_exit_message: str | None = None
        self._workspace_screen: PlanningScreen | VibingScreen | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="notification-banner")
        yield Vertical(id="workspace-host")
        yield Static("Ready", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        """Wire up event listeners and load persisted project/thread state."""

        self.theme = "catppuccin-mocha"
        self._session_manager.add_listener(self._on_session_event)
        self._initialize_project_lifecycle()
        self._sync_workspace_screen()

        saved_threads = self._history.list_threads()
        restored_gatekeeper = False
        if saved_threads:
            for thread in saved_threads:
                if self._is_gatekeeper_history_thread(thread):
                    if not restored_gatekeeper and self._gatekeeper_history_matches_project(thread):
                        self.query_one(ChatPanel).restore_gatekeeper_thread(thread)
                        restored_gatekeeper = True
                    continue
                self._session_manager._threads[thread.id] = thread
            self._set_status(f"Loaded {len(saved_threads)} saved thread(s)")

        self._refresh_thread_list()
        self._refresh_project_views()
        if not self._project_has_vibrant_state():
            self._set_status("Project not initialized")
            self.push_screen(InitializationScreen(self._project_root))
            return
        self.query_one(InputBar).focus_input()

    async def on_unmount(self) -> None:
        for task in (self._gatekeeper_request_task, self._roadmap_runner_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if self._task_refresh_loop is not None:
            self._task_refresh_loop.cancel()
            with suppress(asyncio.CancelledError):
                await self._task_refresh_loop
        await self._session_manager.stop_all()

    async def action_new_thread(self) -> None:
        self._set_status("Creating new thread…")
        try:
            config = self._settings.to_session_config()
            thread = await self._session_manager.create_session(config)
            self._active_thread_id = thread.id
            self._refresh_thread_list()
            self._show_thread(thread)
            self._set_status(f"Thread created · {thread.model or 'default'}")
        except Exception as exc:
            self._set_status(f"Error: {exc}")
            self.notify(f"Failed to create thread: {exc}", severity="error")

    async def action_cycle_thread(self) -> None:
        threads = self._conversation_threads()
        if not threads:
            return
        if self._active_thread_id is None:
            self._active_thread_id = threads[0].id
        else:
            ids = [thread.id for thread in threads]
            try:
                index = ids.index(self._active_thread_id)
                self._active_thread_id = ids[(index + 1) % len(ids)]
            except ValueError:
                self._active_thread_id = ids[0]
        thread = self._find_conversation_thread(self._active_thread_id)
        if thread:
            self._show_conversation(thread)
            self._refresh_thread_list()

    async def action_open_settings(self) -> None:
        result = await self.push_screen_wait(SettingsPanel(self._settings))
        if result:
            self._settings = result
            self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
            self._history = HistoryStore(self._resolve_history_dir(self._settings.history_dir))
            self._initialize_project_lifecycle()
            self._refresh_project_views()
            if not self._project_has_vibrant_state():
                self._set_status("Project not initialized")
                self.push_screen(InitializationScreen(self._project_root))
                return
            self.query_one(InputBar).focus_input()
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
        self._initialize_project_lifecycle()
        self._sync_workspace_screen(prefer_chat_history=self._is_planning_mode())
        self._refresh_project_views()
        self._set_status(f"Initialized Vibrant project in {project_root}")
        self.notify(f"Initialized Vibrant project in {project_root}")
        self.call_after_refresh(self._focus_primary_input)
        return True

    def action_open_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_toggle_pause(self) -> None:
        if self._lifecycle is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return

        engine = self._lifecycle.engine
        current_status = engine.state.status
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
        agent_output = self._query_optional(AgentOutput)
        if agent_output is None:
            self.notify("Agent logs are only available in the vibing screen.", severity="warning")
            return
        agent_output.action_cycle_agent()

    def action_open_consensus_overlay(self) -> None:
        consensus_view = self._query_optional(ConsensusView)
        if consensus_view is None:
            self.notify("Consensus view is only available in the vibing screen.", severity="warning")
            return
        consensus_view.action_open_full_consensus()

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
                    if not results:
                        if notify_when_idle:
                            self._handle_task_result(None)
                    else:
                        self._handle_task_results(results)
                else:
                    result = await self._lifecycle.execute_next_task()
                    if result is None:
                        if notify_when_idle:
                            self._handle_task_result(None)
                    else:
                        self._handle_task_result(result)
            else:
                result = await self._lifecycle.execute_next_task()
                if result is None:
                    if notify_when_idle:
                        self._handle_task_result(None)
                else:
                    self._handle_task_result(result)
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
        if self._lifecycle is None or self._task_execution_in_progress:
            return
        if self._is_planning_mode():
            return
        if self._roadmap_execution_mode() is not RoadmapExecutionMode.AUTOMATIC:
            return

        engine = self._lifecycle.engine
        if engine.state.pending_questions or engine.state.status in {OrchestratorStatus.PAUSED, OrchestratorStatus.COMPLETED}:
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
        assert self._lifecycle is not None
        try:
            start_message = getattr(self._lifecycle, "start_gatekeeper_message", None)
            if callable(start_message):
                handle = await start_message(text)
                self._sync_gatekeeper_storage_thread_id(
                    getattr(getattr(handle, "agent_record", None), "agent_id", None)
                )
                self._persist_gatekeeper_thread()
                self._set_status("Gatekeeper is responding…")
            else:
                raise AttributeError("Lifecycle does not support async Gatekeeper messages")
        except Exception as exc:
            self.notify(f"Error: {exc}", severity="error")
            self._set_status(f"Gatekeeper start failed: {exc}")
            self._sync_chat_panel_state()
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

    async def action_delete_thread(self) -> None:
        if not self._active_thread_id:
            return
        thread_id = self._active_thread_id
        if thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
            self.notify("The Gatekeeper conversation cannot be deleted.", severity="warning")
            return
        await self._session_manager.stop_session(thread_id)
        self._history.delete_thread(thread_id)
        threads = self._conversation_threads()
        remaining = [thread for thread in threads if thread.id != thread_id]
        if remaining:
            self._active_thread_id = remaining[0].id
            self._show_conversation(remaining[0])
        else:
            self._active_thread_id = None
            self.query_one(ChatPanel).clear()
        self._refresh_thread_list()
        self._sync_chat_panel_state()
        self._set_status("Thread deleted")

    async def action_quit_app(self) -> None:
        for thread in self._session_manager.list_threads():
            self._history.save_thread(thread)
        self._persist_gatekeeper_thread()
        await self._session_manager.stop_all()
        self.exit()

    async def on_input_bar_message_submitted(self, event: InputBar.MessageSubmitted) -> None:
        pending_question = self._current_pending_gatekeeper_question()
        input_bar = self.query_one(InputBar)
        if self._should_route_input_to_gatekeeper() and self._lifecycle is not None:
            if self._gatekeeper_request_task is not None and not self._gatekeeper_request_task.done():
                self.notify("Gatekeeper is already running.", severity="warning")
                return
            if bool(getattr(self._lifecycle, "gatekeeper_busy", False)):
                self.notify("Gatekeeper is already running.", severity="warning")
                return
            input_bar.set_enabled(False)
            input_bar.set_context("gatekeeper", "sending…")
            self._set_status("Sending message to Gatekeeper…")
            chat_panel = self.query_one(ChatPanel)
            chat_panel.record_gatekeeper_user_message(event.text, question=pending_question)

            start_message = getattr(self._lifecycle, "start_gatekeeper_message", None)
            if callable(start_message):
                self._launch_gatekeeper_message(event.text)
                self._refresh_thread_list()
                self._sync_chat_panel_state()
            else:
                try:
                    submit_message = getattr(self._lifecycle, "submit_gatekeeper_message", None)
                    if callable(submit_message):
                        result = await submit_message(event.text)
                    elif pending_question is not None:
                        result = await self._lifecycle.engine.answer_pending_question(
                            self._lifecycle.gatekeeper,
                            answer=event.text,
                            question=pending_question,
                        )
                    else:
                        raise AttributeError("Lifecycle does not support Gatekeeper planning messages")
                except Exception as exc:
                    self.notify(f"Error: {exc}", severity="error")
                    self._sync_chat_panel_state()
                else:
                    self._sync_gatekeeper_storage_thread_id(
                        getattr(getattr(result, "agent_record", None), "agent_id", None)
                    )
                    gatekeeper_text = _render_gatekeeper_result_text(result)
                    if gatekeeper_text:
                        chat_panel.record_gatekeeper_response(gatekeeper_text)
                    self._persist_gatekeeper_thread()
                    if self._maybe_handle_planning_completion_request(result):
                        return
                    self._refresh_project_views()
                    self.notify("Message sent to Gatekeeper.")
                    self._set_status("Gatekeeper updated the plan")
                    self._start_automatic_workflow_if_needed()
            return

        if not self._active_thread_id:
            self.notify("Create a thread first (Ctrl+N)", severity="warning")
            return
        if self._active_thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
            self.notify("Gatekeeper is the active conversation. Type your planning message directly.", severity="warning")
            return
        thread = self._session_manager.get_thread(self._active_thread_id)
        if not thread or thread.status == ThreadStatus.RUNNING:
            self.notify("Thread is busy", severity="warning")
            return

        input_bar.set_enabled(False)
        input_bar.set_context(thread.model, "sending…")
        self._set_status("Sending message…")

        try:
            await self._session_manager.send_message(self._active_thread_id, event.text)
        except Exception as exc:
            self.notify(f"Error: {exc}", severity="error")
            input_bar.set_enabled(True)

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
            self._refresh_thread_list()
            self._set_status("Refreshed project and thread views")
        elif cmd == "history":
            self.notify("Conversation history stays visible in the active chat panel.")
        elif cmd == "logs":
            if not self._active_thread_id:
                self.notify("Create a thread first (Ctrl+N)", severity="warning")
                return
            if self._active_thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
                self.notify("Provider logs are only available for Codex chat threads.", severity="warning")
                return
            native_log, canonical_log = self._session_manager.get_provider_log_paths(self._active_thread_id)
            if native_log or canonical_log:
                self.notify(f"Native log: {native_log or 'n/a'}\nCanonical log: {canonical_log or 'n/a'}")
            else:
                self.notify("No provider logs available for this thread", severity="warning")
        elif cmd == "help":
            self.notify(
                "/model <name> - Set model\n"
                "/run - Execute the next roadmap task\n"
                "/refresh - Reload project state\n"
                "/settings - Open settings\n"
                "/logs - Show provider log paths\n"
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
        if await self.initialize_project_at(event.target_path):
            if isinstance(self.screen, InitializationScreen):
                self.screen.dismiss(None)

    def on_initialization_screen_exit_requested(self, _: InitializationScreen.ExitRequested) -> None:
        self.exit()

    async def _on_session_event(self, event: SessionEvent) -> None:
        thread = self._session_manager.get_thread(event.thread_id)

        if isinstance(event, ThreadCreated):
            self._refresh_thread_list()

        elif isinstance(event, ThreadStatusChanged):
            self._refresh_thread_list()
            if event.thread_id == self._active_thread_id:
                input_bar = self.query_one(InputBar)
                if event.status in (ThreadStatus.IDLE, ThreadStatus.STOPPED, ThreadStatus.ERROR):
                    input_bar.set_enabled(True)
                    if thread:
                        input_bar.set_context(thread.model, event.status.value)
                    if event.status == ThreadStatus.IDLE:
                        self._set_status("Ready")
                elif event.status == ThreadStatus.RUNNING:
                    input_bar.set_enabled(False)
                    input_bar.set_context(thread.model if thread else None, "running…")

        elif isinstance(event, StreamingDelta):
            if event.thread_id == self._active_thread_id:
                self.query_one(ChatPanel).update_streaming_text(event.accumulated_text)
                self._set_status("Receiving…")

        elif isinstance(event, ItemAdded):
            if event.thread_id == self._active_thread_id and thread:
                self.query_one(ChatPanel).show_thread(thread)

        elif isinstance(event, TurnCompleted):
            if event.thread_id == self._active_thread_id and thread:
                self.query_one(ChatPanel).show_thread(thread)
                self._history.save_thread(thread)
                self._set_status("Turn completed")

        elif isinstance(event, ApprovalRequested):
            if self._settings.default_approval_mode.value == "full-auto":
                await self._session_manager.approve_request(event.thread_id, event.jsonrpc_id, approved=True)
            else:
                self.notify(
                    f"Approval requested: {event.method}\nUse /approve or /reject",
                    severity="warning",
                )

        self._sync_chat_panel_state()

    async def _on_lifecycle_canonical_event(self, event: dict[str, Any]) -> None:
        if _event_requests_planning_completion(event):
            self._transition_to_vibing(prefer_chat_history=True)
            return

        try:
            self.query_one(AgentOutput).ingest_canonical_event(event)
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
            self._sync_chat_panel_state(force_flash=True)
        elif event_type == "gatekeeper.result.applied":
            gatekeeper_text = _render_gatekeeper_event_text(event)
            if gatekeeper_text:
                self.query_one(ChatPanel).record_gatekeeper_response(gatekeeper_text)
                self._persist_gatekeeper_thread()
            self._sync_chat_panel_state(force_flash=bool(event.get("questions")))
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

        chat_panel = self.query_one(ChatPanel)
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
        if not previous:
            self._refresh_thread_list()
        self._set_status("Gatekeeper is responding…")

    def _initialize_project_lifecycle(self) -> None:
        project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._project_root = project_root
        vibrant_dir = project_root / DEFAULT_CONFIG_DIR
        if not vibrant_dir.exists():
            self._lifecycle = None
            self._gatekeeper_focus_initialized = False
            return

        try:
            ensure_project_files(project_root)
            self._lifecycle = self._lifecycle_factory(project_root, on_canonical_event=self._on_lifecycle_canonical_event)
            self._gatekeeper_focus_initialized = False
        except Exception as exc:
            logger.exception("Failed to initialize project lifecycle")
            self._lifecycle = None
            self._gatekeeper_focus_initialized = False
            self.notify(f"Failed to load project state: {exc}", severity="error")

    def _project_has_vibrant_state(self) -> bool:
        return (self._project_root / DEFAULT_CONFIG_DIR).exists()

    def _focus_primary_input(self) -> None:
        with suppress(Exception):
            self.query_one(InputBar).focus_input()

    def _query_optional(self, selector: object, expect_type: type | None = None):
        with suppress(Exception):
            if expect_type is None:
                return self.query_one(selector)
            return self.query_one(selector, expect_type)
        return None

    def _workspace_host(self) -> Vertical:
        return self.query_one("#workspace-host", Vertical)

    def _mount_workspace(self, workspace: PlanningScreen | VibingScreen) -> None:
        host = self._workspace_host()
        previous_chat = self._query_optional(ChatPanel)
        gatekeeper_thread = None
        if previous_chat is not None:
            gatekeeper_thread = previous_chat.get_persisted_gatekeeper_thread() or previous_chat.get_gatekeeper_thread()

        host.remove_children()
        host.mount(workspace)
        self._workspace_screen = workspace

        chat_panel = self._query_optional(ChatPanel)
        if chat_panel is not None and gatekeeper_thread is not None:
            chat_panel.restore_gatekeeper_thread(gatekeeper_thread)

        if chat_panel is None:
            return
        if self._active_thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
            chat_panel.show_gatekeeper_thread()
            return
        if self._active_thread_id is None:
            return

        active_thread = self._session_manager.get_thread(self._active_thread_id)
        if active_thread is not None:
            chat_panel.show_thread(active_thread)

    def _sync_workspace_screen(self, *, prefer_chat_history: bool = False) -> None:
        planning_mode = self._is_planning_mode() if self._lifecycle is not None else True
        self.set_class(planning_mode, "planning-mode")
        self.set_class(not planning_mode, "vibing-mode")

        if planning_mode:
            if not isinstance(self._workspace_screen, PlanningScreen):
                self._mount_workspace(PlanningScreen())
        else:
            initial_tab = "chat-history" if prefer_chat_history else "task-status"
            if isinstance(self._workspace_screen, VibingScreen):
                if prefer_chat_history:
                    self._workspace_screen.set_active_tab(initial_tab)
            else:
                self._mount_workspace(VibingScreen(initial_tab=initial_tab))

        input_bar = self._query_optional(InputBar)
        if input_bar is None:
            return

        if planning_mode:
            input_bar.set_placeholder("Tell me what you want to build")
        else:
            input_bar.set_placeholder(InputBar.DEFAULT_PLACEHOLDER)

    def _transition_to_vibing(self, *, prefer_chat_history: bool) -> bool:
        if self._lifecycle is None:
            self.notify("Initialize a project before entering the vibing phase.", severity="warning")
            return False

        current_status = _normalize_orchestrator_status(self._lifecycle.engine.state.status)
        if current_status in {OrchestratorStatus.INIT, OrchestratorStatus.PLANNING}:
            try:
                self._transition_workflow_state(OrchestratorStatus.EXECUTING)
            except Exception as exc:
                logger.exception("Failed to enter vibing phase")
                self.notify(f"Failed to enter vibing phase: {exc}", severity="error")
                self._set_status(f"Failed to enter vibing phase: {exc}")
                return False

        self._todo_exit_message = None
        self._sync_workspace_screen(prefer_chat_history=prefer_chat_history)
        self._refresh_project_views()
        self._set_status("Entered vibing phase")
        self._start_automatic_workflow_if_needed()
        return True

    def _resolve_history_dir(self, history_dir: str) -> str:
        return str(resolve_project_path(history_dir, project_root=self._project_root))

    def _refresh_project_views(self) -> None:
        self._sync_workspace_screen()
        plan_tree = self._query_optional(PlanTree)
        agent_output = self._query_optional(AgentOutput)
        consensus_view = self._query_optional(ConsensusView)
        task_status = self._query_optional(TaskStatusView)
        if self._lifecycle is None:
            if plan_tree is not None:
                plan_tree.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
            if agent_output is not None:
                agent_output.clear_agents("No `.vibrant/roadmap.md` found for this workspace.")
            if consensus_view is not None:
                consensus_view.clear_summary("No `.vibrant/consensus.md` found for this workspace.")
            if task_status is not None:
                task_status.set_generating_roadmap(True)
            if isinstance(self._workspace_screen, VibingScreen):
                self._workspace_screen.set_roadmap_loading(True)
            self._refresh_thread_list()
            self._sync_chat_panel_state()
            return

        if agent_output is not None:
            agent_output.sync_agents(self._lifecycle.engine.agents.values())
        consensus_document = getattr(self._lifecycle.engine, "consensus", None)
        consensus_path = getattr(self._lifecycle.engine, "consensus_path", None)

        try:
            roadmap = self._lifecycle.reload_from_disk()
            consensus_document = getattr(self._lifecycle.engine, "consensus", consensus_document)
            consensus_path = getattr(self._lifecycle.engine, "consensus_path", consensus_path)
        except Exception as exc:
            logger.exception("Failed to refresh roadmap view")
            if plan_tree is not None:
                plan_tree.clear_tasks(f"Failed to load roadmap: {exc}")
            if consensus_view is not None:
                consensus_view.update_consensus(
                    consensus_document,
                    source_path=consensus_path,
                )
            self._sync_chat_panel_state()
            return

        if plan_tree is not None:
            plan_tree.update_tasks(roadmap.tasks, agent_summaries=self._collect_task_summaries())
        if consensus_view is not None:
            consensus_view.update_consensus(
                consensus_document,
                tasks=roadmap.tasks,
                source_path=consensus_path,
            )
        roadmap_loading = not bool(roadmap.tasks)
        if task_status is not None:
            task_status.set_generating_roadmap(roadmap_loading)
        if isinstance(self._workspace_screen, VibingScreen):
            self._workspace_screen.set_roadmap_loading(roadmap_loading)
        self._sync_chat_panel_state()
        self._refresh_thread_list()
        self._sync_chat_panel_state()

    def _collect_task_summaries(self) -> dict[str, str]:
        if self._lifecycle is None:
            return {}

        by_task: dict[str, tuple[float, str]] = {}
        for record in self._lifecycle.engine.agents.values():
            if not record.summary:
                continue
            sort_key = 0.0
            if record.started_at is not None:
                sort_key = record.started_at.timestamp()
            elif record.finished_at is not None:
                sort_key = record.finished_at.timestamp()
            previous = by_task.get(record.task_id)
            if previous is None or sort_key >= previous[0]:
                by_task[record.task_id] = (sort_key, record.summary)
        return {task_id: summary for task_id, (_, summary) in by_task.items()}

    def _handle_task_result(self, result: CodeAgentLifecycleResult | None) -> None:
        if result is None:
            if self._lifecycle and self._lifecycle.engine.state.status is OrchestratorStatus.COMPLETED:
                self.notify("Workflow completed.")
                self._set_status("Workflow completed")
            elif self._lifecycle and self._lifecycle.engine.state.pending_questions:
                self.notify(self._lifecycle.engine.USER_INPUT_BANNER, severity="warning")
                self._set_status(self._lifecycle.engine.USER_INPUT_BANNER)
            else:
                self._notify_no_ready_task()
            return

        if result.gatekeeper_result is not None:
            gatekeeper_text = _render_gatekeeper_result_text(result.gatekeeper_result)
            if gatekeeper_text:
                self.query_one(ChatPanel).record_gatekeeper_response(gatekeeper_text)
                self._persist_gatekeeper_thread()

        if result.outcome == "accepted":
            completed = bool(self._lifecycle and self._lifecycle.engine.state.status is OrchestratorStatus.COMPLETED)
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
            self.notify(self._lifecycle.engine.USER_INPUT_BANNER if self._lifecycle else "User input required.", severity="warning")
        else:
            self._set_status(f"Task result: {result.outcome}")

    def _notify_no_ready_task(self) -> None:
        self.notify("No ready roadmap task found.", severity="information")
        self._set_status("No ready roadmap task found")

    def _refresh_thread_list(self) -> None:
        threads = self._conversation_threads()
        available_ids = {thread.id for thread in threads}
        should_focus_gatekeeper = self._should_force_gatekeeper_focus() and ChatPanel.GATEKEEPER_THREAD_ID in available_ids
        should_focus_for_pending = bool(self._pending_gatekeeper_questions())

        if should_focus_gatekeeper and (should_focus_for_pending or not self._gatekeeper_focus_initialized):
            self._active_thread_id = ChatPanel.GATEKEEPER_THREAD_ID
            self._gatekeeper_focus_initialized = True
        elif self._active_thread_id not in available_ids:
            if threads:
                self._active_thread_id = threads[0].id
            else:
                self._active_thread_id = None

    def _show_thread(self, thread: ThreadInfo) -> None:
        self._show_conversation(thread)

    def _show_conversation(self, thread: ThreadInfo) -> None:
        conv = self.query_one(ChatPanel)
        if thread.id == ChatPanel.GATEKEEPER_THREAD_ID:
            conv.show_gatekeeper_thread()
        else:
            conv.show_thread(thread)
        input_bar = self.query_one(InputBar)
        input_bar.set_context(thread.model, thread.status.value)
        input_bar.set_enabled(thread.status != ThreadStatus.RUNNING)
        input_bar.focus_input()
        self._sync_chat_panel_state()

    def _conversation_threads(self) -> list[ThreadInfo]:
        threads = list(self._session_manager.list_threads())
        if self._lifecycle is None or not self.is_mounted:
            return threads

        chat_panel = self._query_optional(ChatPanel)
        if chat_panel is None:
            return threads
        gatekeeper_thread = chat_panel.get_gatekeeper_thread()
        if gatekeeper_thread is None:
            return threads
        return [gatekeeper_thread, *threads]

    def _persist_gatekeeper_thread(self) -> None:
        if not self.is_mounted:
            return
        chat_panel = self._query_optional(ChatPanel)
        if chat_panel is None:
            return
        gatekeeper_thread = chat_panel.get_persisted_gatekeeper_thread()
        if gatekeeper_thread is None or not gatekeeper_thread.turns:
            return
        gatekeeper_thread.cwd = str(self._project_root)
        self._history.save_thread(gatekeeper_thread)

    def _sync_gatekeeper_storage_thread_id(self, thread_id: str | None) -> bool:
        if not self.is_mounted:
            return False
        chat_panel = self._query_optional(ChatPanel)
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

    def _find_conversation_thread(self, thread_id: str) -> ThreadInfo | None:
        if thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
            if not self.is_mounted:
                return None
            chat_panel = self._query_optional(ChatPanel)
            if chat_panel is None:
                return None
            return chat_panel.get_gatekeeper_thread()
        return self._session_manager.get_thread(thread_id)

    def _should_force_gatekeeper_focus(self) -> bool:
        status = None
        if self._lifecycle is not None:
            status = getattr(getattr(self._lifecycle, "engine", None), "state", None)
            status = getattr(status, "status", None)
        normalized_status = _normalize_orchestrator_status(status)
        return normalized_status in {OrchestratorStatus.INIT, OrchestratorStatus.PLANNING} or bool(
            self._pending_gatekeeper_questions()
        )

    def _should_route_input_to_gatekeeper(self) -> bool:
        if self._lifecycle is None:
            return False
        if self._pending_gatekeeper_questions():
            return True
        if self._active_thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
            return True
        status = _normalize_orchestrator_status(self._lifecycle.engine.state.status)
        return status in {OrchestratorStatus.INIT, OrchestratorStatus.PLANNING}

    def _pending_gatekeeper_questions(self) -> list[str]:
        if self._lifecycle is None:
            return []
        engine = getattr(self._lifecycle, "engine", None)
        state = getattr(engine, "state", None)
        questions = getattr(state, "pending_questions", None)
        if not questions:
            return []
        return [question for question in questions if isinstance(question, str) and question]

    def _current_pending_gatekeeper_question(self) -> str | None:
        questions = self._pending_gatekeeper_questions()
        return questions[0] if questions else None

    def _sync_chat_panel_state(self, *, force_flash: bool = False) -> None:
        chat_panel = self._query_optional(ChatPanel)
        input_bar = self._query_optional(InputBar)
        if chat_panel is None or input_bar is None:
            return
        questions = self._pending_gatekeeper_questions()
        gatekeeper_busy = bool(
            self._gatekeeper_request_task is not None and not self._gatekeeper_request_task.done()
        ) or bool(getattr(self._lifecycle, "gatekeeper_busy", False))

        status = None
        if self._lifecycle is not None:
            engine = getattr(self._lifecycle, "engine", None)
            state = getattr(engine, "state", None)
            status = getattr(state, "status", None)

        normalized_status = _normalize_orchestrator_status(status)
        if normalized_status in {OrchestratorStatus.PLANNING, OrchestratorStatus.EXECUTING}:
            self._paused_return_status = normalized_status
        new_questions = [question for question in questions if question not in self._known_pending_questions]
        flash = force_flash or bool(new_questions)
        chat_panel.set_gatekeeper_state(status=normalized_status or status, pending_questions=questions, flash=flash)

        active_conversation: ThreadInfo | None = None
        if questions:
            self._active_thread_id = ChatPanel.GATEKEEPER_THREAD_ID
        elif self._should_force_gatekeeper_focus() and self._active_thread_id in {None, ChatPanel.GATEKEEPER_THREAD_ID}:
            self._active_thread_id = ChatPanel.GATEKEEPER_THREAD_ID

        if self._active_thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
            active_conversation = chat_panel.get_gatekeeper_thread()
            if active_conversation is not None:
                chat_panel.show_gatekeeper_thread()
        elif self._active_thread_id:
            active_conversation = self._session_manager.get_thread(self._active_thread_id)
            if active_conversation is not None:
                chat_panel.show_thread(active_conversation)

        if questions and not gatekeeper_busy:
            banner = getattr(
                getattr(self._lifecycle, "engine", None),
                "USER_INPUT_BANNER",
                "⚠ Gatekeeper needs your input — see Chat panel",
            )
            self._set_banner(banner)
            input_bar.set_enabled(True)
            input_bar.set_context("gatekeeper", "awaiting answer")
            if flash:
                self.notify(banner, severity="warning")
                self._set_status(banner)
                if bool(getattr(getattr(self._lifecycle, "engine", None), "notification_bell_enabled", False)):
                    with suppress(Exception):
                        self.bell()
        elif questions and gatekeeper_busy:
            self._set_banner("Gatekeeper is responding…")
            input_bar.set_enabled(False)
            input_bar.set_context("gatekeeper", "running…")
        else:
            self._set_banner(None)
            if self._active_thread_id == ChatPanel.GATEKEEPER_THREAD_ID and gatekeeper_busy:
                input_bar.set_enabled(False)
                input_bar.set_context("gatekeeper", "running…")
            elif self._active_thread_id == ChatPanel.GATEKEEPER_THREAD_ID:
                input_bar.set_enabled(True)
                input_bar.set_context("gatekeeper", "conversation")
            elif normalized_status is OrchestratorStatus.INIT:
                input_bar.set_enabled(True)
                input_bar.set_context("gatekeeper", "describe your goal")
            elif normalized_status is OrchestratorStatus.PLANNING:
                input_bar.set_enabled(True)
                input_bar.set_context("gatekeeper", "planning")
            elif active_conversation is not None:
                input_bar.set_context(active_conversation.model, active_conversation.status.value)
                input_bar.set_enabled(active_conversation.status != ThreadStatus.RUNNING)
            elif normalized_status is OrchestratorStatus.PAUSED:
                input_bar.set_enabled(True)
                input_bar.set_context("workflow", "paused")
            else:
                input_bar.set_enabled(True)
                input_bar.set_context(None, "")

        self._known_pending_questions = tuple(questions)

    def _infer_resume_status(self) -> OrchestratorStatus:
        if self._lifecycle is None:
            return OrchestratorStatus.EXECUTING

        consensus = getattr(self._lifecycle.engine, "consensus", None)
        if consensus is not None:
            mapped = {
                ConsensusStatus.PLANNING: OrchestratorStatus.PLANNING,
                ConsensusStatus.EXECUTING: OrchestratorStatus.EXECUTING,
            }.get(consensus.status)
            if mapped is not None:
                return mapped

        roadmap_document = getattr(self._lifecycle, "roadmap_document", None)
        if roadmap_document is None:
            roadmap_document = self._lifecycle.reload_from_disk()
        return OrchestratorStatus.EXECUTING if getattr(roadmap_document, "tasks", None) else OrchestratorStatus.PLANNING

    def _transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        if self._lifecycle is None:
            raise RuntimeError("Project lifecycle is not initialized")

        engine = self._lifecycle.engine
        if not engine.can_transition_to(next_status):
            current = engine.state.status.value
            raise ValueError(f"Invalid orchestrator state transition: {current} -> {next_status.value}")

        consensus_document = getattr(engine, "consensus", None)
        consensus_path = Path(getattr(engine, "consensus_path", self._project_root / DEFAULT_CONFIG_DIR / "consensus.md"))
        target_consensus_status = _WORKFLOW_TO_CONSENSUS.get(next_status)

        if target_consensus_status is not None and consensus_path.exists():
            document = consensus_document
            if document is None:
                document = ConsensusParser().parse_file(consensus_path)
            updated_document = document.model_copy(deep=True)
            updated_document.status = target_consensus_status
            engine.consensus = ConsensusWriter().write(consensus_path, updated_document)

        engine.transition_to(next_status)
        engine.refresh_from_disk()

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
        try:
            self.query_one("#status-bar", Static).update(text)
        except Exception:
            return

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
        if self._lifecycle is None:
            return False

        status = _normalize_orchestrator_status(self._lifecycle.engine.state.status)
        return status in {OrchestratorStatus.INIT, OrchestratorStatus.PLANNING}

    def _apply_view_mode(self) -> None:
        self._sync_workspace_screen()

    def _maybe_handle_planning_completion_request(self, result: object) -> bool:
        completion_request = _extract_planning_completion_request(result)
        if completion_request is None:
            return False

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
