"""Main Textual application for Vibrant."""

from __future__ import annotations

import asyncio
import logging
import os

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Ready
from textual import work
from textual.widgets import Footer, Header, Static

from ..history import HistoryStore
from ..models import AppSettings, ThreadInfo, ThreadStatus
from ..session_manager import (
    ApprovalRequested,
    ItemAdded,
    SessionEvent,
    SessionManager,
    StreamingDelta,
    ThreadCreated,
    ThreadStatusChanged,
    TurnCompleted,
    TurnStarted,
)
from .widgets.conversation_view import ConversationView
from .widgets.input_bar import InputBar
from .widgets.settings_panel import SettingsPanel
from .widgets.thread_list import ThreadList

logger = logging.getLogger(__name__)


class VibrantApp(App):
    """Terminal UI for managing multiple OpenAI Codex agent threads."""

    TITLE = "Vibrant"
    SUB_TITLE = "Multi-agent orchestration control plane"

    CSS = """
    /* ── Layout ── */
    #main-layout {
        layout: horizontal;
        height: 1fr;
    }

    #sidebar {
        width: 30;
        min-width: 24;
        max-width: 40;
        height: 100%;
        border-right: tall $primary-background;
        background: $surface;
    }

    #thread-list-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-align: center;
    }

    #thread-listview {
        height: 1fr;
    }

    #content-area {
        width: 1fr;
        height: 100%;
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

    /* ── Messages ── */
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

    /* ── Input Bar ── */
    #input-panel {
        height: auto;
        max-height: 8;
        dock: bottom;
        border-top: tall $primary-background;
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

    /* ── Collapsible ── */
    .command-collapsible {
        margin: 0;
        padding: 0;
    }

    .reasoning-collapsible {
        margin: 0;
        padding: 0;
    }

    .msg-command-output {
        background: $surface;
        padding: 0 1;
        color: $text-muted;
        max-height: 30;
        overflow-y: auto;
    }

    /* ── Status Bar ── */
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
        Binding("ctrl+q", "quit_app", "Quit", show=True),
        Binding("ctrl+d", "delete_thread", "Delete Thread", show=False),
    ]

    def __init__(
        self,
        settings: AppSettings | None = None,
        cwd: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or AppSettings()
        if cwd:
            self._settings.default_cwd = cwd
        self._session_manager = SessionManager()
        self._history = HistoryStore(self._settings.history_dir)
        self._active_thread_id: str | None = None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-layout"):
            yield ThreadList(id="sidebar")
            with Vertical(id="content-area"):
                yield ConversationView(id="conversation-panel")
                yield InputBar(id="input-panel")
        yield Static("Ready · Ctrl+N new thread · Ctrl+Q quit", id="status-bar")
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        """Wire up event listeners and load history."""
        self._session_manager.add_listener(self._on_session_event)

        # Load saved threads from disk into the sidebar
        saved_threads = self._history.list_threads()
        if saved_threads:
            for thread in saved_threads:
                self._session_manager._threads[thread.id] = thread
            self._set_status(f"Loaded {len(saved_threads)} saved thread(s)")

        self._refresh_thread_list()
        input_bar = self.query_one(InputBar)
        input_bar.focus_input()

    async def on_unmount(self) -> None:
        """Clean up sessions on exit."""
        await self._session_manager.stop_all()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def action_new_thread(self) -> None:
        """Create a new Codex thread."""
        self._set_status("Creating new thread…")
        try:
            config = self._settings.to_session_config()
            thread = await self._session_manager.create_session(config)
            self._active_thread_id = thread.id
            self._refresh_thread_list()
            self._show_thread(thread)
            self._set_status(f"Thread created · {thread.model or 'default'}")
        except Exception as e:
            self._set_status(f"Error: {e}")
            self.notify(f"Failed to create thread: {e}", severity="error")

    async def action_cycle_thread(self) -> None:
        """Cycle to the next thread."""
        threads = self._session_manager.list_threads()
        if not threads:
            return
        if self._active_thread_id is None:
            self._active_thread_id = threads[0].id
        else:
            ids = [t.id for t in threads]
            try:
                idx = ids.index(self._active_thread_id)
                self._active_thread_id = ids[(idx + 1) % len(ids)]
            except ValueError:
                self._active_thread_id = ids[0]
        thread = self._session_manager.get_thread(self._active_thread_id)
        if thread:
            self._show_thread(thread)
            self._refresh_thread_list()

    @work
    async def action_open_settings(self) -> None:
        """Open the settings modal."""
        result = await self.push_screen_wait(SettingsPanel(self._settings))
        if result:
            self._settings = result
            self._set_status("Settings updated")

    async def action_delete_thread(self) -> None:
        """Delete the active thread."""
        if not self._active_thread_id:
            return
        thread_id = self._active_thread_id
        await self._session_manager.stop_session(thread_id)
        self._history.delete_thread(thread_id)
        # Switch to next thread
        threads = self._session_manager.list_threads()
        remaining = [t for t in threads if t.id != thread_id]
        if remaining:
            self._active_thread_id = remaining[0].id
            self._show_thread(remaining[0])
        else:
            self._active_thread_id = None
            self.query_one(ConversationView).clear()
        self._refresh_thread_list()
        self._set_status("Thread deleted")

    async def action_quit_app(self) -> None:
        """Save state and quit."""
        # Persist all threads
        for thread in self._session_manager.list_threads():
            self._history.save_thread(thread)
        await self._session_manager.stop_all()
        self.exit()

    # ------------------------------------------------------------------
    # Message handlers from widgets
    # ------------------------------------------------------------------

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
        """Send a message to the active thread."""
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
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            input_bar.set_enabled(True)

    async def on_input_bar_slash_command(self, event: InputBar.SlashCommand) -> None:
        """Handle slash commands."""
        cmd = event.command.lower()
        if cmd == "model":
            if event.args:
                self._settings.default_model = event.args
                self._set_status(f"Model set to {event.args}")
            else:
                self.notify(f"Current model: {self._settings.default_model}")
        elif cmd == "settings":
            await self.action_open_settings()
        elif cmd == "history":
            await self.action_open_history()
        elif cmd == "logs":
            if not self._active_thread_id:
                self.notify("Create a thread first (Ctrl+N)", severity="warning")
                return
            native_log, canonical_log = self._session_manager.get_provider_log_paths(self._active_thread_id)
            if native_log or canonical_log:
                self.notify(
                    f"Native log: {native_log or 'n/a'}\nCanonical log: {canonical_log or 'n/a'}"
                )
            else:
                self.notify("No provider logs available for this thread", severity="warning")
        elif cmd == "help":
            self.notify(
                "/model <name> - Set model\n"
                "/settings - Open settings\n"
                "/logs - Show provider log paths\n"
                "/help - Show this help"
            )
        else:
            self.notify(f"Unknown command: /{cmd}", severity="warning")

    # ------------------------------------------------------------------
    # Session event handler
    # ------------------------------------------------------------------

    async def _on_session_event(self, event: SessionEvent) -> None:
        """Handle domain events from the session manager."""
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
            # Live streaming update — update the streaming text in the conversation
            if event.thread_id == self._active_thread_id:
                conv = self.query_one(ConversationView)
                conv.update_streaming_text(event.accumulated_text)
                self._set_status("Receiving…")

        elif isinstance(event, ItemAdded):
            if event.thread_id == self._active_thread_id and thread:
                conv = self.query_one(ConversationView)
                conv.show_thread(thread)

        elif isinstance(event, TurnCompleted):
            if event.thread_id == self._active_thread_id and thread:
                conv = self.query_one(ConversationView)
                conv.show_thread(thread)
                # Auto-save
                self._history.save_thread(thread)
                self._set_status("Turn completed")

        elif isinstance(event, ApprovalRequested):
            # For now, auto-approve in full-auto mode
            if self._settings.default_approval_mode.value == "full-auto":
                await self._session_manager.approve_request(
                    event.thread_id, event.jsonrpc_id, approved=True,
                )
            else:
                self.notify(
                    f"Approval requested: {event.method}\nUse /approve or /reject",
                    severity="warning",
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_thread_list(self) -> None:
        """Refresh the sidebar thread list."""
        sidebar = self.query_one(ThreadList)
        threads = self._session_manager.list_threads()
        sidebar.update_threads(threads)
        if self._active_thread_id:
            sidebar.selected_thread_id = self._active_thread_id

    def _show_thread(self, thread: ThreadInfo) -> None:
        """Display a thread in the conversation view."""
        conv = self.query_one(ConversationView)
        conv.show_thread(thread)
        input_bar = self.query_one(InputBar)
        input_bar.set_context(thread.model, thread.status.value)
        input_bar.set_enabled(thread.status != ThreadStatus.RUNNING)
        input_bar.focus_input()

    def _set_status(self, text: str) -> None:
        """Update the status bar."""
        try:
            status = self.query_one("#status-bar", Static)
            status.update(text)
        except Exception:
            pass
