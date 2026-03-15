"""Agent log streams widget for the Vibrant TUI."""

from __future__ import annotations

from collections.abc import Mapping
from collections import deque
from dataclasses import dataclass, field
import json
from typing import Any, Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible, ContentSwitcher, Static

from vibrant.providers.base import CanonicalEvent, TaskProgressEvent

from ...models.agent import AgentStatus

MAX_BUFFER_LINES = 10_000

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
class AgentStreamState:
    """Per-run stream buffers derived from orchestrator records and events."""

    run_id: str
    status: str | None = None
    provider_thread_id: str | None = None
    entries: deque["AgentStreamEntry"] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER_LINES))
    debug_lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER_LINES))
    thought_text: str = ""
    thought_timestamp: str | None = None
    active_thought_item_id: str | None = None


@dataclass(slots=True)
class AgentStreamEntry:
    """One rendered entry in the ordered agent-output stream."""

    kind: str
    text: str
    timestamp: str | None = None
    item_id: str | None = None
    running: bool = False


class AgentOutput(Static):
    """Live output panel for operational agent logs and raw debug logs."""

    can_focus = True

    BINDINGS = [
        Binding("tab", "cycle_agent", "Next Agent", show=False),
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
    }

    #agent-output-stream {
        margin-top: 1;
        height: auto;
    }

    #agent-output-stream > .agent-output-entry {
        margin-bottom: 1;
    }

    #agent-output-stream > .agent-output-entry:last-child {
        margin-bottom: 0;
    }

    .agent-output-log {
        padding: 0 1;
    }

    .agent-output-reasoning {
        margin: 0;
    }

    .agent-output-reasoning-body {
        padding: 0 1 1 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._streams: dict[str, AgentStreamState] = {}
        self._agent_ids_by_run: dict[str, str] = {}
        self._task_ids_by_run: dict[str, str] = {}
        self._run_order: list[str] = []
        self._active_run_id: str | None = None
        self._auto_follow = True
        self._debug_view_enabled = False
        self._empty_message = "No agent activity yet. Use /run to execute the next roadmap task."

    def compose(self) -> ComposeResult:
        yield Static("[b]Agent Logs[/b]", id="agent-output-header", markup=True)
        yield Static("", id="agent-output-meta")
        yield Static("", id="agent-output-tabs")
        with ContentSwitcher(initial="agent-output-stream-scroll", id="agent-output-switcher"):
            with VerticalScroll(id="agent-output-stream-scroll"):
                yield Vertical(id="agent-output-stream")
            with VerticalScroll(id="agent-output-debug-scroll"):
                yield Static("", id="agent-output-debug", markup=False)

    def on_mount(self) -> None:
        self._refresh_view()

    def on_click(self) -> None:
        self.focus()

    @property
    def active_agent_id(self) -> str | None:
        """Return the stable agent id for the currently selected run."""

        if self._active_run_id is None:
            return None
        return self._agent_ids_by_run.get(self._active_run_id)

    @property
    def active_run_id(self) -> str | None:
        """Return the currently selected run id."""

        return self._active_run_id

    @property
    def auto_follow_enabled(self) -> bool:
        """Return whether the panel auto-follows the latest output."""

        return self._auto_follow

    @property
    def debug_view_enabled(self) -> bool:
        """Return whether the debug/native view is currently visible."""

        return self._debug_view_enabled

    def sync_agents(
        self,
        agents: Iterable[object],
        *,
        task_ids_by_run: Mapping[str, str] | None = None,
    ) -> None:
        """Refresh known agents from orchestrator-owned runtime records."""

        ordered_agents = sorted(
            [agent for agent in agents if self._run_id(agent) is not None],
            key=self._run_sort_key,
        )

        for agent in ordered_agents:
            run_id = self._run_id(agent)
            agent_id = self._agent_id(agent)
            if run_id is None or agent_id is None:
                continue
            stream = self._ensure_stream(run_id)
            self._agent_ids_by_run[run_id] = agent_id
            if task_ids_by_run is not None:
                task_id = self._task_id_for_agent(agent, task_ids_by_run)
                if task_id is not None:
                    self._task_ids_by_run[run_id] = task_id
            stream.status = self._status(agent)
            stream.provider_thread_id = self._provider_thread_id(agent)
            if stream.status != AgentStatus.RUNNING.value:
                self._close_active_reasoning(stream)

        self._run_order = [run_id for run_id in (self._run_id(agent) for agent in ordered_agents) if run_id]
        self._active_run_id = self._resolve_active_run(self._active_run_id)
        self._refresh_view()

    def clear_agents(self, message: str | None = None) -> None:
        """Clear the panel when no project lifecycle is available."""

        self._streams.clear()
        self._agent_ids_by_run.clear()
        self._task_ids_by_run.clear()
        self._run_order.clear()
        self._active_run_id = None
        if message:
            self._empty_message = message
        self._refresh_view()

    def ingest_canonical_event(self, event: CanonicalEvent) -> None:
        """Append one canonical event to the relevant agent buffer."""

        run_id = event.get("run_id")
        agent_id = event.get("agent_id")
        if not isinstance(run_id, str) or not run_id:
            return

        stream = self._ensure_stream(run_id)
        if isinstance(agent_id, str) and agent_id:
            self._agent_ids_by_run[run_id] = agent_id
        task_id = event.get("task_id")
        if isinstance(task_id, str) and task_id:
            self._task_ids_by_run[run_id] = task_id

        event_type = str(event.get("type") or "event")
        debug_line = _render_debug_event_line(event)
        if debug_line:
            self._append_debug_line(stream, debug_line)

        if event_type == "reasoning.summary.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                item_id = event.get("item_id")
                self._append_reasoning_delta(
                    stream,
                    delta=delta,
                    item_id=item_id if isinstance(item_id, str) and item_id else None,
                    timestamp=_timestamp_text(event.get("timestamp")),
                )
        elif event_type == "task.progress":
            item = event.get("item")
            assert isinstance(item, dict)
            item = item if isinstance(event.get("item"), dict) else {}
            item_type = str(item.get("type") or event.get("item_type") or "").strip().lower()
            if item_type == "reasoning":
                final_text = _extract_reasoning_text(item, fallback=stream.thought_text)
                item_id = item.get("id")
                self._finalize_reasoning(
                    stream,
                    item_id=item_id if isinstance(item_id, str) and item_id else None,
                    text=final_text,
                    timestamp=_timestamp_text(event.get("timestamp")),
                )
            else:
                for line in _render_task_progress_lines(event):
                    self._append_canonical_line(stream, line)
        elif event_type != "content.delta":
            for line in _render_canonical_event_lines(event):
                self._append_canonical_line(stream, line)

        if run_id not in self._run_order:
            self._run_order.append(run_id)
        self._active_run_id = self._resolve_active_run(self._active_run_id)
        self._refresh_view(active_agent_changed=run_id == self._active_run_id)

    def action_cycle_agent(self) -> None:
        """Cycle to the next known agent stream."""

        if not self._run_order:
            return
        if self._active_run_id not in self._run_order:
            self._active_run_id = self._run_order[0]
        else:
            index = self._run_order.index(self._active_run_id)
            self._active_run_id = self._run_order[(index + 1) % len(self._run_order)]
        self._refresh_view(active_agent_changed=True)

    def action_toggle_scroll_lock(self) -> None:
        """Toggle follow mode for the active output view."""

        self._auto_follow = not self._auto_follow
        self._refresh_view(active_agent_changed=True)

    def action_toggle_debug_view(self) -> None:
        """Switch between operational logs and canonical-event debug output."""

        self._debug_view_enabled = not self._debug_view_enabled
        self._refresh_view(active_agent_changed=True)

    def poll_native_logs_now(self) -> None:
        """Compatibility no-op now that the widget is event-driven only."""

    def get_rendered_text(self, run_id: str | None = None, *, debug: bool | None = None) -> str:
        """Return the rendered text for tests and diagnostics."""

        target_run_id = run_id if run_id is not None else self._active_run_id
        if target_run_id is None:
            return self._empty_message
        stream = self._streams.get(target_run_id)
        if stream is None:
            return self._empty_message
        use_debug = self._debug_view_enabled if debug is None else debug
        return self._build_debug_text(stream) if use_debug else self._build_canonical_text(stream)

    def get_buffer_line_count(self, run_id: str, *, debug: bool = False) -> int:
        """Return the current buffer size for one run."""

        stream = self._streams[run_id]
        if debug:
            return len(stream.debug_lines)
        return len(stream.entries)

    def get_thoughts_text(self, run_id: str | None = None) -> str:
        """Return the latest visible thought text for tests and diagnostics."""

        target_run_id = run_id if run_id is not None else self._active_run_id
        if target_run_id is None:
            return ""
        stream = self._streams.get(target_run_id)
        if stream is None:
            return ""
        return stream.thought_text

    def thoughts_running(self, run_id: str | None = None) -> bool:
        """Return whether the current run is actively streaming thoughts."""

        target_run_id = run_id if run_id is not None else self._active_run_id
        if target_run_id is None:
            return False
        stream = self._streams.get(target_run_id)
        if stream is None:
            return False
        return bool(stream.active_thought_item_id and stream.status == AgentStatus.RUNNING.value)

    def _run_sort_key(self, agent: object) -> tuple[float, str]:
        run_id = self._run_id(agent) or ""
        started_at = self._started_at(agent)
        return (started_at.timestamp() if started_at is not None else 0.0, run_id)

    def _agent_id(self, agent: object) -> str | None:
        identity = getattr(agent, "identity", None)
        agent_id = getattr(identity, "agent_id", None)
        return agent_id if isinstance(agent_id, str) and agent_id else None

    def _run_id(self, agent: object) -> str | None:
        identity = getattr(agent, "identity", None)
        run_id = getattr(identity, "run_id", None)
        return run_id if isinstance(run_id, str) and run_id else None

    def _task_id_for_agent(self, agent: object, task_ids_by_run: Mapping[str, str]) -> str | None:
        run_id = self._run_id(agent)
        if run_id is not None:
            task_id = task_ids_by_run.get(run_id)
            if isinstance(task_id, str) and task_id:
                return task_id
        identity = getattr(agent, "identity", None)
        task_id = getattr(identity, "task_id", None)
        return task_id if isinstance(task_id, str) and task_id else None

    def _status(self, agent: object) -> str:
        lifecycle = getattr(agent, "lifecycle", None)
        runtime = getattr(agent, "runtime", None)
        status = getattr(lifecycle, "status", None)
        if status is None:
            status = getattr(runtime, "status", None)
        value = getattr(status, "value", status)
        return str(value)

    def _provider_thread_id(self, agent: object) -> str | None:
        provider = getattr(agent, "provider", None)
        provider_thread_id = getattr(provider, "provider_thread_id", None)
        if isinstance(provider_thread_id, str) and provider_thread_id:
            return provider_thread_id
        thread_id = getattr(provider, "thread_id", None)
        return thread_id if isinstance(thread_id, str) and thread_id else None

    def _started_at(self, agent: object):
        lifecycle = getattr(agent, "lifecycle", None)
        runtime = getattr(agent, "runtime", None)
        started_at = getattr(lifecycle, "started_at", None)
        return started_at if started_at is not None else getattr(runtime, "started_at", None)

    def _ensure_stream(self, run_id: str) -> AgentStreamState:
        stream = self._streams.get(run_id)
        if stream is None:
            stream = AgentStreamState(run_id=run_id)
            self._streams[run_id] = stream
        return stream

    def _resolve_active_run(self, current_run_id: str | None) -> str | None:
        if current_run_id in self._run_order:
            return current_run_id
        if not self._run_order:
            return None
        running_agents = [
            run_id
            for run_id in self._run_order
            if self._streams.get(run_id) is not None and self._streams[run_id].status == AgentStatus.RUNNING.value
        ]
        if running_agents:
            return running_agents[-1]
        return self._run_order[-1]

    def _refresh_view(self, *, active_agent_changed: bool = False) -> None:
        try:
            self._update_meta()
            self._update_tabs()
            self._update_switcher()
            self._update_active_body(active_agent_changed=active_agent_changed)
        except Exception:
            return

    def _update_meta(self) -> None:
        meta = self.query_one("#agent-output-meta", Static)
        if self._active_run_id is None:
            meta.update(self._empty_message)
            return

        stream = self._streams[self._active_run_id]
        mode = "raw" if self._debug_view_enabled else "logs"
        follow = "follow" if self._auto_follow else "locked"
        status = stream.status or "unknown"
        agent_id = self._agent_ids_by_run.get(stream.run_id, stream.run_id)
        task = self._task_ids_by_run.get(stream.run_id, "n/a")
        meta.update(
            f"Active: {agent_id} ({stream.run_id}) · Task: {task} · Status: {status} · View: {mode} · {follow} · Tab next · S lock · D debug"
        )

    def _update_tabs(self) -> None:
        tabs = self.query_one("#agent-output-tabs", Static)
        if not self._run_order:
            tabs.update("No agents yet")
            return

        parts: list[str] = []
        for run_id in self._run_order:
            stream = self._streams[run_id]
            prefix = "▶" if run_id == self._active_run_id else "•"
            icon = _STATUS_ICONS.get(stream.status or "", "•")
            agent_id = self._agent_ids_by_run.get(run_id, run_id)
            task_id = self._task_ids_by_run.get(run_id)
            task_fragment = f"/{task_id}" if task_id else ""
            parts.append(f"{prefix} {icon} {agent_id}@{run_id}{task_fragment}")
        tabs.update("  |  ".join(parts))

    def _update_switcher(self) -> None:
        switcher = self.query_one("#agent-output-switcher", ContentSwitcher)
        switcher.current = "agent-output-debug-scroll" if self._debug_view_enabled else "agent-output-stream-scroll"

    def _update_active_body(self, *, active_agent_changed: bool = False) -> None:
        stream_body = self.query_one("#agent-output-stream", Vertical)
        debug_body = self.query_one("#agent-output-debug", Static)

        if self._active_run_id is None:
            self._rebuild_stream(stream_body, None)
            debug_body.update("No event debug output yet.")
            return

        stream = self._streams[self._active_run_id]
        self._rebuild_stream(stream_body, stream)
        debug_body.update(self._build_debug_text(stream))

        if self._auto_follow or active_agent_changed:
            scroll = self.query_one(
                "#agent-output-debug-scroll" if self._debug_view_enabled else "#agent-output-stream-scroll",
                VerticalScroll,
            )
            scroll.scroll_end(animate=False)

    def _rebuild_stream(self, container: Vertical, stream: AgentStreamState | None) -> None:
        container.remove_children()
        if stream is None:
            container.mount(Static(self._empty_message, classes="agent-output-entry agent-output-log", markup=False))
            return
        if not stream.entries:
            container.mount(
                Static(
                    "Operational activity will appear here…",
                    classes="agent-output-entry agent-output-log",
                    markup=False,
                )
            )
            return
        for entry in stream.entries:
            if entry.kind == "reasoning":
                container.mount(_build_reasoning_widget(entry))
            else:
                container.mount(Static(entry.text, classes="agent-output-entry agent-output-log", markup=False))

    def _build_canonical_text(self, stream: AgentStreamState) -> str:
        if not stream.entries:
            return "Operational activity will appear here…"
        return "\n\n".join(_entry_plain_text(entry) for entry in stream.entries)

    def _build_debug_text(self, stream: AgentStreamState) -> str:
        return "\n".join(stream.debug_lines) if stream.debug_lines else "Waiting for event debug output…"

    def _append_canonical_line(self, stream: AgentStreamState, line: str) -> None:
        self._close_active_reasoning(stream)
        stream.entries.append(AgentStreamEntry(kind="log", text=line))

    def _append_debug_line(self, stream: AgentStreamState, line: str) -> None:
        stream.debug_lines.append(line)

    def _append_reasoning_delta(self, stream: AgentStreamState, *, delta: str, item_id: str | None, timestamp: str | None) -> None:
        last_entry = stream.entries[-1] if stream.entries else None
        if (
            last_entry is not None
            and last_entry.kind == "reasoning"
            and last_entry.running
            and last_entry.item_id == item_id
        ):
            last_entry.text = f"{last_entry.text}{delta}"
            last_entry.timestamp = timestamp or last_entry.timestamp
            stream.thought_text = last_entry.text
            stream.thought_timestamp = last_entry.timestamp
            stream.active_thought_item_id = item_id
            return

        entry = AgentStreamEntry(
            kind="reasoning",
            text=delta,
            timestamp=timestamp,
            item_id=item_id,
            running=True,
        )
        stream.entries.append(entry)
        stream.thought_text = entry.text
        stream.thought_timestamp = timestamp
        stream.active_thought_item_id = item_id

    def _finalize_reasoning(
        self,
        stream: AgentStreamState,
        *,
        item_id: str | None,
        text: str,
        timestamp: str | None,
    ) -> None:
        target_entry: AgentStreamEntry | None = None
        last_entry = stream.entries[-1] if stream.entries else None
        if (
            last_entry is not None
            and last_entry.kind == "reasoning"
            and (item_id is None or last_entry.item_id in {None, item_id})
        ):
            target_entry = last_entry

        if target_entry is None:
            target_entry = AgentStreamEntry(
                kind="reasoning",
                text=text,
                timestamp=timestamp,
                item_id=item_id,
                running=False,
            )
            stream.entries.append(target_entry)
        else:
            if text:
                target_entry.text = text
            target_entry.timestamp = timestamp or target_entry.timestamp
            target_entry.running = False

        stream.thought_text = target_entry.text
        stream.thought_timestamp = target_entry.timestamp
        stream.active_thought_item_id = None

    def _close_active_reasoning(self, stream: AgentStreamState) -> None:
        last_entry = stream.entries[-1] if stream.entries else None
        if last_entry is None or last_entry.kind != "reasoning" or not last_entry.running:
            return
        last_entry.running = False
        stream.active_thought_item_id = None


def _build_reasoning_widget(entry: AgentStreamEntry) -> Collapsible:
    title = "💭 Agent thoughts (thinking…)" if entry.running else "💭 Agent thoughts"
    body_text = entry.text or "Thinking..."
    return Collapsible(
        Static(body_text, classes="agent-output-reasoning-body", markup=False),
        title=title,
        collapsed=True,
        classes="agent-output-entry agent-output-reasoning",
    )


def _entry_plain_text(entry: AgentStreamEntry) -> str:
    if entry.kind != "reasoning":
        return entry.text
    label = "Reasoning..." if entry.running else "Reasoning"
    return f"{label}\n{entry.text}".strip()


def _timestamp_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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


def _compose_line(event: CanonicalEvent, message: str) -> str:
    prefix = _timestamp_prefix(_timestamp_text(event.get("timestamp")))
    return f"{prefix}{message}" if prefix else message


def _render_canonical_event_lines(event: CanonicalEvent) -> list[str]:
    event_type = str(event.get("type") or "event")

    if event_type == "task.completed":
        return [_compose_line(event, "✓ Task completed")]
    if event_type == "tool.call.started":
        tool_name = str(event.get("tool_name") or "tool").strip() or "tool"
        return [_compose_line(event, f"🛠 {tool_name} started")]
    if event_type == "tool.call.completed":
        tool_name = str(event.get("tool_name") or "tool").strip() or "tool"
        if event.get("error") is not None:
            return [_compose_line(event, f"✗ {tool_name} failed")]
        return [_compose_line(event, f"✅ {tool_name} completed")]
    if event_type == "user-input.requested":
        return [_compose_line(event, "⚠ User input requested")]
    if event_type == "runtime.error":
        return [_compose_line(event, f"✗ {_error_text(event)}".rstrip())]
    return []


def _render_task_progress_lines(event: TaskProgressEvent) -> list[str]:
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    item_type = str(item.get("type") or event.get("item_type") or "").strip().lower()

    if item_type in {"", "usermessage", "agentmessage", "reasoning"}:
        progress_text = _extract_progress_text(item)
        if item_type in {"agentmessage", "reasoning", "usermessage"}:
            return []
        if progress_text:
            return [_compose_line(event, f"⋯ {line}") for line in _split_visible_lines(progress_text)]
        return []

    if item_type in {"commandexecution", "command_execution"}:
        command = item.get("command")
        exit_code = item.get("exitCode")
        duration_ms = item.get("durationMs")
        status_icon = "✅" if exit_code == 0 else "❌" if exit_code is not None else "⏳"
        detail = f"$ {command}" if isinstance(command, str) and command else "command"
        if isinstance(duration_ms, int):
            detail = f"{detail} ({duration_ms}ms)"
        return [_compose_line(event, f"{status_icon} {detail}")]

    if item_type in {"filechange", "file_change"}:
        path = item.get("filename") or item.get("path")
        return [_compose_line(event, f"✏ Modified {path}" if path else "✏ File modified")]

    if item_type in {"fileread", "file_read"}:
        path = item.get("filename") or item.get("path")
        return [_compose_line(event, f"📖 Read {path}" if path else "📖 File read")]

    progress_text = _extract_progress_text(item)
    if progress_text:
        return [_compose_line(event, f"⋯ {line}") for line in _split_visible_lines(progress_text)]

    return [_compose_line(event, f"⋯ {item_type.replace('_', ' ')}")]


def _extract_progress_text(item: Any) -> str:
    if not isinstance(item, dict):
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


def _extract_reasoning_text(item: Any, *, fallback: str = "") -> str:
    if not isinstance(item, dict):
        return fallback

    text = _extract_progress_text(item)
    if text:
        return text

    summary = item.get("summary")
    if isinstance(summary, list):
        parts = [entry if isinstance(entry, str) else str(entry) for entry in summary]
        summary_text = "\n".join(part for part in parts if part)
        if summary_text:
            return summary_text
    if isinstance(summary, str) and summary:
        return summary

    return fallback


def _split_visible_lines(text: str) -> list[str]:
    lines = [line.rstrip("\r") for line in text.splitlines()]
    if text and not lines:
        return [text]
    return lines or [text]


def _error_text(event: CanonicalEvent) -> str:
    error_message = event.get("error_message")
    if isinstance(error_message, str) and error_message != "":
        return error_message
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "")


def _render_debug_event_line(event: CanonicalEvent) -> str:
    prefix = _timestamp_prefix(_timestamp_text(event.get("timestamp")))
    event_type = str(event.get("type") or "event")
    ignored = {"timestamp", "type", "agent_id", "task_id"}
    payload = {key: value for key, value in event.items() if key not in ignored}
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) if payload else ""
    return f"{prefix}{event_type} {compact}".strip()


__all__ = ["AgentOutput", "MAX_BUFFER_LINES"]
