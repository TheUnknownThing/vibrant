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
from textual.containers import Grid, Vertical
from textual.widgets import Footer, Header, Static

from ..config import DEFAULT_CONFIG_DIR, find_project_root
from ..history import HistoryStore
from ..models import AppSettings, ThreadInfo, ThreadStatus
from ..orchestrator import CodeAgentLifecycle, CodeAgentLifecycleResult
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
from .widgets.agent_output import AgentOutput
from .widgets.chat_panel import ChatPanel
from .widgets.consensus_view import ConsensusView
from .widgets.input_bar import InputBar
from .widgets.plan_tree import PlanTree
from .widgets.settings_panel import SettingsPanel
from .widgets.thread_list import ThreadList

logger = logging.getLogger(__name__)
LifecycleFactory = Callable[..., CodeAgentLifecycle]


class VibrantApp(App):
    """Terminal UI for managing roadmap execution and Codex conversations."""

    TITLE = "Vibrant"
    SUB_TITLE = "Multi-agent orchestration control plane"

    CSS = """
    #workspace-grid {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 34 1fr;
        grid-rows: 1fr 1fr;
        height: 1fr;
    }

    #plan-panel,
    #agent-output-panel,
    #consensus-panel,
    #chat-panel-container {
        min-height: 10;
    }

    #chat-panel-container {
        border: round $primary-background;
        background: $surface;
        padding: 0;
    }

    #thread-panel {
        height: 11;
        border-bottom: solid $primary-background;
    }

    #conversation-panel {
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

    #status-bar {
        dock: bottom;
        height: 1;
        background: $primary-background;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_thread", "New Thread", show=True),
        Binding("ctrl+t", "cycle_thread", "Next Thread", show=True),
        Binding("ctrl+s", "open_settings", "Settings", show=True),
        Binding("f5", "cycle_agent_output", "Next Agent", show=True),
        Binding("f6", "run_next_task", "Run Task", show=True),
        Binding("ctrl+d", "delete_thread", "Delete Thread", show=False),
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
        self._history = HistoryStore(self._settings.history_dir)
        self._active_thread_id: str | None = None
        self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._lifecycle_factory = lifecycle_factory or CodeAgentLifecycle
        self._lifecycle: CodeAgentLifecycle | None = None
        self._task_execution_in_progress = False
        self._task_refresh_loop: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Grid(id="workspace-grid"):
            yield PlanTree(id="plan-panel")
            yield AgentOutput(id="agent-output-panel")
            yield ConsensusView(id="consensus-panel")
            with Vertical(id="chat-panel-container"):
                yield ThreadList(id="thread-panel")
                yield ChatPanel(id="conversation-panel")
                yield InputBar(id="input-panel")
        yield Static("Ready · F6 run next task · Enter inspect task · Ctrl+Q quit", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        """Wire up event listeners and load persisted project/thread state."""

        self._session_manager.add_listener(self._on_session_event)

        saved_threads = self._history.list_threads()
        if saved_threads:
            for thread in saved_threads:
                self._session_manager._threads[thread.id] = thread
            self._set_status(f"Loaded {len(saved_threads)} saved thread(s)")

        self._refresh_thread_list()
        self._initialize_project_lifecycle()
        self._refresh_project_views()
        self.query_one(InputBar).focus_input()

    async def on_unmount(self) -> None:
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
        threads = self._session_manager.list_threads()
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
        thread = self._session_manager.get_thread(self._active_thread_id)
        if thread:
            self._show_thread(thread)
            self._refresh_thread_list()

    async def action_open_settings(self) -> None:
        result = await self.push_screen_wait(SettingsPanel(self._settings))
        if result:
            self._settings = result
            self._project_root = find_project_root(self._settings.default_cwd or os.getcwd())
            self._initialize_project_lifecycle()
            self._refresh_project_views()
            self._set_status("Settings updated")


    def action_cycle_agent_output(self) -> None:
        self.query_one(AgentOutput).action_cycle_agent()

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

        self._task_execution_in_progress = True
        self._set_status("Running next roadmap task…")
        self._start_project_refresh_loop()
        self._refresh_project_views()

        try:
            result = await self._lifecycle.execute_next_task()
        except Exception as exc:
            logger.exception("Roadmap task execution failed")
            self.notify(f"Task execution failed: {exc}", severity="error")
            self._set_status(f"Task execution failed: {exc}")
        else:
            self._handle_task_result(result)
        finally:
            self._task_execution_in_progress = False
            await self._stop_project_refresh_loop()
            self._refresh_project_views()

    async def action_delete_thread(self) -> None:
        if not self._active_thread_id:
            return
        thread_id = self._active_thread_id
        await self._session_manager.stop_session(thread_id)
        self._history.delete_thread(thread_id)
        threads = self._session_manager.list_threads()
        remaining = [thread for thread in threads if thread.id != thread_id]
        if remaining:
            self._active_thread_id = remaining[0].id
            self._show_thread(remaining[0])
        else:
            self._active_thread_id = None
            self.query_one(ChatPanel).clear()
        self._refresh_thread_list()
        self._set_status("Thread deleted")

    async def action_quit_app(self) -> None:
        for thread in self._session_manager.list_threads():
            self._history.save_thread(thread)
        await self._session_manager.stop_all()
        self.exit()

    async def on_thread_list_thread_selected(self, event: ThreadList.ThreadSelected) -> None:
        self._active_thread_id = event.thread_id
        thread = self._session_manager.get_thread(event.thread_id)
        if thread:
            self._show_thread(thread)

    async def on_thread_list_new_thread_requested(self, _: ThreadList.NewThreadRequested) -> None:
        await self.action_new_thread()

    async def on_thread_list_delete_thread_requested(self, event: ThreadList.DeleteThreadRequested) -> None:
        self._active_thread_id = event.thread_id
        await self.action_delete_thread()

    async def on_input_bar_message_submitted(self, event: InputBar.MessageSubmitted) -> None:
        if not self._active_thread_id:
            self.notify("Create a thread first (Ctrl+N)", severity="warning")
            return
        thread = self._session_manager.get_thread(self._active_thread_id)
        if not thread or thread.status == ThreadStatus.RUNNING:
            self.notify("Thread is busy", severity="warning")
            return

        input_bar = self.query_one(InputBar)
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
            self.notify("History stays visible in the thread switcher panel.")
        elif cmd == "logs":
            if not self._active_thread_id:
                self.notify("Create a thread first (Ctrl+N)", severity="warning")
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
        else:
            self.notify(f"Unknown command: /{cmd}", severity="warning")

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

    async def _on_lifecycle_canonical_event(self, event: dict[str, Any]) -> None:
        try:
            self.query_one(AgentOutput).ingest_canonical_event(event)
        except Exception:
            logger.exception("Failed to update agent output panel")

        event_type = str(event.get("type") or "")
        if event_type in {"turn.started", "turn.completed", "runtime.error", "task.progress"}:
            self._refresh_project_views()
        if event_type == "turn.started":
            self._set_status(f"Running {event.get('task_id', 'task')}…")
        elif event_type == "turn.completed":
            self._set_status(f"Completed {event.get('task_id', 'task')}")
        elif event_type == "runtime.error":
            self._set_status(str(event.get("error") or "Task failed"))

    def _initialize_project_lifecycle(self) -> None:
        project_root = find_project_root(self._settings.default_cwd or os.getcwd())
        self._project_root = project_root
        vibrant_dir = project_root / DEFAULT_CONFIG_DIR
        if not vibrant_dir.exists():
            self._lifecycle = None
            return

        try:
            self._lifecycle = self._lifecycle_factory(project_root, on_canonical_event=self._on_lifecycle_canonical_event)
        except Exception as exc:
            logger.exception("Failed to initialize project lifecycle")
            self._lifecycle = None
            self.notify(f"Failed to load project state: {exc}", severity="error")

    def _refresh_project_views(self) -> None:
        plan_tree = self.query_one(PlanTree)
        agent_output = self.query_one(AgentOutput)
        if self._lifecycle is None:
            plan_tree.clear_tasks("No `.vibrant/roadmap.md` found for this workspace.")
            agent_output.clear_agents("No `.vibrant/roadmap.md` found for this workspace.")
            return

        agent_output.sync_agents(self._lifecycle.engine.agents.values())

        try:
            roadmap = self._lifecycle.reload_from_disk()
        except Exception as exc:
            logger.exception("Failed to refresh roadmap view")
            plan_tree.clear_tasks(f"Failed to load roadmap: {exc}")
            return

        plan_tree.update_tasks(roadmap.tasks, agent_summaries=self._collect_task_summaries())

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
            if self._lifecycle and self._lifecycle.engine.state.pending_questions:
                self.notify(self._lifecycle.engine.USER_INPUT_BANNER, severity="warning")
                self._set_status(self._lifecycle.engine.USER_INPUT_BANNER)
            else:
                self._notify_no_ready_task()
            return

        if result.outcome == "accepted":
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
        sidebar = self.query_one(ThreadList)
        threads = self._session_manager.list_threads()
        sidebar.update_threads(threads)
        if self._active_thread_id:
            sidebar.selected_thread_id = self._active_thread_id

    def _show_thread(self, thread: ThreadInfo) -> None:
        conv = self.query_one(ChatPanel)
        conv.show_thread(thread)
        input_bar = self.query_one(InputBar)
        input_bar.set_context(thread.model, thread.status.value)
        input_bar.set_enabled(thread.status != ThreadStatus.RUNNING)
        input_bar.focus_input()

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
