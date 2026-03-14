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

from vibrant.models.task import TaskInfo
from vibrant.orchestrator.types import QuestionRecord, QuestionStatus, RuntimeExecutionResult
from vibrant.providers.base import CanonicalEvent

from ..agents import PLANNING_COMPLETE_MCP_TOOL
from ..config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, find_project_root
from ..models import AppSettings, ConsensusStatus, OrchestratorStatus
from ..models.consensus import DEFAULT_CONSENSUS_CONTEXT
from ..orchestrator import TaskResult, Orchestrator, OrchestratorFacade, create_orchestrator
from ..project_init import ensure_project_files, initialize_project
from .screens import HelpScreen, InitializationScreen, PlanningScreen, VibingScreen
from .widgets.chat_panel import ChatPanel
from .widgets.consensus_view import ConsensusView
from .widgets.input_bar import InputBar
from .widgets.settings_panel import SettingsPanel

logger = logging.getLogger(__name__)
OrchestratorFactory = Callable[..., Orchestrator]
WorkspaceScreen = PlanningScreen | VibingScreen
_STREAM_ONLY_EVENT_TYPES = {
    "assistant.message.delta",
    "assistant.thinking.delta",
    "content.delta",
    "reasoning.summary.delta",
    "tool.call.delta",
}

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
        Binding("f5", "show_task_status", "Task", show=True),
        Binding("f6", "show_chat_history", "Chat", show=True),
        Binding("f7", "toggle_consensus", "Consensus", show=True),
        Binding("f8", "show_agent_logs", "Logs", show=True),
        Binding("escape", "interrupt_gatekeeper", show=False),
        Binding("f10", "quit_app", "Quit", show=True),
        Binding("ctrl+s", "open_settings", "Settings", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=True),
    ]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        current_screen = self.screen
        if isinstance(current_screen, (HelpScreen, InitializationScreen)):
            return False

        planning_screen = self._planning_screen()
        vibing_screen = self._vibing_screen()

        if action in {"open_help", "quit_app", "open_settings"}:
            return planning_screen is not None or vibing_screen is not None
        if action == "toggle_pause":
            return vibing_screen is not None
        if action == "interrupt_gatekeeper":
            return (
                (planning_screen is not None or vibing_screen is not None)
                and self._gatekeeper_is_busy()
            )
        if action in {"show_task_status", "show_chat_history", "show_agent_logs"}:
            return vibing_screen is not None
        if action == "toggle_consensus":
            return planning_screen is not None or vibing_screen is not None

        return super().check_action(action, parameters)

    def __init__(
        self,
        settings: AppSettings | None = None,
        cwd: str | None = None,
        *,
        orchestrator_factory: OrchestratorFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or AppSettings()
        if cwd:
            self._settings.default_cwd = cwd

        self.orchestrator: Orchestrator | None = None
        self.orchestrator_facade: OrchestratorFacade | None = None
        self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._orchestrator_factory = orchestrator_factory or create_orchestrator
        self._runtime_event_subscription = None
        self._gatekeeper_conversation_subscription = None
        self._gatekeeper_conversation_id: str | None = None
        self._pending_runtime_bootstrap_events: list[dict[str, Any]] = []
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
        self._initialize_project_setup()
        self._sync_workspace_screen()
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
        self._close_orchestrator_subscriptions()

    async def action_open_settings(self) -> None:
        result = await self.push_screen_wait(SettingsPanel(self._settings))
        if not result:
            return

        self._settings = result
        self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._initialize_project_setup()
        self._sync_workspace_screen()
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
        self._initialize_project_setup()
        self._sync_workspace_screen()
        self.call_after_refresh(self._refresh_project_views)
        self._set_status(f"Initialized Vibrant project in {project_root}")
        self.notify(f"Initialized Vibrant project in {project_root}")
        self.call_after_refresh(self._focus_primary_input)
        return True

    def action_open_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_toggle_pause(self) -> None:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return

        current_status = orchestrator.get_workflow_status()
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

    def action_show_task_status(self) -> None:
        vibing_screen = self._vibing_screen()
        if vibing_screen is None:
            self.notify("Task status is only available in the vibing screen.", severity="warning")
            return
        vibing_screen.show_task_status()

    def action_toggle_consensus(self) -> None:
        planning_screen = self._planning_screen()
        if planning_screen is not None:
            planning_screen.toggle_consensus_panel()
            state = "shown" if planning_screen.consensus_visible else "hidden"
            self._set_status(f"Consensus panel {state}")
            return

        vibing_screen = self._vibing_screen()
        if vibing_screen is None:
            self.notify("Consensus view is not available on this screen.", severity="warning")
            return
        vibing_screen.show_consensus()
        self._set_status("Opened Consensus tab")

    def action_show_chat_history(self) -> None:
        vibing_screen = self._vibing_screen()
        if vibing_screen is None:
            self.notify("Chat history is only available in the vibing screen.", severity="warning")
            return
        vibing_screen.show_chat_history()
        self._set_status("Opened Gatekeeper chat history")

    def action_show_agent_logs(self) -> None:
        vibing_screen = self._vibing_screen()
        if vibing_screen is None:
            self.notify("Agent logs are only available in the vibing screen.", severity="warning")
            return
        vibing_screen.show_agent_logs()
        self._set_status("Opened Agent Logs tab")

    async def action_run_next_task(self) -> None:
        if self.orchestrator is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return
        if self._task_execution_in_progress:
            self.notify("A roadmap task is already running.", severity="warning")
            return

        self._launch_roadmap_runner(notify_when_idle=True)

    async def action_interrupt_gatekeeper(self) -> None:
        if self.orchestrator is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return
            
        await self.orchestrator.gatekeeper_lifecycle.interrupt_active_turn()

    async def _run_roadmap_tasks(self, *, notify_when_idle: bool) -> None:
        assert self.orchestrator is not None

        automatic = self._roadmap_execution_mode() is RoadmapExecutionMode.AUTOMATIC

        self._task_execution_in_progress = True
        self._set_status("Running roadmap workflow…" if automatic else "Running next roadmap task…")
        self._start_project_refresh_loop()
        self._refresh_project_views()

        try:
            if automatic:
                results = await self.orchestrator.run_until_blocked()
                if results:
                    self._handle_task_results(results)
                elif notify_when_idle:
                    self._handle_task_result(None)
            else:
                result = await self.orchestrator.run_next_task()
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
        orchestrator = self.orchestrator_facade
        if orchestrator is None or self._task_execution_in_progress or self._is_planning_mode():
            return
        if self._roadmap_execution_mode() is not RoadmapExecutionMode.AUTOMATIC:
            return

        snapshot = orchestrator.snapshot()
        if snapshot.pending_questions or snapshot.status in {
            OrchestratorStatus.PAUSED,
            OrchestratorStatus.COMPLETED,
            OrchestratorStatus.FAILED,
        }:
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
        assert self.orchestrator is not None
        pending_question = self._current_pending_gatekeeper_question_record()
        try:
            if pending_question is not None:
                submission = await self.orchestrator.answer_user_decision(
                    pending_question.question_id,
                    text,
                )
            else:
                submission = await self.orchestrator.submit_user_message(text)
            self._sync_gatekeeper_conversation_binding(
                conversation_id=submission.conversation_id,
                force=True,
            )
            self._refresh_gatekeeper_state()

            result = await self.orchestrator.control_plane.wait_for_gatekeeper_submission(submission)
            self._refresh_project_views()
            if _extract_planning_completion_request(result):
                self._transition_to_vibing(prefer_chat_history=True)
                return
            if self._maybe_sync_post_planning_transition():
                return
            self.notify("Message sent to Gatekeeper.")
            self._set_status("Gatekeeper updated the plan")
            self._start_automatic_workflow_if_needed()
        except Exception as exc:
            self.notify(f"Error: {exc}", severity="error")
            self._set_status(f"Gatekeeper request failed: {exc}")
        finally:
            self._gatekeeper_request_task = None
            self._refresh_gatekeeper_state()

    def _roadmap_execution_mode(self) -> RoadmapExecutionMode:
        if self.orchestrator is None:
            return RoadmapExecutionMode.AUTOMATIC
        mode = self.orchestrator.execution_mode
        if isinstance(mode, RoadmapExecutionMode):
            return mode
        return RoadmapExecutionMode(str(mode).strip().lower())

    def _handle_task_results(self, results: list[TaskResult]) -> None:
        for result in results:
            self._handle_task_result(result)

    async def action_quit_app(self) -> None:
        self.exit()

    async def on_input_bar_message_submitted(self, event: InputBar.MessageSubmitted) -> None:
        if self.orchestrator is None:
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

        self._record_command_history_entry(event.text)
        input_bar.set_enabled(False)
        input_bar.set_context("gatekeeper", "sending…")
        self._set_status("Sending message to Gatekeeper…")
        self._launch_gatekeeper_message(event.text)
        self._refresh_gatekeeper_state()

    async def on_input_bar_slash_command(self, event: InputBar.SlashCommand) -> None:
        self._record_command_history_entry(event.text)
        cmd = event.command.lower()
        if cmd == "model":
            if event.args:
                self._settings.default_model = event.args
                self._set_status(f"Model set to {event.args}")
            else:
                self.notify(f"Current model: {self._settings.default_model}")
        elif cmd == "settings":
            await self.action_open_settings()
        elif cmd == "vibe":
            self._transition_to_vibing(prefer_chat_history=True)
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
                "/vibe - Enter vibing phase\n"
                "/run - Execute the next roadmap task\n"
                "/refresh - Reload project state\n"
                "/settings - Open settings\n"
                "/history - Open Gatekeeper chat history\n"
                "/logs - Open Agent Logs\n"
                "/help - Show this help"
            )
        else:
            self.notify(f"Unknown command: /{cmd}", severity="warning")

    async def on_consensus_view_save_requested(self, event: ConsensusView.SaveRequested) -> None:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            self.notify("Consensus edits require an initialized project.", severity="warning")
            return

        if not event.already_saved:
            try:
                orchestrator.write_consensus_document(event.document)
            except Exception as exc:
                logger.exception("Failed to save consensus edits")
                self.notify(f"Failed to save consensus edits: {exc}", severity="error")
                self._set_status(f"Consensus save failed: {exc}")
                return

        self._refresh_project_views()
        self.notify("Consensus updated.")
        self._set_status("Saved consensus edits")

    async def on_initialization_screen_initialize_requested(
        self,
        event: InitializationScreen.InitializeRequested,
    ) -> None:
        if await self.initialize_project_at(event.target_path) and isinstance(self.screen, InitializationScreen):
            self.screen.dismiss(None)

    def on_initialization_screen_exit_requested(self, _: InitializationScreen.ExitRequested) -> None:
        self.exit()

    def _on_runtime_event(self, event: CanonicalEvent) -> None:
        self.call_after_refresh(self._handle_runtime_event, dict(event))

    def _handle_runtime_event(self, event: CanonicalEvent) -> None:
        vibing_screen = self._vibing_screen()
        if vibing_screen is not None:
            with suppress(Exception):
                vibing_screen.agent_output.ingest_canonical_event(event)

        event_type = str(event.get("type") or "")
        if _is_gatekeeper_event(event):
            self._sync_gatekeeper_conversation_binding()
            if event_type in {"content.delta", "assistant.message.delta", "assistant.thinking.delta"}:
                self._set_status("Gatekeeper is responding…")

        if event_type == "turn.started":
            self._set_status(f"Running {event.get('task_id', 'task')}…")
        elif event_type == "turn.completed":
            self._set_status(f"Completed {event.get('task_id', 'task')}")
        elif event_type == "runtime.error":
            self._set_status(_error_text_from_event(event) or "Task failed")
        elif event_type in {"user-input.requested", "request.opened"}:
            self._refresh_gatekeeper_state(force_flash=True)

        if event_type not in _STREAM_ONLY_EVENT_TYPES:
            self._refresh_project_views()

    def _on_gatekeeper_conversation_event(self, event) -> None:
        self.call_after_refresh(self._apply_gatekeeper_conversation_event, event)

    def _apply_gatekeeper_conversation_event(self, event) -> None:
        chat_panel = self._chat_panel()
        if chat_panel is None:
            return

        if chat_panel.current_conversation_id not in {None, event.conversation_id}:
            self._sync_gatekeeper_conversation_binding(
                conversation_id=event.conversation_id,
                force=True,
            )
            chat_panel = self._chat_panel()
            if chat_panel is None:
                return

        chat_panel.ingest_stream_event(event)
        if event.type == "conversation.request.opened":
            self._refresh_gatekeeper_state(force_flash=True)
        elif event.type == "conversation.runtime.error":
            self._set_status(event.text or "Gatekeeper request failed")
        elif event.type in {"conversation.assistant.message.delta", "conversation.assistant.thinking.delta"}:
            self._set_status("Gatekeeper is responding…")

    def _close_orchestrator_subscriptions(self) -> None:
        for subscription in (self._runtime_event_subscription, self._gatekeeper_conversation_subscription):
            if subscription is None:
                continue
            with suppress(Exception):
                subscription.close()
        self._runtime_event_subscription = None
        self._gatekeeper_conversation_subscription = None
        self._gatekeeper_conversation_id = None
        self._pending_runtime_bootstrap_events = []

    def _attach_orchestrator_subscriptions(self) -> None:
        self._close_orchestrator_subscriptions()
        if self.orchestrator is None:
            return
        self._pending_runtime_bootstrap_events = self.orchestrator.control_plane.list_recent_events(limit=200)
        self._runtime_event_subscription = self.orchestrator.control_plane.subscribe_runtime_events(self._on_runtime_event)

    def _initialize_project_setup(self) -> None:
        self._close_orchestrator_subscriptions()
        project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._project_root = project_root
        vibrant_dir = project_root / DEFAULT_CONFIG_DIR
        if not vibrant_dir.exists():
            self.orchestrator = None
            self.orchestrator_facade = None
            return

        try:
            ensure_project_files(project_root)
            self.orchestrator = self._orchestrator_factory(project_root)
            self.orchestrator_facade = OrchestratorFacade(self.orchestrator)
            self._attach_orchestrator_subscriptions()
        except Exception as exc:
            logger.exception("Failed to initialize project lifecycle")
            self.orchestrator = None
            self.orchestrator_facade = None
            self.notify(f"Failed to load project state: {exc}", severity="error")
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
        host.remove_children()
        host.mount(workspace)
        self._workspace_screen = workspace
    def _apply_workspace_placeholder(self, placeholder: str) -> None:
        input_bar = self._input_bar()
        if input_bar is None:
            return
        with suppress(Exception):
            input_bar.set_placeholder(placeholder)
        with suppress(Exception):
            input_bar.set_completion_base_path(self._project_root)
        with suppress(Exception):
            input_bar.set_history_provider(self._command_history_entries)

    def _sync_workspace_screen(self, *, prefer_chat_history: bool = False) -> None:
        planning_mode = self.orchestrator is None or self._is_planning_mode()
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

        placeholder = self._default_input_placeholder()
        self.call_after_refresh(self._apply_workspace_placeholder, placeholder)
        self.refresh_bindings()

    def _transition_to_vibing(self, *, prefer_chat_history: bool) -> bool:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            self.notify("Initialize a project before entering the vibing phase.", severity="warning")
            return False

        try:
            for _ in range(2):
                current_status = _normalize_orchestrator_status(orchestrator.get_workflow_status())
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

    def _refresh_project_views(self) -> None:
        self._sync_workspace_screen()
        vibing_screen = self._vibing_screen()
        self._sync_gatekeeper_conversation_binding()
        orchestrator = self.orchestrator_facade
        agent_output = None
        plan_tree = None
        consensus_view = None
        if vibing_screen is not None:
            with suppress(Exception):
                agent_output = vibing_screen.agent_output
            with suppress(Exception):
                plan_tree = vibing_screen.plan_tree
            with suppress(Exception):
                consensus_view = vibing_screen.consensus_view
        if orchestrator is None:
            chat_panel = self._chat_panel()
            if chat_panel is not None:
                chat_panel.clear_conversation()
            if vibing_screen is not None:
                if plan_tree is not None:
                    plan_tree.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
                if agent_output is not None:
                    agent_output.clear_agents("No `.vibrant/roadmap.md` found for this workspace.")
                if consensus_view is not None:
                    self._clear_consensus_view(
                        consensus_view
                    )
                with suppress(Exception):
                    vibing_screen.set_roadmap_loading(True)
            self._refresh_gatekeeper_state()
            return

        snapshot = orchestrator.snapshot()
        if agent_output is not None:
            agents = snapshot.agent_records
            agent_output.sync_agents(agents)
            for event in self._pending_runtime_bootstrap_events:
                agent_output.ingest_canonical_event(event)
            self._pending_runtime_bootstrap_events = []

        roadmap = snapshot.roadmap
        roadmap_tasks = roadmap.tasks if roadmap is not None else []
        consensus_document = snapshot.consensus
        consensus_path = snapshot.consensus_path

        if vibing_screen is not None:
            sync_task_views = getattr(vibing_screen, "sync_task_views", None)
            if callable(sync_task_views):
                sync_task_views(
                    roadmap_tasks,
                    facade=orchestrator,
                    agent_summaries=self._collect_task_summaries(),
                )
            elif plan_tree is not None:
                plan_tree.update_tasks(
                    roadmap_tasks,
                    agent_summaries=self._collect_task_summaries(),
                )
            with suppress(Exception):
                vibing_screen.set_roadmap_loading(not bool(roadmap_tasks))

        if consensus_view is not None:
            self._update_consensus_view(
                consensus_view,
                consensus_document,
                tasks=roadmap_tasks,
                source_path=consensus_path,
            )

        planning_screen = self._planning_screen()
        if planning_screen is not None and self._should_auto_reveal_consensus(consensus_document):
            planning_screen.reveal_consensus_once()

        self._refresh_gatekeeper_state()

    def _collect_task_summaries(self) -> dict[str, str]:
        if self.orchestrator_facade is None:
            return {}
        return self.orchestrator_facade.get_task_summaries()

    def _update_consensus_view(
        self,
        consensus_view: ConsensusView,
        document,
        *,
        tasks: list[TaskInfo],
        source_path,
    ) -> None:
        try:
            consensus_view.update_consensus(
                document,
                tasks=tasks,
                source_path=source_path,
            )
        except TypeError:
            consensus_view.update_consensus(
                document,
                tasks=tasks,
            )

    def _clear_consensus_view(self, consensus_view: ConsensusView) -> None:
        consensus_view.clear_summary()

    def _command_history_entries(self) -> list[str]:
        # TODO: Implement a persistent command history
        return []

    def _record_command_history_entry(self, text: str) -> None:
        # TODO: Implement a persistent command history
        pass

    def _handle_task_result(self, result: TaskResult | None) -> None:
        orchestrator = self.orchestrator_facade
        if result is None:
            if orchestrator and orchestrator.get_workflow_status() is OrchestratorStatus.COMPLETED:
                self.notify("Workflow completed.")
                self._set_status("Workflow completed")
            elif self._pending_question_records():
                assert orchestrator is not None
                banner = orchestrator.get_user_input_banner()
                self.notify(banner, severity="warning")
                self._set_status(banner)
            else:
                self._notify_no_ready_task()
            return

        task_label = result.task_id or "task"
        if result.outcome == "accepted":
            completed = bool(orchestrator and orchestrator.get_workflow_status() is OrchestratorStatus.COMPLETED)
            if completed:
                self.notify(f"Task {task_label} accepted and merged. Workflow completed.")
                self._set_status(f"Task {task_label} accepted · workflow completed")
            else:
                self.notify(f"Task {task_label} accepted and merged.")
                self._set_status(f"Task {task_label} accepted and merged")
        elif result.outcome == "retried":
            self.notify(f"Task {task_label} queued for retry.", severity="warning")
            self._set_status(f"Task {task_label} queued for retry")
        elif result.outcome == "escalated":
            self.notify(f"Task {task_label} escalated to the user.", severity="warning")
            self._set_status(f"Task {task_label} escalated to the user")
        elif result.outcome == "review_pending":
            worktree_path = result.worktree_path
            if isinstance(worktree_path, str) and worktree_path.strip():
                self.notify(f"Task {task_label} is awaiting review in {worktree_path}.")
            else:
                self.notify(f"Task {task_label} is awaiting review.")
            self._set_status(f"Task {task_label} awaiting review")
        elif result.outcome == "awaiting_user":
            self.notify(
                orchestrator.get_user_input_banner() if orchestrator else "User input required.",
                severity="warning",
            )
        elif result.outcome == "failed":
            error = result.error or "Task failed."
            self.notify(str(error), severity="error")
            self._set_status(f"Task {task_label} failed")
        else:
            self._set_status(f"Task result: {result.outcome}")

    def _notify_no_ready_task(self) -> None:
        self.notify("No ready roadmap task found.", severity="information")
        self._set_status("No ready roadmap task found")

    def _sync_gatekeeper_conversation_binding(
        self,
        *,
        conversation_id: str | None = None,
        force: bool = False,
    ) -> None:
        chat_panel = self._chat_panel()
        if self.orchestrator is None or chat_panel is None:
            return

        resolved_conversation_id = conversation_id
        if resolved_conversation_id is None:
            resolved_conversation_id = self.orchestrator.control_plane.gatekeeper_conversation_id()

        if not resolved_conversation_id:
            if force or self._gatekeeper_conversation_id is not None:
                if self._gatekeeper_conversation_subscription is not None:
                    with suppress(Exception):
                        self._gatekeeper_conversation_subscription.close()
                self._gatekeeper_conversation_subscription = None
                self._gatekeeper_conversation_id = None
                chat_panel.clear_conversation()
            return

        needs_rebind = force or resolved_conversation_id != self._gatekeeper_conversation_id
        if not needs_rebind:
            if chat_panel.current_conversation_id != resolved_conversation_id:
                chat_panel.bind_conversation(
                    self.orchestrator.control_plane.conversation(resolved_conversation_id)
                )
            return

        if self._gatekeeper_conversation_subscription is not None:
            with suppress(Exception):
                self._gatekeeper_conversation_subscription.close()

        self._gatekeeper_conversation_id = resolved_conversation_id
        chat_panel.bind_conversation(
            self.orchestrator.control_plane.conversation(resolved_conversation_id)
        )
        self._gatekeeper_conversation_subscription = self.orchestrator.control_plane.subscribe_conversation(
            resolved_conversation_id,
            self._on_gatekeeper_conversation_event,
            replay=False,
        )

    def _current_pending_gatekeeper_question_record(self):
        pending = self._pending_question_records()
        return pending[0] if pending else None

    def _list_question_records(self) -> list[QuestionRecord]:
        facade = self.orchestrator_facade
        if facade is None:
            return []

        list_records = facade.list_question_records()
        return list_records

    def _pending_question_records(self) -> list[QuestionRecord]:
        facade = self.orchestrator_facade
        if facade is None:
            return []

        list_pending: list[QuestionRecord] = facade.list_pending_question_records()
        return list_pending

    def _notification_bell_enabled(self) -> bool:
        # TODO: Orchestrator have no related setting yet.
        return False

    def _gatekeeper_is_busy(self) -> bool:
        if self.orchestrator is None:
            return False
        return self.orchestrator.gatekeeper_busy

    def _refresh_gatekeeper_state(self, *, force_flash: bool = False) -> None:
        chat_panel = self._chat_panel()
        input_bar = self._input_bar()
        if chat_panel is None or input_bar is None:
            return

        question_records = self._list_question_records()
        questions = [record.text for record in question_records if record.status == QuestionStatus.PENDING]
        status = self.orchestrator_facade.get_workflow_status() if self.orchestrator_facade is not None else None

        normalized_status = _normalize_orchestrator_status(status)
        if normalized_status in {OrchestratorStatus.PLANNING, OrchestratorStatus.EXECUTING}:
            self._paused_return_status = normalized_status

        new_questions = [question for question in questions if question not in self._known_pending_questions]
        flash = force_flash or bool(new_questions)
        with suppress(Exception):
            chat_panel.set_gatekeeper_state(
                status=normalized_status or status,
                question_records=question_records,
                flash=flash,
            )

        if questions and not self._gatekeeper_is_busy():
            banner = (
                self.orchestrator_facade.get_user_input_banner()
                if self.orchestrator_facade is not None
                else "⚠ Gatekeeper needs your input — see Chat panel"
            )
            self._set_banner(banner)
            input_bar.set_enabled(True)
            input_bar.set_context("gatekeeper", "awaiting answer")
            input_bar.set_placeholder(self._default_input_placeholder())
            if flash:
                self.notify(banner, severity="warning")
                self._set_status(banner)
                if self._notification_bell_enabled():
                    with suppress(Exception):
                        self.bell()
        elif self._gatekeeper_is_busy():
            self._set_banner("Gatekeeper is responding…")
            input_bar.set_enabled(False)
            input_bar.set_context("gatekeeper", "running… · Esc to interrupt")
            input_bar.set_placeholder("Gatekeeper is responding… Press Esc to interrupt.")
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
            input_bar.set_placeholder(self._default_input_placeholder())

        self._known_pending_questions = tuple(questions)

    def _default_input_placeholder(self) -> str:
        return (
            "Tell me what you want to build"
            if self.orchestrator is None or self._is_planning_mode()
            else InputBar.DEFAULT_PLACEHOLDER
        )

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

    def _planning_screen(self) -> PlanningScreen | None:
        if isinstance(self._workspace_screen, PlanningScreen):
            return self._workspace_screen
        return None

    @staticmethod
    def _should_auto_reveal_consensus(document) -> bool:
        if document is None:
            return False
        normalized = document.context.strip()
        return bool(normalized and normalized != DEFAULT_CONSENSUS_CONTEXT.strip())

    def _vibing_screen(self) -> VibingScreen | None:
        if isinstance(self._workspace_screen, VibingScreen):
            return self._workspace_screen
        return None

    def _infer_resume_status(self) -> OrchestratorStatus:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            return OrchestratorStatus.EXECUTING
        return orchestrator.infer_resume_status()

    def _transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            raise RuntimeError("Project lifecycle is not initialized")

        current_status = _normalize_orchestrator_status(orchestrator.get_workflow_status())
        if current_status is next_status:
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
        if self.orchestrator_facade is None:
            return False
        status = _normalize_orchestrator_status(self.orchestrator_facade.get_workflow_status())
        return status in {OrchestratorStatus.INIT, OrchestratorStatus.PLANNING}

    def _maybe_sync_post_planning_transition(self) -> bool:
        if self._planning_screen() is None or self.orchestrator_facade is None:
            return False

        status = _normalize_orchestrator_status(self.orchestrator_facade.get_workflow_status())
        if status in {None, OrchestratorStatus.INIT, OrchestratorStatus.PLANNING}:
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
    if isinstance(agent_id, str) and (agent_id == "gatekeeper" or agent_id.startswith("gatekeeper-")):
        return True

    task_id = event.get("task_id")
    return isinstance(task_id, str) and task_id.startswith("gatekeeper-")


def _error_text_from_event(event: dict[str, Any]) -> str:
    error_message = event.get("error_message")
    if isinstance(error_message, str) and error_message.strip():
        return error_message.strip()
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "").strip()


def _extract_planning_completion_request(result: RuntimeExecutionResult) -> str | None:
    events = result.events
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
