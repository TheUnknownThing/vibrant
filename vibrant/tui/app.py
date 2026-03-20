"""Main Textual application for Vibrant."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from operator import ne
import os
from pathlib import Path
from collections.abc import Callable

from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Static

from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.types import AgentStreamEvent, AttemptStatus, ConversationSummary, QuestionStatus, QuestionView, WorkflowStatus
from vibrant.providers.base import CanonicalEvent

from ..agents import PLANNING_COMPLETE_MCP_TOOL
from ..config import (
    DEFAULT_CONFIG_DIR,
    RoadmapExecutionMode,
    VibrantConfig,
    VibrantConfigPatch,
    find_project_root,
    load_config,
)
from ..models import AppSettings
from ..models.consensus import DEFAULT_CONSENSUS_CONTEXT, ConsensusDocument
from ..orchestrator import TaskResult, Orchestrator, OrchestratorFacade, OrchestratorSnapshot, create_orchestrator
from ..orchestrator.interface.control_plane import InterfaceControlPlane
from ..project_init import ensure_project_files, initialize_project
from .screens import HelpScreen, InitializationScreen, PlanningScreen, VibingScreen
from .widgets.chat_panel import ChatPanel
from .widgets.consensus_view import ConsensusView
from .widgets.input_bar import InputBar
from .widgets.settings_panel import SettingsPanel, SettingsUpdate

logger = logging.getLogger(__name__)
OrchestratorFactory = Callable[..., Orchestrator]
WorkspaceScreen = PlanningScreen | VibingScreen
_MOBILE_BREAKPOINT = 80

_STREAM_ONLY_EVENT_TYPES = {
    "assistant.message.delta",
    "assistant.thinking.delta",
    "content.delta",
    "reasoning.summary.delta",
    "tool.call.delta",
}


def _should_autofocus_primary_input(*, is_web: bool, width: int) -> bool:
    """Avoid opening the mobile browser keyboard during initial app bootstrap."""

    return not is_web or width >= _MOBILE_BREAKPOINT


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

    VibrantApp.-mobile Header,
    VibrantApp.-mobile Footer {
        display: none;
    }

    VibrantApp.-mobile #status-bar {
        height: auto;
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

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        """Send notifications without letting arbitrary text break Textual markup parsing."""

        safe_message = escape(message) if markup else message
        safe_title = escape(title) if markup else title
        super().notify(
            safe_message,
            title=safe_title,
            severity=severity,
            timeout=timeout,
            markup=markup,
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        current_screen = self.screen
        if isinstance(current_screen, (HelpScreen, InitializationScreen)):
            return False

        planning_screen = self._planning_screen()
        vibing_screen = self.vibing_screen()

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
            if action == "show_agent_logs":
                return vibing_screen is not None and self._agent_logs_tab_available()
            return vibing_screen is not None
        if action == "toggle_consensus":
            return planning_screen is not None or vibing_screen is not None

        return super().check_action(action, parameters)

    def __init__(
        self,
        settings: AppSettings | None = None,
        cwd: str | None = None,
        *,
        dev_mode: bool = False,
        orchestrator_factory: OrchestratorFactory | None = None,
        **app_kwargs: object,
    ) -> None:
        super().__init__(**app_kwargs)
        self._settings = settings or AppSettings()
        if cwd:
            self._settings.default_cwd = cwd

        self.sub_title = _display_path(self._active_directory())
        self._dev_mode = dev_mode
        self.orchestrator: Orchestrator | None = None
        self.orchestrator_facade: OrchestratorFacade | None = None
        self._project_config: VibrantConfig | None = None
        self._project_root = find_project_root(Path(self._settings.default_cwd or os.getcwd()))
        self._orchestrator_factory = orchestrator_factory or create_orchestrator
        self._runtime_event_subscription = None
        self._gatekeeper_conversation_subscription = None
        self._agent_output_conversation_subscriptions: dict[str, StreamSubscription] = {}
        self._agent_output_loaded_conversation_ids: set[str] = set()
        self._gatekeeper_conversation_id: str | None = None
        self._workspace_screen: WorkspaceScreen | None = None
        self._task_execution_in_progress = False
        self._task_refresh_loop: asyncio.Task[None] | None = None
        self._roadmap_runner_task: asyncio.Task[None] | None = None
        self._gatekeeper_request_task: asyncio.Task[None] | None = None
        self._known_pending_questions: tuple[str, ...] = ()
        self._gatekeeper_state_initialized = False
        self._paused_return_status: WorkflowStatus | None = None
        self._banner_text: str | None = None
        self._todo_exit_message: str | None = None
        self._mobile_chrome_enabled: bool | None = None

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
        self._apply_mobile_chrome()

        if not self._project_has_vibrant_state():
            self._set_status("Project not initialized")
            self.push_screen(InitializationScreen(self._project_root))
            return

        self.call_after_refresh(self._focus_primary_input)


    def on_resize(self, event: events.Resize) -> None:
        del event
        self._apply_mobile_chrome()

    def _apply_mobile_chrome(self) -> None:
        is_mobile = self.size.width < _MOBILE_BREAKPOINT
        if self._mobile_chrome_enabled == is_mobile:
            return
        self._mobile_chrome_enabled = is_mobile
        self.set_class(is_mobile, "-mobile")

    async def on_unmount(self) -> None:
        for task in (self._gatekeeper_request_task, self._roadmap_runner_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        await self._stop_project_refresh_loop()
        self._close_orchestrator_subscriptions()

    def action_open_settings(self) -> None:
        if self.orchestrator_facade is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return
        self.push_screen(
            SettingsPanel(
                self.orchestrator_facade.get_config(),
                working_directory=self._settings.default_cwd,
            ),
            self._handle_settings_dismissed,
        )

    def _handle_settings_dismissed(self, result: SettingsUpdate | None) -> None:
        if result is None:
            return

        if result.working_directory != self._settings.default_cwd:
            self._settings.default_cwd = result.working_directory
            if not self._reload_active_project():
                return

        if result.config_patch.has_changes():
            orchestrator = self.orchestrator_facade
            if orchestrator is None:
                self.notify(
                    f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                    severity="warning",
                )
                return
            try:
                orchestrator.update_config(result.config_patch)
            except Exception as exc:
                logger.exception("Failed to update orchestrator config")
                self.notify(f"Failed to update settings: {exc}", severity="error")
                self._set_status(f"Settings update failed: {exc}")
                return
            if not self._reload_active_project():
                return

        self._set_status("Settings updated")

    async def initialize_project_at(self, target_path: Path) -> bool:
        try:
            vibrant_dir = initialize_project(target_path)
        except Exception as exc:
            logger.exception("Failed to initialize Vibrant project")
            self.notify(f"Failed to initialize project: {exc}", severity="error")
            self._set_status(f"Initialization failed: {exc}")
            return False

        project_root = vibrant_dir.parent
        self._settings.default_cwd = str(project_root)
        self._reload_active_project()
        self._set_status(f"Initialized Vibrant project in {project_root}")
        self.notify(f"Initialized Vibrant project in {project_root}")
        self.call_after_refresh(self._focus_primary_input)
        return True

    def _reload_active_project(self) -> bool:
        self._refresh_app_bar()
        self._project_root = find_project_root(Path(self._settings.default_cwd or os.getcwd()))
        self._initialize_project_setup()
        self._sync_workspace_screen()
        self.call_after_refresh(self._refresh_project_views)
        self._apply_mobile_chrome()

        if not self._project_has_vibrant_state():
            self._set_status("Project not initialized")
            self.push_screen(InitializationScreen(self._project_root))
            return False

        self._focus_primary_input()
        return True

    def action_open_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_toggle_pause(self) -> None:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return

        current_status = orchestrator.get_workflow_status()
        normalized_status = _normalize_workflow_status(current_status)
        if normalized_status is WorkflowStatus.PAUSED:
            next_status = self._paused_return_status or self._infer_resume_status()
        elif normalized_status in {WorkflowStatus.PLANNING, WorkflowStatus.EXECUTING}:
            self._paused_return_status = normalized_status
            next_status = WorkflowStatus.PAUSED
        else:
            label = normalized_status.value if normalized_status is not None else str(current_status)
            self.notify(f"Cannot toggle pause from {label}.", severity="warning")
            return

        try:
            if next_status is WorkflowStatus.PAUSED:
                await orchestrator.pause_policies("user_paused")
            else:
                await orchestrator.resume_policies()
                self._start_automatic_workflow_if_needed()
        except Exception as exc:
            logger.exception("Failed to toggle workflow pause state")
            self.notify(f"Failed to update workflow state: {exc}", severity="error")
            self._set_status(f"Workflow update failed: {exc}")
            return

        if next_status is WorkflowStatus.PAUSED:
            self._set_status("Workflow paused")
            self.notify("Workflow paused.")
        else:
            self._paused_return_status = None
            self._set_status(f"Workflow resumed ({next_status.value})")
            self.notify(f"Workflow resumed ({next_status.value}).")

        self._transition_workflow_state(next_status)
        self._refresh_project_views()

    def action_cycle_agent_output(self) -> None:
        vibing_screen = self.vibing_screen()
        if vibing_screen is None or not self._agent_logs_tab_available():
            self.notify(
                "Agent logs are disabled. Enable `[ui] show-agent-logs` or run with `--dev`.",
                severity="warning",
            )
            return
        vibing_screen.agent_output.action_cycle_agent()

    def action_show_task_status(self) -> None:
        vibing_screen = self.vibing_screen()
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

        vibing_screen = self.vibing_screen()
        if vibing_screen is None:
            self.notify("Consensus view is not available on this screen.", severity="warning")
            return
        vibing_screen.show_consensus()
        self._set_status("Opened Consensus tab")

    def action_show_chat_history(self) -> None:
        vibing_screen = self.vibing_screen()
        if vibing_screen is None:
            self.notify("Chat history is only available in the vibing screen.", severity="warning")
            return
        vibing_screen.show_chat_history()
        self._set_status("Opened Gatekeeper chat history")

    def action_show_agent_logs(self) -> None:
        vibing_screen = self.vibing_screen()
        if vibing_screen is None or not self._agent_logs_tab_available():
            self.notify(
                "Agent logs are disabled. Enable `[ui] show-agent-logs` or run with `--dev`.",
                severity="warning",
            )
            return
        vibing_screen.show_agent_logs()
        self._set_status("Opened Agent Logs tab")

    async def action_run_next_task(self) -> None:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
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
        if self.orchestrator_facade is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return

        await self.orchestrator_facade.interrupt_gatekeeper()

    async def _run_roadmap_tasks(self, *, notify_when_idle: bool) -> None:
        assert self.orchestrator_facade is not None

        automatic = self._roadmap_execution_mode() is RoadmapExecutionMode.AUTOMATIC

        self._task_execution_in_progress = True
        self._set_status("Running roadmap workflow…" if automatic else "Running next roadmap task…")
        self._start_project_refresh_loop()
        self._refresh_execution_views(include_consensus=False)

        try:
            if automatic:
                results = await self.orchestrator_facade.run_until_blocked()
                if results:
                    self._handle_task_results(results)
                elif notify_when_idle:
                    self._handle_task_result(None)
            else:
                result = await self.orchestrator_facade.run_next_task()
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
            self._refresh_execution_views(include_consensus=True)

    def _start_automatic_workflow_if_needed(self) -> None:
        orchestrator = self.orchestrator_facade
        if orchestrator is None or self._task_execution_in_progress or self._is_planning_mode():
            return
        if self._roadmap_execution_mode() is not RoadmapExecutionMode.AUTOMATIC:
            return

        snapshot = orchestrator.snapshot()
        if snapshot.pending_questions or snapshot.status in {
            WorkflowStatus.PAUSED,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
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
        assert self.orchestrator_facade is not None
        pending_question = self._current_pending_gatekeeper_question_record()
        try:
            if pending_question is not None:
                submission = await self.orchestrator_facade.answer_user_decision(
                    pending_question.question_id,
                    text,
                )
            else:
                submission = await self.orchestrator_facade.submit_user_message(text)
            self._sync_gatekeeper_conversation_binding(
                conversation_id=submission.conversation_id,
                force=False,
            )
            self._refresh_gatekeeper_views(rebind_conversation=False)

            self._refresh_post_gatekeeper_submission()
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
            self._refresh_gatekeeper_views(rebind_conversation=False)

    def _roadmap_execution_mode(self) -> RoadmapExecutionMode:
        if self.orchestrator_facade is None:
            return RoadmapExecutionMode.AUTOMATIC
        return self.orchestrator_facade.get_execution_mode()

    def _handle_task_results(self, results: list[TaskResult]) -> None:
        for result in results:
            self._handle_task_result(result)

    async def action_quit_app(self) -> None:
        self.exit()

    async def on_input_bar_message_submitted(self, event: InputBar.MessageSubmitted) -> None:
        if self.orchestrator_facade is None:
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
        model_name = self.orchestrator_facade.get_config().model if self.orchestrator_facade else "N/A"
        input_bar.set_context(model_name, "sending…")
        self._set_status("Sending message to Gatekeeper…")
        self._launch_gatekeeper_message(event.text)
        self._refresh_gatekeeper_state()

    async def on_input_bar_slash_command(self, event: InputBar.SlashCommand) -> None:
        self._record_command_history_entry(event.text)
        cmd = event.command.lower()
        if cmd == "model":
            orchestrator = self.orchestrator_facade
            if orchestrator is None:
                self.notify(
                    f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                    severity="warning",
                )
                return
            if event.args:
                try:
                    orchestrator.update_config(VibrantConfigPatch(model=event.args))
                except Exception as exc:
                    logger.exception("Failed to update orchestrator model")
                    self.notify(f"Failed to update model: {exc}", severity="error")
                    self._set_status(f"Model update failed: {exc}")
                    return
                self._reload_active_project()
                self._set_status(f"Model set to {event.args}")
            else:
                self.notify(f"Current model: {orchestrator.get_config().model}")
        elif cmd == "settings":
            self.action_open_settings()
        elif cmd == "vibe":
            self._transition_to_vibing(prefer_chat_history=True)
        elif cmd in {"run", "next"}:
            await self.action_run_next_task()
        elif cmd == "restart":
            self._restart_failed_task(event.args or None)
        elif cmd == "refresh":
            self._refresh_project_views()
            self._set_status("Refreshed project views")
        elif cmd == "history":
            vibing_screen = self.vibing_screen()
            if vibing_screen is None:
                self.notify("Gatekeeper chat is already visible.")
                return
            vibing_screen.show_chat_history()
            self._set_status("Opened Gatekeeper chat history")
        elif cmd == "logs":
            vibing_screen = self.vibing_screen()
            if vibing_screen is None or not self._agent_logs_tab_available():
                self.notify(
                    "Agent logs are disabled. Enable `[ui] show-agent-logs` or run with `--dev`.",
                    severity="warning",
                )
                return
            vibing_screen.show_agent_logs()
            self._set_status("Opened Agent Logs tab")
        elif cmd == "help":
            self.notify(
                "/model <name> - Set model\n"
                "/vibe - Enter vibing phase\n"
                "/run, /next - Execute the next roadmap task or resume an interrupted attempt\n"
                "/restart [task-id] - Requeue a failed task\n"
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

        snapshot = self._project_snapshot()
        self._refresh_consensus_views(snapshot)
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
        event_type = str(event.get("type") or "")
        gatekeeper_event = _is_gatekeeper_event(event)
        event_task_id = self._task_id_for_runtime_event(event)
        if gatekeeper_event:
            self._sync_gatekeeper_conversation_binding()
            if event_type in {"content.delta", "assistant.message.delta", "assistant.thinking.delta"}:
                self._set_status("Gatekeeper is responding…")

        if event_type == "turn.started":
            self._set_status(f"Running {_event_subject(event)}…")
        elif event_type == "turn.completed":
            self._set_status(f"Completed {_event_subject(event)}")
        elif event_type == "runtime.error":
            self._set_status(_error_text_from_event(event) or "Task failed")

        if event_type in _STREAM_ONLY_EVENT_TYPES:
            return

        if event_type in {"user-input.requested", "request.opened"}:
            self._refresh_gatekeeper_views(force_flash=True)
            return

        if event_type == "task.progress":
            if not gatekeeper_event:
                self._refresh_selected_task_status_execution(task_id=event_task_id)
            return

        snapshot = self._project_snapshot()
        if snapshot is None:
            self._clear_project_dependent_views()
            self._refresh_gatekeeper_views(rebind_conversation=False)
            return

        if event_type == "turn.started":
            if gatekeeper_event:
                self._refresh_gatekeeper_views()
            else:
                self._refresh_agent_output_registry(snapshot)
                self._refresh_roadmap_views(snapshot, refresh_task_status_execution=False)
                self._refresh_selected_task_status_execution(task_id=event_task_id)
            return

        if event_type in {"turn.completed", "task.completed", "runtime.error"}:
            if not gatekeeper_event:
                self._refresh_agent_output_registry(snapshot)
                self._refresh_roadmap_views(snapshot, refresh_task_status_execution=False)
                self._refresh_selected_task_status_execution(task_id=event_task_id)
            self._refresh_consensus_views(snapshot)
            self._refresh_gatekeeper_views()
            return

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

    def _on_agent_output_conversation_event(self, event: AgentStreamEvent) -> None:
        self.call_after_refresh(self._apply_agent_output_conversation_event, event)

    def _apply_agent_output_conversation_event(self, event: AgentStreamEvent) -> None:
        vibing_screen = self.vibing_screen()
        if vibing_screen is None:
            return
        with suppress(Exception):
            vibing_screen.agent_output.ingest_stream_event(event)

    def _sync_agent_output_conversation_bindings(self, summaries: list[ConversationSummary]) -> None:
        if self.orchestrator is None:
            for subscription in self._agent_output_conversation_subscriptions.values():
                with suppress(Exception):
                    subscription.close()
            self._agent_output_conversation_subscriptions = {}
            self._agent_output_loaded_conversation_ids.clear()
            return

        desired_ids = {summary.conversation_id for summary in summaries}
        for conversation_id, subscription in list(self._agent_output_conversation_subscriptions.items()):
            if conversation_id in desired_ids:
                continue
            with suppress(Exception):
                subscription.close()
            self._agent_output_conversation_subscriptions.pop(conversation_id, None)

        self._agent_output_loaded_conversation_ids.intersection_update(desired_ids)

    def ensure_agent_output_loaded(self) -> None:
        snapshot = self._project_snapshot()
        if snapshot is None:
            return
        self._refresh_agent_output_registry(snapshot, hydrate=True)

    def _hydrate_agent_output_conversations(self, summaries: list[ConversationSummary]) -> None:
        control_plane = self._orchestrator_control_plane()
        if control_plane is None:
            return

        vibing_screen = self.vibing_screen()
        if vibing_screen is None:
            return

        agent_output = vibing_screen.agent_output
        for summary in summaries:
            conversation_id = summary.conversation_id
            if conversation_id in self._agent_output_conversation_subscriptions:
                continue
            replay = conversation_id not in self._agent_output_loaded_conversation_ids
            replay_events: list[AgentStreamEvent] = []
            replay_state = {"complete": not replay}

            def callback(
                event: AgentStreamEvent,
                *,
                _replay_events: list[AgentStreamEvent] = replay_events,
                _replay_state: dict[str, bool] = replay_state,
            ) -> None:
                if not _replay_state["complete"]:
                    _replay_events.append(event)
                    return
                self._on_agent_output_conversation_event(event)

            self._agent_output_conversation_subscriptions[conversation_id] = (
                control_plane.subscribe_conversation(
                    conversation_id,
                    callback,
                    replay=replay,
                )
            )
            replay_state["complete"] = True
            if replay_events:
                agent_output.ingest_stream_events(replay_events)
            if replay:
                self._agent_output_loaded_conversation_ids.add(conversation_id)

    def _close_orchestrator_subscriptions(self) -> None:
        for subscription in (self._runtime_event_subscription, self._gatekeeper_conversation_subscription):
            if subscription is None:
                continue
            with suppress(Exception):
                subscription.close()
        for subscription in self._agent_output_conversation_subscriptions.values():
            with suppress(Exception):
                subscription.close()
        self._runtime_event_subscription = None
        self._gatekeeper_conversation_subscription = None
        self._agent_output_conversation_subscriptions = {}
        self._agent_output_loaded_conversation_ids.clear()
        self._gatekeeper_conversation_id = None

    def _attach_orchestrator_subscriptions(self) -> None:
        self._close_orchestrator_subscriptions()
        if self.orchestrator_facade is None:
            return
        self._runtime_event_subscription = self.orchestrator_facade.subscribe_runtime_events(self._on_runtime_event)

    def _initialize_project_setup(self) -> None:
        self._close_orchestrator_subscriptions()
        self._known_pending_questions = ()
        self._gatekeeper_state_initialized = False
        self._project_config = None
        project_root = find_project_root(Path(self._settings.default_cwd or os.getcwd()))
        self._project_root = project_root
        vibrant_dir = project_root / DEFAULT_CONFIG_DIR
        if not vibrant_dir.exists():
            self.orchestrator = None
            self.orchestrator_facade = None
            return

        try:
            ensure_project_files(project_root)
            self._project_config = load_config(start_path=project_root)
            self.orchestrator = self._orchestrator_factory(project_root)
            self.orchestrator_facade = OrchestratorFacade(self.orchestrator)
            self._attach_orchestrator_subscriptions()
        except Exception as exc:
            logger.exception("Failed to initialize project lifecycle")
            self.orchestrator = None
            self.orchestrator_facade = None
            self._project_config = None
            self.notify(f"Failed to load project state: {exc}", severity="error")

    def _agent_logs_tab_available(self) -> bool:
        if self._project_config is None:
            return self._dev_mode
        return self._project_config.tui_agent_logs_visible(dev_mode=self._dev_mode)

    @staticmethod
    def _normalize_vibing_tab(tab_id: str, *, agent_logs_visible: bool) -> str:
        if tab_id == "agent-logs" and not agent_logs_visible:
            return "task-status"
        return tab_id

    def _active_directory(self) -> Path:
        return Path(self._settings.default_cwd or os.getcwd()).expanduser().resolve(strict=False)

    def _refresh_app_bar(self) -> None:
        self.sub_title = _display_path(self._active_directory())

    def _orchestrator_control_plane(self) -> InterfaceControlPlane | None:
        orchestrator = self.orchestrator
        if orchestrator is None:
            return None
        control_plane = getattr(orchestrator, "control_plane", None)
        if control_plane is not None:
            return control_plane
        fallback = getattr(orchestrator, "_control_plane", None)
        if fallback is None:
            return None
        return fallback

    def _project_has_vibrant_state(self) -> bool:
        return (self._project_root / DEFAULT_CONFIG_DIR).exists()

    def _focus_primary_input(self) -> None:
        if not _should_autofocus_primary_input(is_web=self.is_web, width=self.size.width):
            return
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

        # Newly mounted widgets are not immediately queryable in the same frame.
        # Refresh once more after mount so task and conversation views bind reliably.
        self.call_after_refresh(self._refresh_workspace_bound_views)

    def _refresh_workspace_bound_views(self) -> None:
        """Refresh workspace widgets after the mounted tree is available."""

        if self._workspace_screen is None:
            return

        snapshot = self._project_snapshot()
        if snapshot is None:
            self._clear_project_dependent_views()
            self._refresh_gatekeeper_views(rebind_conversation=False)
            return

        self._refresh_agent_output_registry(snapshot)
        self._refresh_roadmap_views(snapshot)
        self._refresh_consensus_views(snapshot)
        self._refresh_gatekeeper_views()

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
        planning_mode = self.orchestrator_facade is None or self._is_planning_mode()
        self.set_class(planning_mode, "planning-mode")
        self.set_class(not planning_mode, "vibing-mode")

        if planning_mode:
            if not isinstance(self._workspace_screen, PlanningScreen):
                self._mount_workspace(PlanningScreen())
        else:
            agent_logs_visible = self._agent_logs_tab_available()
            initial_tab = "chat-history" if prefer_chat_history else "task-status"
            if isinstance(self._workspace_screen, VibingScreen):
                active_tab = self._normalize_vibing_tab(
                    self._workspace_screen.active_tab,
                    agent_logs_visible=agent_logs_visible,
                )
                if self._workspace_screen.agent_logs_visible != agent_logs_visible:
                    initial_tab = "chat-history" if prefer_chat_history else active_tab
                    self._mount_workspace(
                        VibingScreen(
                            initial_tab=initial_tab,
                            show_agent_logs=agent_logs_visible,
                        )
                    )
                elif prefer_chat_history:
                    self._workspace_screen.show_chat_history()
            else:
                self._mount_workspace(
                    VibingScreen(
                        initial_tab=initial_tab,
                        show_agent_logs=agent_logs_visible,
                    )
                )

        if self._workspace_screen is None:
            return

        placeholder = self._default_input_placeholder()
        self.call_after_refresh(self._apply_workspace_placeholder, placeholder)
        self.refresh_bindings()

    def _refresh_workspace_shell(self, *, prefer_chat_history: bool = False) -> None:
        self._sync_workspace_screen(prefer_chat_history=prefer_chat_history)

    def _transition_to_vibing(self, *, prefer_chat_history: bool) -> bool:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            self.notify("Initialize a project before entering the vibing phase.", severity="warning")
            return False

        try:
            for _ in range(2):
                current_status = _normalize_workflow_status(orchestrator.get_workflow_status())
                if current_status not in {WorkflowStatus.INIT, WorkflowStatus.PLANNING}:
                    break
                next_status = (
                    WorkflowStatus.PLANNING
                    if current_status is WorkflowStatus.INIT
                    else WorkflowStatus.EXECUTING
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
        self._apply_mobile_chrome()
        self._set_status("Entered vibing phase")
        self._start_automatic_workflow_if_needed()
        return True

    def _refresh_project_views(self) -> None:
        self._refresh_workspace_shell()
        snapshot = self._project_snapshot()
        if snapshot is None:
            self._clear_project_dependent_views()
            self._refresh_gatekeeper_views(rebind_conversation=False)
            return

        self._refresh_agent_output_registry(snapshot)
        self._refresh_roadmap_views(snapshot)
        self._refresh_consensus_views(snapshot)
        self._refresh_gatekeeper_views()

    def _project_snapshot(self) -> OrchestratorSnapshot | None:
        if self.orchestrator_facade is None:
            return None
        return self.orchestrator_facade.snapshot()

    def _clear_gatekeeper_conversation_binding(self) -> None:
        chat_panel = self._chat_panel()
        if self._gatekeeper_conversation_subscription is not None:
            with suppress(Exception):
                self._gatekeeper_conversation_subscription.close()
        self._gatekeeper_conversation_subscription = None
        self._gatekeeper_conversation_id = None
        if chat_panel is not None:
            chat_panel.clear_conversation()

    def _clear_project_dependent_views(self) -> None:
        self._clear_gatekeeper_conversation_binding()
        self._sync_agent_output_conversation_bindings([])

        vibing_screen = self.vibing_screen()
        if vibing_screen is not None:
            with suppress(Exception):
                vibing_screen.plan_tree.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
            with suppress(Exception):
                vibing_screen.agent_output.clear_agents("No `.vibrant/roadmap.md` found for this workspace.")
            with suppress(Exception):
                self._clear_consensus_view(vibing_screen.consensus_view)

        planning_screen = self._planning_screen()
        if planning_screen is not None:
            with suppress(Exception):
                self._clear_consensus_view(planning_screen.consensus_view)

    def _refresh_gatekeeper_views(
        self,
        *,
        force_flash: bool = False,
        rebind_conversation: bool = True,
    ) -> None:
        if rebind_conversation:
            if self.orchestrator is None:
                self._clear_gatekeeper_conversation_binding()
            else:
                self._sync_gatekeeper_conversation_binding()
        self._refresh_gatekeeper_state(force_flash=force_flash)

    def _refresh_roadmap_views(
        self,
        snapshot: OrchestratorSnapshot | None,
        *,
        refresh_task_status_execution: bool = True,
    ) -> None:
        vibing_screen = self.vibing_screen()
        if vibing_screen is None:
            return

        if snapshot is None:
            with suppress(Exception):
                vibing_screen.plan_tree.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
            with suppress(Exception):
                vibing_screen.task_status.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
            return

        roadmap = snapshot.roadmap
        if roadmap is None:
            with suppress(Exception):
                vibing_screen.plan_tree.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
            with suppress(Exception):
                vibing_screen.task_status.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
            return
        roadmap_tasks = roadmap.tasks
        task_summaries = self._collect_task_summaries()
        vibing_screen.sync_task_views(
            roadmap_tasks,
            facade=self.orchestrator_facade,
            agent_summaries=task_summaries,
            refresh_task_status_execution=refresh_task_status_execution,
        )

    def _refresh_selected_task_status_execution(
        self,
        *,
        task_id: str | None = None,
    ) -> None:
        vibing_screen = self.vibing_screen()
        if vibing_screen is None:
            return

        selected_task_id = vibing_screen.task_status.selected_task_id
        if task_id is not None and selected_task_id != task_id:
            return

        with suppress(Exception):
            vibing_screen.task_status.refresh_selected_task_execution()

    def _refresh_agent_output_registry(
        self,
        snapshot: OrchestratorSnapshot | None,
        *,
        hydrate: bool | None = None,
    ) -> None:
        control_plane = self._orchestrator_control_plane()
        if snapshot is None or control_plane is None:
            self._sync_agent_output_conversation_bindings([])
            vibing_screen = self.vibing_screen()
            if vibing_screen is not None:
                with suppress(Exception):
                    vibing_screen.agent_output.clear_agents("No `.vibrant/roadmap.md` found for this workspace.")
            return

        vibing_screen = self.vibing_screen()
        if vibing_screen is None:
            return

        with suppress(Exception):
            conversation_summaries = control_plane.list_conversation_summaries()
            vibing_screen.agent_output.sync_conversations(conversation_summaries, snapshot.agent_records)
            self._sync_agent_output_conversation_bindings(conversation_summaries)
            should_hydrate = hydrate if hydrate is not None else vibing_screen.active_tab == "agent-logs"
            if should_hydrate:
                self._hydrate_agent_output_conversations(conversation_summaries)

    def _refresh_consensus_views(self, snapshot: OrchestratorSnapshot | None) -> None:
        consensus_document = snapshot.consensus if snapshot is not None else None

        vibing_screen = self.vibing_screen()
        if vibing_screen is not None:
            with suppress(Exception):
                self._update_consensus_view(vibing_screen.consensus_view, consensus_document)

        planning_screen = self._planning_screen()
        if planning_screen is not None:
            with suppress(Exception):
                self._update_consensus_view(planning_screen.consensus_view, consensus_document)
            if self._should_auto_reveal_consensus(consensus_document):
                planning_screen.reveal_consensus_once()

    def _refresh_execution_views(self, *, include_consensus: bool) -> None:
        snapshot = self._project_snapshot()
        if snapshot is None:
            self._clear_project_dependent_views()
            self._refresh_gatekeeper_views(rebind_conversation=False)
            return

        self._refresh_agent_output_registry(snapshot)
        self._refresh_roadmap_views(snapshot, refresh_task_status_execution=False)
        self._refresh_selected_task_status_execution()
        if include_consensus:
            self._refresh_consensus_views(snapshot)
        self._refresh_gatekeeper_views()

    def _refresh_post_gatekeeper_submission(self) -> None:
        self._refresh_workspace_shell()
        snapshot = self._project_snapshot()
        if snapshot is None:
            self._clear_project_dependent_views()
            self._refresh_gatekeeper_views(rebind_conversation=False)
            return
        self._refresh_roadmap_views(snapshot)
        self._refresh_consensus_views(snapshot)
        self._refresh_gatekeeper_views(rebind_conversation=False)

    def _collect_task_summaries(self) -> dict[str, str]:
        if self.orchestrator_facade is None:
            return {}
        return self.orchestrator_facade.get_task_summaries()

    def _task_id_for_runtime_event(self, event: CanonicalEvent) -> str | None:
        task_id = event.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            return task_id.strip()

        run_id = event.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            return None

        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            return None
        return orchestrator.task_id_for_run(run_id)

    def _update_consensus_view(
        self,
        consensus_view: ConsensusView,
        document: ConsensusDocument | None,
    ) -> None:
        consensus_view.update_consensus(document)


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
            if orchestrator and orchestrator.get_workflow_status() is WorkflowStatus.COMPLETED:
                self.notify("Workflow completed.")
                self._set_status("Workflow completed")
            elif self._pending_question_records():
                self._set_status("awaiting user input")
            else:
                self._notify_no_ready_task()
            return

        task_label = result.task_id or "task"
        if result.outcome == "accepted":
            completed = bool(orchestrator and orchestrator.get_workflow_status() is WorkflowStatus.COMPLETED)
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
            self._set_status("awaiting user input")
        elif result.outcome == "interrupted":
            error = result.error or "Task interrupted. Resume is available."
            self.notify(str(error), severity="warning")
            self._set_status(f"Task {task_label} interrupted")
        elif result.outcome == "failed":
            error = result.error or "Task failed."
            self.notify(str(error), severity="error")
            self._set_status(f"Task {task_label} failed")
        else:
            self._set_status(f"Task result: {result.outcome}")

    def _notify_no_ready_task(self) -> None:
        self.notify("No ready roadmap task found.", severity="information")
        self._set_status("No ready roadmap task found")

    def _restart_failed_task(self, task_id: str | None) -> bool:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            self.notify(
                f"No Vibrant project found under {self._project_root}. Run `vibrant init` first.",
                severity="warning",
            )
            return False

        resolved_task_id = (task_id or "").strip()
        if not resolved_task_id:
            resolved_task_id = self._default_restart_task_id(orchestrator) or ""
        if not resolved_task_id:
            self.notify("Select a failed task or pass a task id to `/restart`.", severity="warning")
            return False

        try:
            task = orchestrator.restart_failed_task(resolved_task_id)
        except Exception as exc:
            logger.exception("Failed to restart task")
            self.notify(f"Failed to restart task {resolved_task_id}: {exc}", severity="error")
            self._set_status(f"Failed to restart task {resolved_task_id}: {exc}")
            return False

        self._refresh_project_views()
        self.notify(f"Task {task.id} queued for retry.")
        self._set_status(f"Task {task.id} queued for retry")
        self._start_automatic_workflow_if_needed()
        return True

    def _default_restart_task_id(self, orchestrator: OrchestratorFacade) -> str | None:
        selected_task_id = None
        vibing_screen = self.vibing_screen()
        if vibing_screen is not None:
            selected_task_id = vibing_screen.task_status.selected_task_id
        get_task = getattr(orchestrator, "get_task", None)
        if selected_task_id:
            if not callable(get_task):
                return selected_task_id
            selected_task = get_task(selected_task_id)
            if selected_task is not None and selected_task.status is TaskStatus.FAILED:
                return selected_task_id

        get_roadmap = getattr(orchestrator, "get_roadmap", None)
        roadmap = get_roadmap() if callable(get_roadmap) else None
        failed_tasks = [task for task in roadmap.tasks if task.status is TaskStatus.FAILED] if roadmap is not None else []
        if not failed_tasks:
            return None
        if len(failed_tasks) == 1:
            return failed_tasks[0].id

        failed_task_ids = {task.id for task in failed_tasks}
        latest_failed_task_id: str | None = None
        latest_failed_updated_at = ""
        list_attempt_executions = getattr(orchestrator, "list_attempt_executions", None)
        try:
            executions = list_attempt_executions() if callable(list_attempt_executions) else []
        except Exception:
            executions = []
        for execution in executions:
            if execution.task_id not in failed_task_ids or execution.status is not AttemptStatus.FAILED:
                continue
            updated_at = execution.updated_at or ""
            if updated_at >= latest_failed_updated_at:
                latest_failed_updated_at = updated_at
                latest_failed_task_id = execution.task_id
        if latest_failed_task_id is not None:
            return latest_failed_task_id
        return failed_tasks[0].id

    def _sync_gatekeeper_conversation_binding(
        self,
        *,
        conversation_id: str | None = None,
        force: bool = False,
    ) -> None:
        chat_panel = self._chat_panel()
        if self.orchestrator_facade is None or chat_panel is None:
            return

        resolved_conversation_id = conversation_id
        if resolved_conversation_id is None:
            resolved_conversation_id = self.orchestrator_facade.gatekeeper_conversation_id()

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
                chat_panel.bind_conversation(self.orchestrator_facade.conversation(resolved_conversation_id))
            return

        if self._gatekeeper_conversation_subscription is not None:
            with suppress(Exception):
                self._gatekeeper_conversation_subscription.close()

        self._gatekeeper_conversation_id = resolved_conversation_id
        chat_panel.bind_conversation(self.orchestrator_facade.conversation(resolved_conversation_id))
        self._gatekeeper_conversation_subscription = self.orchestrator_facade.subscribe_conversation(
            resolved_conversation_id,
            self._on_gatekeeper_conversation_event,
            replay=False,
        )

    def _current_pending_gatekeeper_question_record(self):
        pending = self._pending_question_records()
        return pending[0] if pending else None

    def _list_question_records(self) -> list[QuestionView]:
        facade = self.orchestrator_facade
        if facade is None:
            return []
        return list(facade.list_question_records())

    def _pending_question_records(self) -> list[QuestionView]:
        facade = self.orchestrator_facade
        if facade is None:
            return []
        return list(facade.list_pending_question_records())

    def _notification_bell_enabled(self) -> bool:
        facade = self.orchestrator_facade
        if facade is None:
            return False

        try:
            snapshot = facade.snapshot()
        except Exception:
            return False
        return bool(getattr(snapshot, "notification_bell_enabled", False))

    def _gatekeeper_is_busy(self) -> bool:
        return bool(
            self._gatekeeper_request_task is not None and not self._gatekeeper_request_task.done()
        ) or bool(self.orchestrator_facade and self.orchestrator_facade.gatekeeper_busy())

    def _gatekeeper_interrupt_supported(self) -> bool:
        orchestrator = self.orchestrator_facade
        return callable(getattr(orchestrator, "interrupt_gatekeeper", None))

    def _refresh_gatekeeper_state(self, *, force_flash: bool = False) -> None:
        chat_panel = self._chat_panel()
        input_bar = self._input_bar()
        if chat_panel is None or input_bar is None:
            return

        question_records = self._list_question_records()
        questions = [record.text for record in question_records if record.status == QuestionStatus.PENDING]
        status = self.orchestrator_facade.get_workflow_status() if self.orchestrator_facade is not None else None

        normalized_status = _normalize_workflow_status(status)
        if normalized_status in {WorkflowStatus.PLANNING, WorkflowStatus.EXECUTING}:
            self._paused_return_status = normalized_status

        new_questions = [question for question in questions if question not in self._known_pending_questions]
        flash = force_flash or (self._gatekeeper_state_initialized and bool(new_questions))
        with suppress(Exception):
            chat_panel.set_gatekeeper_state(
                status=normalized_status or status,
                question_records=question_records,
                flash=flash,
            )
        model_name = self.orchestrator_facade.get_config().model if self.orchestrator_facade else "N/A"
        
        if questions and not self._gatekeeper_is_busy():
            self.sub_title = "awaiting user input"
            self._set_banner(None)
            input_bar.set_enabled(True)
            input_bar.set_context(model_name, "awaiting user input")
            input_bar.set_placeholder(self._default_input_placeholder())
            self._set_status("awaiting user input")
            if flash and self._notification_bell_enabled():
                with suppress(Exception):
                    self.bell()
        elif self._gatekeeper_is_busy():
            self._refresh_app_bar()
            self._set_banner("Gatekeeper is responding…")
            input_bar.set_enabled(False)
            input_bar.set_context(model_name, "running… · Esc to interrupt")
            input_bar.set_placeholder("Gatekeeper is responding… Press Esc to interrupt.")
        else:
            self._refresh_app_bar()
            self._set_banner(None)
            if normalized_status is WorkflowStatus.INIT:
                input_bar.set_enabled(True)
                input_bar.set_context(model_name, "describe your goal")
            elif normalized_status is WorkflowStatus.PLANNING:
                input_bar.set_enabled(True)
                input_bar.set_context(model_name, "planning")
            elif normalized_status is WorkflowStatus.PAUSED:
                input_bar.set_enabled(True)
                input_bar.set_context(model_name, "paused")
            else:
                input_bar.set_enabled(True)
                input_bar.set_context(model_name, "feedback")
            input_bar.set_placeholder(self._default_input_placeholder())

        self._known_pending_questions = tuple(questions)
        self._gatekeeper_state_initialized = True

    def _default_input_placeholder(self) -> str:
        return (
            "Tell me what you want to build"
            if self.orchestrator_facade is None or self._is_planning_mode()
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

    def vibing_screen(self) -> VibingScreen | None:
        if isinstance(self._workspace_screen, VibingScreen):
            return self._workspace_screen
        return None

    def _infer_resume_status(self) -> WorkflowStatus:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            return WorkflowStatus.EXECUTING
        return orchestrator.infer_resume_status()

    def _transition_workflow_state(self, next_status: WorkflowStatus) -> None:
        orchestrator = self.orchestrator_facade
        if orchestrator is None:
            raise RuntimeError("Project lifecycle is not initialized")

        current_status = _normalize_workflow_status(orchestrator.get_workflow_status())
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
                self._refresh_execution_views(include_consensus=False)
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise

    def _is_planning_mode(self) -> bool:
        if self.orchestrator_facade is None:
            return False
        status = _normalize_workflow_status(self.orchestrator_facade.get_workflow_status())
        return status in {WorkflowStatus.INIT, WorkflowStatus.PLANNING}

    def _maybe_sync_post_planning_transition(self) -> bool:
        if self._planning_screen() is None or self.orchestrator_facade is None:
            return False

        status = _normalize_workflow_status(self.orchestrator_facade.get_workflow_status())
        if status in {None, WorkflowStatus.INIT, WorkflowStatus.PLANNING}:
            return False

        self._todo_exit_message = None
        return self._transition_to_vibing(prefer_chat_history=True)

    def get_todo_exit_message(self) -> str | None:
        return self._todo_exit_message

def _normalize_workflow_status(status: object) -> WorkflowStatus | None:
    if isinstance(status, WorkflowStatus):
        return status
    if isinstance(status, str):
        normalized = status.strip().lower()
        try:
            return WorkflowStatus(normalized)
        except ValueError:
            return None
    return None


def _display_path(path: Path) -> str:
    try:
        home = Path.home().resolve()
    except Exception:
        home = None

    if home is not None:
        try:
            relative_to_home = path.relative_to(home)
        except ValueError:
            pass
        else:
            return "~" if not relative_to_home.parts else f"~/{relative_to_home.as_posix()}"

    return path.as_posix()


def _is_gatekeeper_event(event: CanonicalEvent) -> bool:
    role = event.get("role")
    if isinstance(role, str) and role == "gatekeeper":
        return True

    agent_id = event.get("agent_id")
    if isinstance(agent_id, str) and (agent_id == "gatekeeper" or agent_id.startswith("gatekeeper-")):
        return True
    return isinstance(agent_id, str) and agent_id == "gatekeeper"


def _event_subject(event: CanonicalEvent) -> str:
    task_id = event.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        return task_id.strip()

    role = event.get("role")
    if isinstance(role, str) and role.strip():
        return role.strip()

    agent_id = event.get("agent_id")
    if isinstance(agent_id, str) and agent_id.strip():
        return agent_id.strip()

    run_id = event.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()

    return "run"


def _error_text_from_event(event: CanonicalEvent) -> str:
    error_message = event.get("error_message")
    if isinstance(error_message, str) and error_message.strip():
        return error_message.strip()
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "").strip()
