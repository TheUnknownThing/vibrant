"""Conversation-scoped agent log panel for the Vibrant TUI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
from typing import Iterable, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static

from ...models.agent import AgentRecord, AgentStatus
from ...orchestrator.types import AgentStreamEvent, ConversationSummary
from ...type_defs import JSONValue, is_json_mapping

MAX_DEBUG_LINES = 10_000
DEFAULT_RENDERED_BLOCKS = 120
DEFAULT_RENDERED_DEBUG_LINES = 200
WINDOW_CHUNK_SIZE = 40
MAX_RENDERED_BLOCKS = 220
MAX_RENDERED_DEBUG_LINES = 300
SCROLL_EDGE_THRESHOLD = 1.0

BlockKind = Literal[
    "reasoning",
    "output",
    "mcp_call",
    "command",
    "progress",
    "request",
    "status",
    "error",
]

_STREAMING_STATUSES = {
    AgentStatus.SPAWNING.value,
    AgentStatus.CONNECTING.value,
    AgentStatus.RUNNING.value,
    AgentStatus.AWAITING_INPUT.value,
}

_STATUS_ICONS: dict[str, str] = {
    AgentStatus.SPAWNING.value: "○",
    AgentStatus.CONNECTING.value: "◔",
    AgentStatus.RUNNING.value: "⟳",
    AgentStatus.AWAITING_INPUT.value: "⚠",
    AgentStatus.COMPLETED.value: "✓",
    AgentStatus.FAILED.value: "✗",
    AgentStatus.KILLED.value: "■",
}


@dataclass(slots=True)
class LogBlock:
    """One normalized log block that can be rendered lazily."""

    kind: BlockKind
    title: str
    text: str
    timestamp: str | None
    item_id: str | None = None
    turn_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    status: str | None = None
    streaming: bool = False
    collapsed: bool = False


@dataclass(slots=True)
class ConversationLogState:
    """Per-conversation render state and retained block data."""

    summary: ConversationSummary
    latest_agent_id: str | None = None
    latest_status: str | None = None
    blocks: list[LogBlock] = field(default_factory=list)
    debug_lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_DEBUG_LINES))
    visible_start: int = 0
    visible_end: int = 0
    debug_visible_start: int = 0
    debug_visible_end: int = 0
    unread_blocks: int = 0
    unread_debug_lines: int = 0

    @property
    def conversation_id(self) -> str:
        return self.summary.conversation_id


@dataclass(slots=True)
class _StreamEventUpdate:
    """One retained-state mutation produced by a stream event."""

    state: ConversationLogState
    changed_index: int | None
    new_block_created: bool
    debug_line_added: bool


class LogBlockWidget(Vertical):
    """Mutable widget for one rendered log block."""

    def __init__(self, block: LogBlock, *, block_index: int, **kwargs: object) -> None:
        classes = f"agent-output-entry agent-output-{block.kind}"
        super().__init__(classes=classes, **kwargs)
        self.styles.height = "auto"
        self.block_index = block_index
        self._block = block
        self._header = Static("", markup=False, classes="agent-output-block-header")
        self._preview = Static("", markup=False, classes="agent-output-block-preview")
        self._body = Static("", markup=False, classes="agent-output-block-body")

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._preview
        yield self._body

    def on_mount(self) -> None:
        self._refresh()

    def on_click(self) -> None:
        if not _is_collapsible(self._block):
            return
        self._block.collapsed = not self._block.collapsed
        self._refresh()

    def set_block(self, block: LogBlock) -> None:
        if self._block.kind != block.kind:
            self.remove_class(f"agent-output-{self._block.kind}")
            self.add_class(f"agent-output-{block.kind}")
        self._block = block
        self._refresh()

    def _refresh(self) -> None:
        self._header.update(_block_header_text(self._block))
        if not self._block.text.strip():
            self._preview.update("")
            self._preview.display = False
            self._body.update("")
            self._body.display = False
            return

        if _is_collapsible(self._block):
            if self._block.collapsed:
                self._preview.update(_preview_text(self._block.text))
                self._preview.display = True
                self._body.update("")
                self._body.display = False
                return
            self._preview.update("")
            self._preview.display = False
            self._body.update(self._block.text)
            self._body.display = True
            return

        self._preview.update(self._block.text)
        self._preview.display = True
        self._body.update("")
        self._body.display = False


class AgentOutputScroll(VerticalScroll):
    """Scrollable region that asks the owner to expand windows on demand."""

    def __init__(self, owner: "AgentOutput", *, mode: Literal["stream", "debug"], **widget_kwargs: object) -> None:
        super().__init__(**widget_kwargs)
        self._owner = owner
        self._mode = mode

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if not self.is_mounted:
            return
        if self._mode == "stream":
            self._owner._on_stream_scroll(self, old_value, new_value)
            return
        self._owner._on_debug_scroll(self, old_value, new_value)


class AgentOutput(Static):
    """Incremental, conversation-scoped log viewer for agent activity."""

    can_focus = True

    BINDINGS = [
        Binding("tab", "cycle_agent", "Next Conversation", show=False),
        Binding("s", "toggle_scroll_lock", "Scroll Lock", show=False),
        Binding("d", "toggle_debug_view", "Debug View", show=False),
    ]

    DEFAULT_CSS = """
    AgentOutput {
        border: round $primary-background;
        background: $surface;
        padding: 0;
    }

    #agent-output-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-align: center;
    }

    #agent-output-meta,
    #agent-output-tabs {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }

    #agent-output-tabs {
        border-bottom: solid $primary-background;
        padding-bottom: 1;
    }

    #agent-output-switcher {
        height: 1fr;
    }

    #agent-output-stream-scroll,
    #agent-output-debug-scroll {
        height: 1fr;
        padding: 0 1 1 1;
        scrollbar-size: 1 1;
    }

    #agent-output-stream,
    #agent-output-debug {
        width: 100%;
        padding: 0 1;
        height: auto;
    }

    #agent-output-stream > .agent-output-entry,
    #agent-output-debug > .agent-output-debug-line {
        margin-bottom: 1;
    }

    .agent-output-entry {
        padding: 0 1;
        height: auto;
    }

    .agent-output-block-header {
        color: $text;
    }

    .agent-output-block-preview,
    .agent-output-block-body {
        padding-left: 1;
        color: $text-muted;
    }

    .agent-output-output .agent-output-block-preview,
    .agent-output-output .agent-output-block-body {
        color: $text;
    }

    .agent-output-reasoning {
        background: $surface-lighten-1;
    }

    .agent-output-output {
        background: $secondary 8%;
        border-left: tall $secondary;
    }

    .agent-output-mcp_call {
        background: $primary 10%;
        border-left: tall $primary;
    }

    .agent-output-command {
        background: $panel 10%;
        border-left: tall $panel;
    }

    .agent-output-request {
        background: $warning 10%;
        border-left: tall $warning;
    }

    .agent-output-error {
        background: $error 10%;
        border-left: tall $error;
    }

    .agent-output-debug-line {
        color: $text-muted;
        padding: 0 1;
        height: auto;
    }
    """

    def __init__(self, **widget_kwargs: object) -> None:
        super().__init__(**widget_kwargs)
        self._conversations: dict[str, ConversationLogState] = {}
        self._conversation_order: list[str] = []
        self._active_conversation_id: str | None = None
        self._active_conversation_index: int | None = None
        self._auto_follow = True
        self._debug_view_enabled = False
        self._empty_message = "No agent activity yet. Use /run to execute the next roadmap task."
        self._run_records: dict[str, AgentRecord] = {}
        self._latest_run_id_by_agent: dict[str, str] = {}
        self._mounted_block_widgets: dict[int, LogBlockWidget] = {}
        self._mounted_debug_widgets: dict[int, Static] = {}
        self._mounted_stream_conversation_id: str | None = None
        self._mounted_debug_conversation_id: str | None = None
        self._stream_scroll: AgentOutputScroll | None = None
        self._debug_scroll: AgentOutputScroll | None = None
        self._stream_body: Vertical | None = None
        self._debug_body: Vertical | None = None
        self._window_mutation_guard = False

    def compose(self) -> ComposeResult:
        yield Static("[b]Agent Logs[/b]", id="agent-output-header", markup=True)
        yield Static("", id="agent-output-meta")
        yield Static("", id="agent-output-tabs")
        with ContentSwitcher(initial="agent-output-stream-scroll", id="agent-output-switcher"):
            self._stream_scroll = AgentOutputScroll(self, mode="stream", id="agent-output-stream-scroll")
            with self._stream_scroll:
                self._stream_body = Vertical(id="agent-output-stream")
                yield self._stream_body
            self._debug_scroll = AgentOutputScroll(self, mode="debug", id="agent-output-debug-scroll")
            with self._debug_scroll:
                self._debug_body = Vertical(id="agent-output-debug")
                yield self._debug_body

    def on_mount(self) -> None:
        self._update_meta()
        self._update_tabs()
        self._update_switcher()
        self._show_active_conversation(reset_window=True)

    def on_click(self) -> None:
        self.focus()

    @property
    def active_conversation_id(self) -> str | None:
        """Return the currently selected conversation id."""

        return self._active_conversation_id

    @property
    def active_agent_id(self) -> str | None:
        """Compatibility alias for the latest active agent in the current conversation."""

        state = self._active_state()
        if state is None:
            return None
        return state.latest_agent_id or (state.summary.agent_ids[-1] if state.summary.agent_ids else None)

    @property
    def auto_follow_enabled(self) -> bool:
        """Return whether the panel auto-follows the latest output."""

        return self._auto_follow

    @property
    def debug_view_enabled(self) -> bool:
        """Return whether the debug view is currently visible."""

        return self._debug_view_enabled

    def sync_conversations(
        self,
        conversations: Iterable[ConversationSummary],
        agents: Iterable[AgentRecord],
    ) -> None:
        """Refresh known conversations and runtime metadata."""

        incoming_summaries = list(conversations)
        incoming_ids = {summary.conversation_id for summary in incoming_summaries}
        for conversation_id in list(self._conversations):
            if conversation_id not in incoming_ids:
                self._conversations.pop(conversation_id, None)

        for summary in incoming_summaries:
            state = self._ensure_conversation(summary.conversation_id)
            state.summary = _merge_summary(state.summary, summary)

        self.sync_agents(agents, refresh=False)
        previous = self._active_conversation_id
        self._conversation_order = self._sorted_conversation_ids()
        self._active_conversation_id = self._resolve_active_conversation(previous)
        self._update_meta()
        self._update_tabs()
        self._update_switcher()
        self._show_active_conversation(reset_window=previous != self._active_conversation_id)

    def sync_agents(self, agents: Iterable[AgentRecord], *, refresh: bool = True) -> None:
        """Refresh per-run metadata used by conversation tabs and headers."""

        ordered_agents = sorted(
            [agent for agent in agents if self._run_id(agent)],
            key=self._agent_sort_key,
        )
        self._run_records = {
            self._run_id(agent): agent
            for agent in ordered_agents
            if self._run_id(agent) is not None
        }
        self._latest_run_id_by_agent = {}
        for agent in ordered_agents:
            agent_id = self._agent_id(agent)
            run_id = self._run_id(agent)
            if run_id is not None:
                self._latest_run_id_by_agent[agent_id] = run_id

        for state in self._conversations.values():
            self._sync_state_runtime_meta(state)

        if not refresh:
            return
        previous = self._active_conversation_id
        self._conversation_order = self._sorted_conversation_ids()
        self._active_conversation_id = self._resolve_active_conversation(previous)
        self._update_meta()
        self._update_tabs()
        self._update_switcher()
        self._show_active_conversation(reset_window=previous != self._active_conversation_id)

    def clear_agents(self, message: str | None = None) -> None:
        """Clear all retained conversation state."""

        self._conversations.clear()
        self._conversation_order.clear()
        self._active_conversation_id = None
        self._run_records.clear()
        self._latest_run_id_by_agent.clear()
        self._mounted_block_widgets.clear()
        self._mounted_debug_widgets.clear()
        self._mounted_stream_conversation_id = None
        self._mounted_debug_conversation_id = None
        if message:
            self._empty_message = message
        self._update_meta()
        self._update_tabs()
        self._update_switcher()
        self._show_active_conversation(reset_window=True)

    def ingest_stream_event(self, event: AgentStreamEvent) -> None:
        """Apply one projected conversation event to the retained log state."""

        update = self._ingest_stream_event_state(event)
        self._refresh_after_stream_event(update)

    def ingest_stream_events(self, events: Iterable[AgentStreamEvent]) -> None:
        """Apply multiple projected conversation events and refresh once at the end."""

        updates = [self._ingest_stream_event_state(event) for event in events]
        if not updates:
            return

        for update in updates:
            self._sync_state_runtime_meta(update.state)

        previous = self._active_conversation_id
        self._conversation_order = self._sorted_conversation_ids()
        self._active_conversation_id = self._resolve_active_conversation(previous)
        self._update_meta()
        self._update_tabs()
        self._update_switcher()
        self._show_active_conversation(reset_window=True)

    def _ingest_stream_event_state(self, event: AgentStreamEvent) -> "_StreamEventUpdate":
        """Mutate retained state for one event without performing any visible refresh."""

        state = self._ensure_conversation(event.conversation_id)
        _apply_event_to_summary(state.summary, event)
        if event.agent_id:
            state.latest_agent_id = event.agent_id
        if event.run_id:
            state.summary.latest_run_id = event.run_id
        debug_line = _render_stream_debug_line(event)
        if debug_line:
            state.debug_lines.append(debug_line)

        before_count = len(state.blocks)
        changed_index = self._apply_stream_event_to_blocks(state, event)
        new_block_created = len(state.blocks) > before_count

        return _StreamEventUpdate(
            state=state,
            changed_index=changed_index,
            new_block_created=new_block_created,
            debug_line_added=bool(debug_line),
        )

    def _refresh_after_stream_event(self, update: "_StreamEventUpdate") -> None:
        """Refresh visible widget state after one live stream event."""

        state = update.state
        self._sync_state_runtime_meta(state)
        previous = self._active_conversation_id
        self._conversation_order = self._sorted_conversation_ids()
        self._active_conversation_id = self._resolve_active_conversation(previous)
        active_changed = previous != self._active_conversation_id

        self._update_meta()
        self._update_tabs()
        self._update_switcher()

        if active_changed:
            self._show_active_conversation(reset_window=True)
            return

        if state.conversation_id != self._active_conversation_id:
            if update.new_block_created:
                state.unread_blocks += 1
            if update.debug_line_added:
                state.unread_debug_lines += 1
            return

        state.unread_blocks = 0
        state.unread_debug_lines = 0
        self._apply_active_stream_change(
            state,
            changed_index=update.changed_index,
            new_block_created=update.new_block_created,
            debug_line_added=update.debug_line_added,
        )

    def action_cycle_agent(self) -> None:
        """Cycle to the next known conversation."""

        if not self._conversation_order:
            return
        if self._active_conversation_id not in self._conversation_order:
            self._active_conversation_id = self._conversation_order[0]
        else:
            index = self._conversation_order.index(self._active_conversation_id)
            self._active_conversation_id = self._conversation_order[(index + 1) % len(self._conversation_order)]
        self._update_meta()
        self._update_tabs()
        self._update_switcher()
        self._show_active_conversation(reset_window=False)

    def action_toggle_scroll_lock(self) -> None:
        """Toggle auto-follow for the active view."""

        self._auto_follow = not self._auto_follow
        if self._auto_follow:
            state = self._active_state()
            if state is not None:
                self._reset_stream_window_to_tail(state)
                self._reset_debug_window_to_tail(state)
        self._update_meta()
        self._show_active_conversation(reset_window=False)

    def action_toggle_debug_view(self) -> None:
        """Switch between rendered logs and raw projected stream frames."""

        self._debug_view_enabled = not self._debug_view_enabled
        self._update_meta()
        self._update_switcher()
        self._show_active_conversation(reset_window=False)

    def poll_native_logs_now(self) -> None:
        """Compatibility no-op now that logs are stream-driven."""

    def get_rendered_text(self, target_id: str | None = None, *, debug: bool | None = None) -> str:
        """Return the normalized text for tests and diagnostics."""

        state = self._resolve_target_state(target_id)
        if state is None:
            return self._empty_message
        use_debug = self._debug_view_enabled if debug is None else debug
        if use_debug:
            return self._build_debug_text(state)
        return self._build_stream_text(state)

    def get_buffer_line_count(self, target_id: str, *, debug: bool = False) -> int:
        """Return the retained block or debug-line count for one conversation."""

        state = self._resolve_target_state(target_id)
        if state is None:
            raise KeyError(target_id)
        return len(state.debug_lines) if debug else len(state.blocks)

    def get_thoughts_text(self, target_id: str | None = None) -> str:
        """Return the latest reasoning block text."""

        state = self._resolve_target_state(target_id)
        if state is None:
            return ""
        for block in reversed(state.blocks):
            if block.kind == "reasoning":
                return block.text
        return ""

    def thoughts_running(self, target_id: str | None = None) -> bool:
        """Return whether the latest reasoning block is still streaming."""

        state = self._resolve_target_state(target_id)
        if state is None:
            return False
        for block in reversed(state.blocks):
            if block.kind == "reasoning":
                return block.streaming
        return False

    def _ensure_conversation(self, conversation_id: str) -> ConversationLogState:
        state = self._conversations.get(conversation_id)
        if state is not None:
            return state
        state = ConversationLogState(summary=_blank_summary(conversation_id))
        self._conversations[conversation_id] = state
        return state

    def _resolve_active_conversation(self, current: str | None) -> str | None:
        if current in self._conversation_order:
            return current
        if not self._conversation_order:
            return None
        live_conversations = [
            conversation_id
            for conversation_id in self._conversation_order
            if (self._conversations[conversation_id].latest_status or "") in _STREAMING_STATUSES
        ]
        if live_conversations:
            return live_conversations[-1]
        return self._conversation_order[-1]

    def _active_state(self) -> ConversationLogState | None:
        if self._active_conversation_id is None:
            return None
        return self._conversations.get(self._active_conversation_id)

    def _resolve_target_state(self, target_id: str | None) -> ConversationLogState | None:
        if target_id is None:
            return self._active_state()
        if target_id in self._conversations:
            return self._conversations[target_id]
        matching = [
            state
            for state in self._conversations.values()
            if target_id == state.latest_agent_id or target_id in state.summary.agent_ids
        ]
        if not matching:
            return None
        matching.sort(key=_conversation_state_sort_key)
        return matching[-1]

    def _sorted_conversation_ids(self) -> list[str]:
        return [
            state.conversation_id
            for state in sorted(self._conversations.values(), key=_conversation_state_sort_key)
        ]

    def _sync_state_runtime_meta(self, state: ConversationLogState) -> None:
        run_id = state.summary.latest_run_id
        run = self._run_records.get(run_id) if run_id else None
        if run is None:
            candidate_run_ids = [
                self._latest_run_id_by_agent.get(agent_id)
                for agent_id in state.summary.agent_ids
                if self._latest_run_id_by_agent.get(agent_id) is not None
            ]
            if candidate_run_ids:
                run_id = candidate_run_ids[-1]
                run = self._run_records.get(run_id)

        if run is None:
            if state.latest_agent_id is None and state.summary.agent_ids:
                state.latest_agent_id = state.summary.agent_ids[-1]
            return

        state.summary.latest_run_id = run.identity.run_id
        state.latest_agent_id = run.identity.agent_id
        state.latest_status = run.lifecycle.status.value
        task_id = getattr(run.identity, "task_id", None)
        if isinstance(task_id, str) and task_id and task_id not in state.summary.task_ids:
            state.summary.task_ids.append(task_id)
        if run.identity.agent_id not in state.summary.agent_ids:
            state.summary.agent_ids.append(run.identity.agent_id)
        if not state.summary.provider_thread_id and run.provider.provider_thread_id:
            state.summary.provider_thread_id = run.provider.provider_thread_id

    def _update_meta(self) -> None:
        if not self.is_mounted:
            return
        meta = self.query_one("#agent-output-meta", Static)
        state = self._active_state()
        if state is None:
            meta.update(self._empty_message)
            return

        summary = state.summary
        mode = "raw" if self._debug_view_enabled else "logs"
        follow = "follow" if self._auto_follow else "locked"
        status = state.latest_status or "unknown"
        task_text = ", ".join(summary.task_ids) if summary.task_ids else "n/a"
        agent_text = state.latest_agent_id or (summary.agent_ids[-1] if summary.agent_ids else "n/a")
        run_text = summary.latest_run_id or "n/a"
        thread_text = summary.provider_thread_id or "n/a"
        unread = state.unread_debug_lines if self._debug_view_enabled else state.unread_blocks
        unread_text = f" · unread {unread}" if unread else ""
        meta.update(
            "Conversation: "
            f"{summary.conversation_id} · Agent: {agent_text} · "
            f"Run: {run_text} · Thread: {thread_text} · Status: {status} · "
            f"View: {mode} · {follow}{unread_text} · Tab next · S lock · D debug"
        )

    def _update_tabs(self) -> None:
        if not self.is_mounted:
            return
        tabs = self.query_one("#agent-output-tabs", Static)
        if not self._conversation_order:
            tabs.update("No conversations yet")
            return

        parts: list[str] = []
        for conversation_id in self._conversation_order:
            state = self._conversations[conversation_id]
            status_icon = _STATUS_ICONS.get(state.latest_status or "", "•")
            unread = state.unread_debug_lines if self._debug_view_enabled else state.unread_blocks
            unread_fragment = f" +{unread}" if unread else ""
            prefix = "▶" if conversation_id == self._active_conversation_id else "•"
            parts.append(f"{prefix} {status_icon} {conversation_id}/{unread_fragment}")
        tabs.update("  |  ".join(parts))

    def _update_switcher(self) -> None:
        if not self.is_mounted:
            return
        switcher = self.query_one("#agent-output-switcher", ContentSwitcher)
        switcher.current = "agent-output-debug-scroll" if self._debug_view_enabled else "agent-output-stream-scroll"

    def _show_active_conversation(self, *, reset_window: bool) -> None:
        if not self.is_mounted:
            return
        if not self.is_showed():
            return
        
        state = self._active_state()
        if state is None:
            self._rebuild_stream_window(None)
            self._rebuild_debug_window(None)
            return

        if reset_window or not state.visible_end or state.visible_end > len(state.blocks):
            self._reset_stream_window_to_tail(state)
        if reset_window or not state.debug_visible_end or state.debug_visible_end > len(state.debug_lines):
            self._reset_debug_window_to_tail(state)
        state.unread_blocks = 0
        state.unread_debug_lines = 0
        if self._debug_view_enabled:
            self._rebuild_debug_window(state)
        else:
            self._rebuild_stream_window(state)
        if self._auto_follow:
            self._scroll_active_end()

    def is_showed(self):
        app = self.app
        vibing_screen = getattr(app, "vibing_screen", None)
        if not callable(vibing_screen):
            return False
        screen = vibing_screen()
        return bool(screen is not None and screen.active_tab == "agent-logs")

    def _apply_active_stream_change(
        self,
        state: ConversationLogState,
        *,
        changed_index: int | None,
        new_block_created: bool,
        debug_line_added: bool,
    ) -> None:
        if changed_index is None:
            if debug_line_added:
                self._apply_active_debug_change(state, line_added=True)
            return

        if self._auto_follow:
            self._apply_auto_follow_stream_change(state, changed_index=changed_index, new_block_created=new_block_created)
            self._apply_active_debug_change(state, line_added=debug_line_added)
            self._scroll_active_end()
            return

        if state.visible_start <= changed_index < state.visible_end:
            widget = self._mounted_block_widgets.get(changed_index)
            if widget is not None:
                widget.set_block(state.blocks[changed_index])
        elif new_block_created:
            state.unread_blocks += 1

        self._apply_active_debug_change(state, line_added=debug_line_added)

    def _apply_auto_follow_stream_change(
        self,
        state: ConversationLogState,
        *,
        changed_index: int,
        new_block_created: bool,
    ) -> None:
        total = len(state.blocks)
        if not total:
            self._rebuild_stream_window(state)
            return

        if not state.visible_end:
            self._reset_stream_window_to_tail(state)
            self._rebuild_stream_window(state)
            return

        if not new_block_created:
            if state.visible_start <= changed_index < state.visible_end:
                widget = self._mounted_block_widgets.get(changed_index)
                if widget is not None:
                    widget.set_block(state.blocks[changed_index])
                    return
            self._reset_stream_window_to_tail(state)
            self._rebuild_stream_window(state)
            return

        if state.visible_end != total - 1:
            self._reset_stream_window_to_tail(state)
            self._rebuild_stream_window(state)
            return

        if state.visible_end - state.visible_start >= MAX_RENDERED_BLOCKS:
            self._drop_first_stream_widget(state)
            state.visible_start += 1
        state.visible_end = total
        self._append_stream_widget(state, total - 1)

    def _apply_active_debug_change(self, state: ConversationLogState, *, line_added: bool) -> None:
        if not line_added:
            return
        if not self._auto_follow:
            state.unread_debug_lines += 1
            return

        total = len(state.debug_lines)
        if not total:
            self._rebuild_debug_window(state)
            return

        if not state.debug_visible_end:
            self._reset_debug_window_to_tail(state)
            self._rebuild_debug_window(state)
            return

        if state.debug_visible_end != total - 1:
            self._reset_debug_window_to_tail(state)
            self._rebuild_debug_window(state)
            return

        if state.debug_visible_end - state.debug_visible_start >= MAX_RENDERED_DEBUG_LINES:
            self._drop_first_debug_widget(state)
            state.debug_visible_start += 1
        state.debug_visible_end = total
        self._append_debug_widget(state, total - 1)

    def _rebuild_stream_window(self, state: ConversationLogState | None) -> None:
        if self._stream_body is None:
            return
        self._window_mutation_guard = True
        try:
            if state is None:
                self._stream_body.remove_children()
                self._mounted_block_widgets.clear()
                self._mounted_stream_conversation_id = None
                self._stream_body.mount(
                    Static(self._empty_message, classes="agent-output-entry agent-output-status", markup=False)
                )
                return
            if not state.blocks:
                self._stream_body.remove_children()
                self._mounted_block_widgets.clear()
                self._mounted_stream_conversation_id = state.conversation_id
                self._stream_body.mount(
                    Static(
                        "Operational activity will appear here…",
                        classes="agent-output-entry agent-output-status",
                        markup=False,
                    )
                )
                return
            self._ensure_stream_window_bounds(state)
            if self._mounted_stream_conversation_id != state.conversation_id:
                self._stream_body.remove_children()
                self._mounted_block_widgets.clear()
                self._mounted_stream_conversation_id = state.conversation_id
            elif self._stream_body.children and not self._mounted_block_widgets:
                self._stream_body.remove_children()

            if not self._mounted_block_widgets:
                for index in range(state.visible_start, state.visible_end):
                    self._append_stream_widget(state, index)
                return

            current_start = min(self._mounted_block_widgets)
            current_end = max(self._mounted_block_widgets) + 1

            while current_start < state.visible_start:
                widget = self._mounted_block_widgets.pop(current_start, None)
                if widget is not None:
                    widget.remove()
                current_start += 1

            while current_end > state.visible_end:
                current_end -= 1
                widget = self._mounted_block_widgets.pop(current_end, None)
                if widget is not None:
                    widget.remove()

            if not self._mounted_block_widgets:
                for index in range(state.visible_start, state.visible_end):
                    self._append_stream_widget(state, index)
                return

            current_start = min(self._mounted_block_widgets)
            current_end = max(self._mounted_block_widgets) + 1

            if current_start > state.visible_start:
                self._prepend_stream_widgets(state, state.visible_start, current_start)
                current_start = state.visible_start

            for index in range(current_end, state.visible_end):
                self._append_stream_widget(state, index)

            for index in range(max(current_start, state.visible_start), min(current_end, state.visible_end)):
                widget = self._mounted_block_widgets.get(index)
                if widget is not None:
                    widget.set_block(state.blocks[index])
        finally:
            self._window_mutation_guard = False

    def _rebuild_debug_window(self, state: ConversationLogState | None) -> None:
        if self._debug_body is None:
            return
        self._window_mutation_guard = True
        try:
            if state is None:
                self._debug_body.remove_children()
                self._mounted_debug_widgets.clear()
                self._mounted_debug_conversation_id = None
                self._debug_body.mount(Static("No event debug output yet.", classes="agent-output-debug-line"))
                return
            if not state.debug_lines:
                self._debug_body.remove_children()
                self._mounted_debug_widgets.clear()
                self._mounted_debug_conversation_id = state.conversation_id
                self._debug_body.mount(Static("Waiting for event debug output…", classes="agent-output-debug-line"))
                return
            self._ensure_debug_window_bounds(state)
            if self._mounted_debug_conversation_id != state.conversation_id:
                self._debug_body.remove_children()
                self._mounted_debug_widgets.clear()
                self._mounted_debug_conversation_id = state.conversation_id
            elif self._debug_body.children and not self._mounted_debug_widgets:
                self._debug_body.remove_children()

            if not self._mounted_debug_widgets:
                for index in range(state.debug_visible_start, state.debug_visible_end):
                    self._append_debug_widget(state, index)
                return

            current_start = min(self._mounted_debug_widgets)
            current_end = max(self._mounted_debug_widgets) + 1

            while current_start < state.debug_visible_start:
                widget = self._mounted_debug_widgets.pop(current_start, None)
                if widget is not None:
                    widget.remove()
                current_start += 1

            while current_end > state.debug_visible_end:
                current_end -= 1
                widget = self._mounted_debug_widgets.pop(current_end, None)
                if widget is not None:
                    widget.remove()

            if not self._mounted_debug_widgets:
                for index in range(state.debug_visible_start, state.debug_visible_end):
                    self._append_debug_widget(state, index)
                return

            current_start = min(self._mounted_debug_widgets)
            current_end = max(self._mounted_debug_widgets) + 1

            if current_start > state.debug_visible_start:
                self._prepend_debug_widgets(state, state.debug_visible_start, current_start)
                current_start = state.debug_visible_start

            for index in range(current_end, state.debug_visible_end):
                self._append_debug_widget(state, index)

            for index in range(
                max(current_start, state.debug_visible_start),
                min(current_end, state.debug_visible_end),
            ):
                widget = self._mounted_debug_widgets.get(index)
                if widget is not None:
                    widget.update(state.debug_lines[index])
        finally:
            self._window_mutation_guard = False

    def _append_stream_widget(self, state: ConversationLogState, index: int) -> None:
        if self._stream_body is None or index in self._mounted_block_widgets:
            return
        widget = LogBlockWidget(state.blocks[index], block_index=index)
        self._mounted_block_widgets[index] = widget
        self._stream_body.mount(widget)

    def _append_debug_widget(self, state: ConversationLogState, index: int) -> None:
        if self._debug_body is None or index in self._mounted_debug_widgets:
            return
        line = state.debug_lines[index]
        widget = Static(line, classes="agent-output-debug-line", markup=False)
        self._mounted_debug_widgets[index] = widget
        self._debug_body.mount(widget)

    def _prepend_stream_widgets(self, state: ConversationLogState, start_index: int, end_index: int) -> None:
        if self._stream_body is None or start_index >= end_index:
            return
        anchor = self._mounted_block_widgets.get(state.visible_start)
        widgets = [
            LogBlockWidget(state.blocks[index], block_index=index)
            for index in range(start_index, end_index)
        ]
        for index, widget in zip(range(start_index, end_index), widgets):
            self._mounted_block_widgets[index] = widget
        if anchor is not None:
            self._stream_body.mount(*widgets, before=anchor)
            scroll = self._stream_scroll
            if scroll is not None:
                scroll.scroll_to_widget(anchor, animate=False, top=True)
            return
        self._stream_body.mount(*widgets)

    def _prepend_debug_widgets(self, state: ConversationLogState, start_index: int, end_index: int) -> None:
        if self._debug_body is None or start_index >= end_index:
            return
        anchor = self._mounted_debug_widgets.get(state.debug_visible_start)
        debug_lines = list(state.debug_lines)
        widgets = [
            Static(debug_lines[index], classes="agent-output-debug-line", markup=False)
            for index in range(start_index, end_index)
        ]
        for index, widget in zip(range(start_index, end_index), widgets):
            self._mounted_debug_widgets[index] = widget
        if anchor is not None:
            self._debug_body.mount(*widgets, before=anchor)
            scroll = self._debug_scroll
            if scroll is not None:
                scroll.scroll_to_widget(anchor, animate=False, top=True)
            return
        self._debug_body.mount(*widgets)

    def _drop_first_stream_widget(self, state: ConversationLogState) -> None:
        widget = self._mounted_block_widgets.pop(state.visible_start, None)
        if widget is not None:
            widget.remove()

    def _drop_last_stream_widget(self, state: ConversationLogState) -> None:
        widget = self._mounted_block_widgets.pop(state.visible_end - 1, None)
        if widget is not None:
            widget.remove()

    def _drop_first_debug_widget(self, state: ConversationLogState) -> None:
        widget = self._mounted_debug_widgets.pop(state.debug_visible_start, None)
        if widget is not None:
            widget.remove()

    def _drop_last_debug_widget(self, state: ConversationLogState) -> None:
        widget = self._mounted_debug_widgets.pop(state.debug_visible_end - 1, None)
        if widget is not None:
            widget.remove()

    def _on_stream_scroll(self, scroll: AgentOutputScroll, _old_value: float, new_value: float) -> None:
        if self._window_mutation_guard or self._auto_follow:
            return
        state = self._active_state()
        if state is None or not state.blocks:
            return
        if new_value <= SCROLL_EDGE_THRESHOLD and state.visible_start > 0:
            self._window_mutation_guard = True
            try:
                new_start = max(0, state.visible_start - WINDOW_CHUNK_SIZE)
                self._prepend_stream_widgets(state, new_start, state.visible_start)
                state.visible_start = new_start
                while state.visible_end - state.visible_start > MAX_RENDERED_BLOCKS:
                    self._drop_last_stream_widget(state)
                    state.visible_end -= 1
            finally:
                self._window_mutation_guard = False
            return

        if scroll.max_scroll_y - new_value <= SCROLL_EDGE_THRESHOLD and state.visible_end < len(state.blocks):
            self._window_mutation_guard = True
            try:
                new_end = min(len(state.blocks), state.visible_end + WINDOW_CHUNK_SIZE)
                for index in range(state.visible_end, new_end):
                    self._append_stream_widget(state, index)
                state.visible_end = new_end
                while state.visible_end - state.visible_start > MAX_RENDERED_BLOCKS:
                    self._drop_first_stream_widget(state)
                    state.visible_start += 1
            finally:
                self._window_mutation_guard = False

    def _on_debug_scroll(self, scroll: AgentOutputScroll, _old_value: float, new_value: float) -> None:
        if self._window_mutation_guard or self._auto_follow:
            return
        state = self._active_state()
        if state is None or not state.debug_lines:
            return
        if new_value <= SCROLL_EDGE_THRESHOLD and state.debug_visible_start > 0:
            self._window_mutation_guard = True
            try:
                new_start = max(0, state.debug_visible_start - WINDOW_CHUNK_SIZE)
                self._prepend_debug_widgets(state, new_start, state.debug_visible_start)
                state.debug_visible_start = new_start
                while state.debug_visible_end - state.debug_visible_start > MAX_RENDERED_DEBUG_LINES:
                    self._drop_last_debug_widget(state)
                    state.debug_visible_end -= 1
            finally:
                self._window_mutation_guard = False
            return

        if scroll.max_scroll_y - new_value <= SCROLL_EDGE_THRESHOLD and state.debug_visible_end < len(state.debug_lines):
            self._window_mutation_guard = True
            try:
                new_end = min(len(state.debug_lines), state.debug_visible_end + WINDOW_CHUNK_SIZE)
                for index in range(state.debug_visible_end, new_end):
                    self._append_debug_widget(state, index)
                state.debug_visible_end = new_end
                while state.debug_visible_end - state.debug_visible_start > MAX_RENDERED_DEBUG_LINES:
                    self._drop_first_debug_widget(state)
                    state.debug_visible_start += 1
            finally:
                self._window_mutation_guard = False

    def _reset_stream_window_to_tail(self, state: ConversationLogState) -> None:
        total = len(state.blocks)
        state.visible_end = total
        state.visible_start = max(0, total - DEFAULT_RENDERED_BLOCKS)

    def _reset_debug_window_to_tail(self, state: ConversationLogState) -> None:
        total = len(state.debug_lines)
        state.debug_visible_end = total
        state.debug_visible_start = max(0, total - DEFAULT_RENDERED_DEBUG_LINES)

    def _ensure_stream_window_bounds(self, state: ConversationLogState) -> None:
        total = len(state.blocks)
        if total == 0:
            state.visible_start = 0
            state.visible_end = 0
            return
        state.visible_end = min(max(state.visible_end, 1), total)
        state.visible_start = min(max(state.visible_start, 0), state.visible_end - 1)
        if state.visible_end - state.visible_start > MAX_RENDERED_BLOCKS:
            state.visible_start = state.visible_end - MAX_RENDERED_BLOCKS

    def _ensure_debug_window_bounds(self, state: ConversationLogState) -> None:
        total = len(state.debug_lines)
        if total == 0:
            state.debug_visible_start = 0
            state.debug_visible_end = 0
            return
        state.debug_visible_end = min(max(state.debug_visible_end, 1), total)
        state.debug_visible_start = min(max(state.debug_visible_start, 0), state.debug_visible_end - 1)
        if state.debug_visible_end - state.debug_visible_start > MAX_RENDERED_DEBUG_LINES:
            state.debug_visible_start = state.debug_visible_end - MAX_RENDERED_DEBUG_LINES

    def _scroll_active_end(self) -> None:
        if not self.is_mounted:
            return
        target = self._debug_scroll if self._debug_view_enabled else self._stream_scroll
        if target is not None:
            target.scroll_end(animate=False)

    def _apply_stream_event_to_blocks(self, state: ConversationLogState, event: AgentStreamEvent) -> int | None:
        event_type = event.type
        if event_type == "conversation.assistant.thinking.delta":
            return self._append_delta_block(
                state,
                kind="reasoning",
                title="Reasoning",
                text=event.text or "",
                event=event,
                collapsed=True,
            )
        if event_type == "conversation.assistant.thinking.completed":
            return self._finalize_text_block(
                state,
                kind="reasoning",
                title="Reasoning",
                text=event.text or "",
                event=event,
                collapsed=True,
                prefer_replacement=True,
            )
        if event_type == "conversation.assistant.message.delta":
            return self._append_delta_block(
                state,
                kind="output",
                title="Agent output",
                text=event.text or "",
                event=event,
                collapsed=False,
            )
        if event_type == "conversation.assistant.message.completed":
            return self._finalize_text_block(
                state,
                kind="output",
                title="Agent output",
                text=event.text or "",
                event=event,
                collapsed=False,
            )
        if event_type == "conversation.tool_call.started":
            return self._start_tool_call_block(state, event)
        if event_type == "conversation.tool_call.delta":
            return self._append_delta_block(
                state,
                kind="mcp_call",
                title=_tool_name_from_payload(event.payload, fallback=event.text or "MCP call"),
                text=event.text or "",
                event=event,
                status="running",
                collapsed=True,
                separate_existing_text=True,
            )
        if event_type == "conversation.tool_call.completed":
            return self._complete_tool_call_block(state, event)
        if event_type == "conversation.progress":
            return self._apply_progress_event(state, event)
        if event_type == "conversation.request.opened":
            return self._append_line_block(
                state,
                kind="request",
                title="User input requested",
                text=_request_body_text(event.payload, fallback=event.text),
                event=event,
                collapsed=True,
            )
        if event_type == "conversation.request.resolved":
            return self._append_line_block(
                state,
                kind="request",
                title="User input resolved",
                text=_request_resolution_text(event.payload, fallback=event.text),
                event=event,
                collapsed=True,
            )
        if event_type == "conversation.turn.started":
            return self._append_line_block(state, kind="status", title="Turn started", text=event.turn_id or "", event=event)
        if event_type == "conversation.turn.completed":
            return self._append_line_block(state, kind="status", title="Turn completed", text=event.turn_id or "", event=event)
        if event_type == "conversation.runtime.error":
            return self._append_line_block(
                state,
                kind="error",
                title="Runtime error",
                text=event.text or _error_text_from_payload(event.payload) or "Runtime error",
                event=event,
            )
        return None

    def _append_delta_block(
        self,
        state: ConversationLogState,
        *,
        kind: BlockKind,
        title: str,
        text: str,
        event: AgentStreamEvent,
        collapsed: bool,
        status: str | None = None,
        separate_existing_text: bool = False,
    ) -> int | None:
        if not text:
            return None
        last = state.blocks[-1] if state.blocks else None
        if last is not None and _same_stream_target(last, kind=kind, event=event):
            last.text = _append_text(last.text, text, separate=separate_existing_text)
            last.timestamp = event.created_at
            last.streaming = True
            last.status = status or last.status
            return len(state.blocks) - 1

        state.blocks.append(
            LogBlock(
                kind=kind,
                title=title,
                text=text,
                timestamp=event.created_at,
                item_id=event.item_id,
                turn_id=event.turn_id,
                agent_id=event.agent_id,
                run_id=event.run_id,
                status=status,
                streaming=True,
                collapsed=collapsed,
            )
        )
        return len(state.blocks) - 1

    def _finalize_text_block(
        self,
        state: ConversationLogState,
        *,
        kind: BlockKind,
        title: str,
        text: str,
        event: AgentStreamEvent,
        collapsed: bool,
        status: str | None = None,
        prefer_replacement: bool = False,
    ) -> int:
        last = state.blocks[-1] if state.blocks else None
        if last is not None and _same_stream_target(last, kind=kind, event=event):
            last.text = text if prefer_replacement and text else _merge_final_text(last.text, text)
            last.timestamp = event.created_at
            last.streaming = False
            last.status = status or last.status
            return len(state.blocks) - 1

        state.blocks.append(
            LogBlock(
                kind=kind,
                title=title,
                text=text,
                timestamp=event.created_at,
                item_id=event.item_id,
                turn_id=event.turn_id,
                agent_id=event.agent_id,
                run_id=event.run_id,
                status=status,
                streaming=False,
                collapsed=collapsed,
            )
        )
        return len(state.blocks) - 1

    def _append_line_block(
        self,
        state: ConversationLogState,
        *,
        kind: BlockKind,
        title: str,
        text: str,
        event: AgentStreamEvent,
        collapsed: bool = False,
    ) -> int:
        state.blocks.append(
            LogBlock(
                kind=kind,
                title=title,
                text=text,
                timestamp=event.created_at,
                item_id=event.item_id,
                turn_id=event.turn_id,
                agent_id=event.agent_id,
                run_id=event.run_id,
                streaming=False,
                collapsed=collapsed,
            )
        )
        return len(state.blocks) - 1

    def _start_tool_call_block(self, state: ConversationLogState, event: AgentStreamEvent) -> int:
        tool_name = _tool_name_from_payload(event.payload, fallback=event.text or "MCP call")
        body = _tool_arguments_text(event.payload)
        last = state.blocks[-1] if state.blocks else None
        if last is not None and _same_stream_target(last, kind="mcp_call", event=event):
            last.title = tool_name
            last.text = body or last.text
            last.timestamp = event.created_at
            last.status = "running"
            last.streaming = True
            return len(state.blocks) - 1

        state.blocks.append(
            LogBlock(
                kind="mcp_call",
                title=tool_name,
                text=body,
                timestamp=event.created_at,
                item_id=event.item_id,
                turn_id=event.turn_id,
                agent_id=event.agent_id,
                run_id=event.run_id,
                status="running",
                streaming=True,
                collapsed=True,
            )
        )
        return len(state.blocks) - 1

    def _complete_tool_call_block(self, state: ConversationLogState, event: AgentStreamEvent) -> int:
        tool_name = _tool_name_from_payload(event.payload, fallback=event.text or "MCP call")
        body = _tool_completion_text(event.payload, fallback=event.text)
        status = "failed" if _payload_has_error(event.payload) else "completed"
        last = state.blocks[-1] if state.blocks else None
        if last is not None and _same_stream_target(last, kind="mcp_call", event=event):
            last.title = tool_name
            last.text = _append_text(last.text, body, separate=bool(last.text and body))
            last.timestamp = event.created_at
            last.status = status
            last.streaming = False
            return len(state.blocks) - 1

        state.blocks.append(
            LogBlock(
                kind="mcp_call",
                title=tool_name,
                text=body,
                timestamp=event.created_at,
                item_id=event.item_id,
                turn_id=event.turn_id,
                agent_id=event.agent_id,
                run_id=event.run_id,
                status=status,
                streaming=False,
                collapsed=True,
            )
        )
        return len(state.blocks) - 1

    def _apply_progress_event(self, state: ConversationLogState, event: AgentStreamEvent) -> int | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        item_type = _normalize_item_type(item.get("type") or payload.get("item_type"))
        if item_type == "reasoning":
            # I don't know why there is progress events between reasoning deltas/completions, they are not useful and just add noise, so ignore them for now. We can always add support later if needed.
            return None 

        if item_type in {"agentmessage", "assistantmessage"}:
            if _progress_is_running(item):
                return self._append_delta_block(
                    state,
                    kind="output",
                    title="Agent output",
                    text=_extract_progress_text(item) or event.text or "",
                    event=event,
                    collapsed=False,
                )
            return self._finalize_text_block(
                state,
                kind="output",
                title="Agent output",
                text=_extract_progress_text(item) or event.text or "",
                event=event,
                collapsed=False,
            )

        if item_type in {"commandexecution", "command_execution"}:
            title = _command_title(item)
            text = _command_body(item, fallback=event.text or "")
            if _progress_is_running(item):
                return self._append_delta_block(
                    state,
                    kind="command",
                    title=title,
                    text=text,
                    event=event,
                    status=_command_status(item),
                    collapsed=True,
                    separate_existing_text=True,
                )
            return self._finalize_text_block(
                state,
                kind="command",
                title=title,
                text=text,
                event=event,
                status=_command_status(item),
                collapsed=True,
            )

        if item_type in {"filechange", "file_change"}:
            return self._append_line_block(
                state,
                kind="progress",
                title="File modified",
                text=_file_change_text(item),
                event=event,
            )
        if item_type in {"fileread", "file_read"}:
            return self._append_line_block(
                state,
                kind="progress",
                title="File read",
                text=_file_read_text(item),
                event=event,
            )

        visible_text = _extract_progress_text(item) or event.text or ""
        if visible_text:
            return self._append_line_block(
                state,
                kind="progress",
                title=_progress_title(item_type),
                text=visible_text,
                event=event,
            )

        if item_type:
            return self._append_line_block(
                state,
                kind="progress",
                title=_progress_title(item_type),
                text="",
                event=event,
            )
        return None

    def _build_stream_text(self, state: ConversationLogState) -> str:
        if not state.blocks:
            return "Operational activity will appear here…"
        return "\n\n".join(_block_plain_text(block) for block in state.blocks)

    def _build_debug_text(self, state: ConversationLogState) -> str:
        return "\n".join(state.debug_lines) if state.debug_lines else "Waiting for event debug output…"

    def _agent_sort_key(self, agent: AgentRecord) -> tuple[float, str]:
        started_at = getattr(agent.lifecycle, "started_at", None)
        run_id = self._run_id(agent) or ""
        if started_at is None:
            return (0.0, run_id)
        return (started_at.timestamp(), run_id)

    def _agent_id(self, agent: AgentRecord) -> str:
        return agent.identity.agent_id

    def _run_id(self, agent: AgentRecord) -> str | None:
        run_id = getattr(agent.identity, "run_id", None)
        return run_id if isinstance(run_id, str) and run_id else None


def _blank_summary(conversation_id: str) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=conversation_id,
        agent_ids=[],
        task_ids=[],
        provider_thread_id=None,
        active_turn_id=None,
        latest_run_id=None,
        updated_at="",
    )


def _merge_summary(current: ConversationSummary, incoming: ConversationSummary) -> ConversationSummary:
    latest_run_id = incoming.latest_run_id or current.latest_run_id
    provider_thread_id = incoming.provider_thread_id or current.provider_thread_id
    active_turn_id = incoming.active_turn_id if incoming.active_turn_id is not None else current.active_turn_id
    updated_at = incoming.updated_at or current.updated_at
    return ConversationSummary(
        conversation_id=incoming.conversation_id,
        agent_ids=list(incoming.agent_ids or current.agent_ids),
        task_ids=list(incoming.task_ids or current.task_ids),
        provider_thread_id=provider_thread_id,
        active_turn_id=active_turn_id,
        latest_run_id=latest_run_id,
        updated_at=updated_at,
    )


def _apply_event_to_summary(summary: ConversationSummary, event: AgentStreamEvent) -> None:
    if event.agent_id and event.agent_id not in summary.agent_ids:
        summary.agent_ids.append(event.agent_id)
    if event.task_id and event.task_id not in summary.task_ids:
        summary.task_ids.append(event.task_id)
    if event.run_id:
        summary.latest_run_id = event.run_id
    if event.type == "conversation.turn.started":
        summary.active_turn_id = event.turn_id
    elif event.type == "conversation.turn.completed" and summary.active_turn_id == event.turn_id:
        summary.active_turn_id = None
    if event.created_at:
        summary.updated_at = event.created_at


def _conversation_state_sort_key(state: ConversationLogState) -> tuple[str, str]:
    return (state.summary.updated_at or "", state.conversation_id)


def _same_stream_target(block: LogBlock, *, kind: BlockKind, event: AgentStreamEvent) -> bool:
    if block.kind != kind or not block.streaming:
        return False
    if block.turn_id != event.turn_id:
        return False
    if block.run_id != event.run_id:
        return False
    if block.item_id and event.item_id:
        return block.item_id == event.item_id
    return block.item_id == event.item_id


def _is_collapsible(block: LogBlock) -> bool:
    if block.kind in {"reasoning", "mcp_call", "command", "request"}:
        return True
    if block.kind == "output":
        return "\n" in block.text or len(block.text) > 200 or block.streaming
    return False


def _block_header_text(block: LogBlock) -> str:
    prefix = _timestamp_prefix(block.timestamp)
    if _is_collapsible(block):
        arrow = "▸" if block.collapsed else "▾"
    else:
        arrow = "•"
    label = block.title
    if block.streaming:
        label = f"{label} (streaming)"
    elif block.status:
        label = f"{label} ({block.status})"
    return f"{arrow} {prefix}{label}".strip()


def _block_plain_text(block: LogBlock) -> str:
    header = block.title
    if block.streaming:
        header = f"{header} (streaming)"
    elif block.status:
        header = f"{header} ({block.status})"
    body = block.text.strip()
    return f"{header}\n{body}".strip()


def _preview_text(text: str, *, max_lines: int = 4, max_chars: int = 280) -> str:
    lines = text.splitlines() or [text]
    preview = "\n".join(lines[:max_lines]).strip()
    if len(preview) > max_chars:
        preview = f"{preview[:max_chars].rstrip()}..."
    if len(lines) > max_lines:
        return f"{preview}\n..."
    return preview


def _append_text(existing: str, addition: str, *, separate: bool) -> str:
    if not addition:
        return existing
    if not existing:
        return addition
    if existing.endswith(addition):
        return existing
    if separate and not existing.endswith(("\n", " ")):
        return f"{existing}\n{addition}"
    return f"{existing}{addition}"


def _merge_final_text(existing: str, final_text: str) -> str:
    if not final_text:
        return existing
    if not existing:
        return final_text
    if existing == final_text or existing.endswith(final_text):
        return existing
    if final_text.endswith(existing) or len(final_text) >= len(existing):
        return final_text
    return f"{existing}{final_text}"


def _timestamp_prefix(timestamp: str | None) -> str:
    if not timestamp:
        return ""
    time_fragment = timestamp
    if "T" in time_fragment:
        time_fragment = time_fragment.split("T", 1)[1]
    if "." in time_fragment:
        time_fragment = time_fragment.split(".", 1)[0]
    if "+" in time_fragment:
        time_fragment = time_fragment.split("+", 1)[0]
    if time_fragment.endswith("Z"):
        time_fragment = time_fragment[:-1]
    return f"[{time_fragment}] " if time_fragment else ""


def _render_stream_debug_line(event: AgentStreamEvent) -> str:
    prefix = _timestamp_prefix(event.created_at)
    payload = {
        key: value
        for key, value in {
            "conversation_id": event.conversation_id,
            "agent_id": event.agent_id,
            "run_id": event.run_id,
            "task_id": event.task_id,
            "turn_id": event.turn_id,
            "item_id": event.item_id,
            "sequence": event.sequence,
            "text": event.text,
            "payload": event.payload,
        }.items()
        if value not in (None, "", {}, [])
    }
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) if payload else ""
    return f"{prefix}{event.type} {compact}".strip()


def _normalize_item_type(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _extract_progress_text(item: JSONValue) -> str:
    if not is_json_mapping(item):
        return ""
    text = item.get("text")
    if isinstance(text, str):
        return text
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [entry.get("text", "") for entry in content if isinstance(entry, dict)]
        return "".join(part for part in parts if part)
    return ""


def _extract_reasoning_text(item: JSONValue, *, fallback: str = "") -> str:
    if not is_json_mapping(item):
        return fallback

    text = _extract_progress_text(item)
    if text:
        return text

    summary = item.get("summary")
    if isinstance(summary, list):
        parts = [entry if isinstance(entry, str) else str(entry) for entry in summary]
        rendered = "\n".join(part for part in parts if part)
        if rendered:
            return rendered
    if isinstance(summary, str) and summary:
        return summary
    return fallback


def _progress_is_running(item: JSONValue) -> bool:
    if not is_json_mapping(item):
        return False
    status = _normalize_item_type(item.get("status"))
    return status in {"", "started", "inprogress", "running"}


def _tool_name_from_payload(payload: object | None, *, fallback: str) -> str:
    if isinstance(payload, dict):
        tool_name = payload.get("tool_name") or payload.get("name")
        if isinstance(tool_name, str) and tool_name.strip():
            return tool_name.strip()
    return fallback.strip() or "MCP call"


def _tool_arguments_text(payload: object | None) -> str:
    if not isinstance(payload, dict):
        return ""
    arguments = payload.get("arguments")
    if isinstance(arguments, str):
        return arguments.strip()
    if arguments not in (None, "", {}):
        return json.dumps(arguments, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    return ""


def _tool_completion_text(payload: object | None, *, fallback: str | None) -> str:
    if not isinstance(payload, dict):
        return fallback or ""
    if _payload_has_error(payload):
        error = payload.get("error")
        return f"Error: {error}" if error not in (None, "", {}) else "Error"
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    if result not in (None, "", {}):
        return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    return fallback or ""


def _payload_has_error(payload: object | None) -> bool:
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return error not in (None, "", {})


def _request_body_text(payload: object | None, *, fallback: str | None) -> str:
    if not isinstance(payload, dict):
        return fallback or ""
    parts: list[str] = []
    for key in ("text", "message", "method"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    params = payload.get("params")
    if params not in (None, "", {}):
        parts.append(json.dumps(params, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    if parts:
        return "\n".join(parts)
    return fallback or ""


def _request_resolution_text(payload: object | None, *, fallback: str | None) -> str:
    if not isinstance(payload, dict):
        return fallback or ""
    parts: list[str] = []
    result = payload.get("result")
    if result not in (None, "", {}):
        if isinstance(result, str):
            parts.append(result.strip())
        else:
            parts.append(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    error_message = payload.get("error_message")
    if isinstance(error_message, str) and error_message.strip():
        parts.append(f"Error: {error_message.strip()}")
    return "\n".join(part for part in parts if part) or (fallback or "")


def _error_text_from_payload(payload: object | None) -> str:
    if not isinstance(payload, dict):
        return ""
    error_message = payload.get("error_message")
    if isinstance(error_message, str) and error_message:
        return error_message
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "")


def _command_title(item: JSONValue) -> str:
    if not is_json_mapping(item):
        return "Command"
    command = item.get("command")
    if isinstance(command, str) and command.strip():
        return f"$ {command.strip()}"
    return "Command"


def _command_body(item: JSONValue, *, fallback: str) -> str:
    if not is_json_mapping(item):
        return fallback
    aggregated_output = item.get("aggregatedOutput")
    if isinstance(aggregated_output, str) and aggregated_output.strip():
        return aggregated_output.rstrip()
    text = _extract_progress_text(item)
    if text:
        return text.rstrip()
    return fallback


def _command_status(item: JSONValue) -> str:
    if not is_json_mapping(item):
        return "unknown"
    exit_code = item.get("exitCode")
    if exit_code == 0:
        return "completed"
    if exit_code is not None:
        return "failed"
    status = _normalize_item_type(item.get("status"))
    if status in {"inprogress", "started", "running", ""}:
        return "running"
    return status or "unknown"


def _file_change_text(item: JSONValue) -> str:
    if not is_json_mapping(item):
        return "Modified a file"
    path = item.get("filename") or item.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return "Modified a file"


def _file_read_text(item: JSONValue) -> str:
    if not is_json_mapping(item):
        return "Read a file"
    path = item.get("filename") or item.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return "Read a file"


def _progress_title(item_type: str) -> str:
    if item_type in {"", "task"}:
        return "Progress"
    if item_type in {"filechange", "file_change"}:
        return "File modified"
    if item_type in {"fileread", "file_read"}:
        return "File read"
    return item_type.replace("_", " ").strip() or "Progress"


__all__ = ["AgentOutput", "MAX_DEBUG_LINES"]
