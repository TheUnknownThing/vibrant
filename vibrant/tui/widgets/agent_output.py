"""Agent output streams widget for Panel B of the Vibrant TUI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import ContentSwitcher, Static

from ...models.agent import AgentRecord, AgentStatus

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
    """Per-agent stream buffers and log-tail metadata."""

    agent_id: str
    task_id: str | None = None
    status: str | None = None
    provider_thread_id: str | None = None
    canonical_log_path: Path | None = None
    native_log_path: Path | None = None
    canonical_lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER_LINES))
    debug_lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER_LINES))
    assistant_partial: str = ""
    assistant_partial_timestamp: str | None = None
    canonical_backfilled: bool = False
    native_offset: int = 0
    native_partial: str = ""


class AgentOutput(Static):
    """Live output panel for canonical agent events and raw debug logs."""

    can_focus = True

    BINDINGS = [
        Binding("f5", "cycle_agent", "Next Agent", show=False),
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
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._streams: dict[str, AgentStreamState] = {}
        self._agent_order: list[str] = []
        self._active_agent_id: str | None = None
        self._auto_follow = True
        self._debug_view_enabled = False
        self._empty_message = "No agent activity yet. Press F6 to run the next roadmap task."

    def compose(self) -> ComposeResult:
        yield Static("[b]Agent Output[/b]", id="agent-output-header", markup=True)
        yield Static("", id="agent-output-meta")
        yield Static("", id="agent-output-tabs")
        with ContentSwitcher(initial="agent-output-stream-scroll", id="agent-output-switcher"):
            with VerticalScroll(id="agent-output-stream-scroll"):
                yield Static("", id="agent-output-stream", markup=False)
            with VerticalScroll(id="agent-output-debug-scroll"):
                yield Static("", id="agent-output-debug", markup=False)

    def on_mount(self) -> None:
        self.set_interval(0.25, self._poll_native_logs)
        self._refresh_view()

    def on_click(self) -> None:
        self.focus()

    @property
    def active_agent_id(self) -> str | None:
        """Return the currently selected agent id."""

        return self._active_agent_id

    @property
    def auto_follow_enabled(self) -> bool:
        """Return whether the panel auto-follows the latest output."""

        return self._auto_follow

    @property
    def debug_view_enabled(self) -> bool:
        """Return whether the debug/native view is currently visible."""

        return self._debug_view_enabled

    def sync_agents(self, agent_records: Iterable[AgentRecord]) -> None:
        """Refresh known agents and hydrate log-backed state from disk."""

        ordered_records = sorted(
            list(agent_records),
            key=lambda record: (
                record.started_at.timestamp() if record.started_at is not None else 0.0,
                record.agent_id,
            ),
        )

        for record in ordered_records:
            stream = self._ensure_stream(record.agent_id)
            stream.task_id = record.task_id
            stream.status = record.status.value
            stream.provider_thread_id = record.provider.provider_thread_id

            canonical_path = _path_or_none(record.provider.canonical_event_log)
            if canonical_path != stream.canonical_log_path:
                stream.canonical_log_path = canonical_path
                stream.canonical_backfilled = False
                if canonical_path is not None:
                    stream.canonical_lines.clear()
                    stream.assistant_partial = ""
                    stream.assistant_partial_timestamp = None

            native_path = _path_or_none(record.provider.native_event_log)
            if native_path != stream.native_log_path:
                stream.native_log_path = native_path
                stream.native_offset = 0
                stream.native_partial = ""
                if native_path is not None:
                    stream.debug_lines.clear()

            if not stream.canonical_backfilled and stream.canonical_log_path is not None and stream.canonical_log_path.exists():
                self._backfill_canonical_log(stream)

        self._agent_order = [record.agent_id for record in ordered_records]
        self._active_agent_id = self._resolve_active_agent(self._active_agent_id)
        self._refresh_view()

    def clear_agents(self, message: str | None = None) -> None:
        """Clear the panel when no project lifecycle is available."""

        self._streams.clear()
        self._agent_order.clear()
        self._active_agent_id = None
        if message:
            self._empty_message = message
        self._refresh_view()

    def ingest_canonical_event(self, event: dict[str, Any]) -> None:
        """Append one canonical event to the relevant agent buffer."""

        agent_id = event.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            return

        stream = self._ensure_stream(agent_id)
        task_id = event.get("task_id")
        if isinstance(task_id, str) and task_id:
            stream.task_id = task_id

        event_type = str(event.get("type") or "event")
        if event_type != "content.delta":
            self._flush_assistant_partial(stream)

        if event_type == "content.delta":
            self._consume_assistant_delta(stream, str(event.get("delta") or ""), _timestamp_text(event.get("timestamp")))
        elif event_type == "task.progress":
            progress_text = _extract_progress_text(event.get("item"))
            if progress_text:
                for line in _split_visible_lines(progress_text):
                    self._append_canonical_line(stream, _compose_line(event, f"⋯ {line}"))
            else:
                item_type = event.get("item_type")
                detail = f"task.progress {item_type}".strip()
                self._append_canonical_line(stream, _compose_line(event, detail))
        else:
            for line in _render_canonical_event_lines(event):
                self._append_canonical_line(stream, line)

        if agent_id not in self._agent_order:
            self._agent_order.append(agent_id)
        self._active_agent_id = self._resolve_active_agent(self._active_agent_id)
        self._refresh_view(active_agent_changed=agent_id == self._active_agent_id)

    def action_cycle_agent(self) -> None:
        """Cycle to the next known agent stream."""

        if not self._agent_order:
            return
        if self._active_agent_id not in self._agent_order:
            self._active_agent_id = self._agent_order[0]
        else:
            index = self._agent_order.index(self._active_agent_id)
            self._active_agent_id = self._agent_order[(index + 1) % len(self._agent_order)]
        self._refresh_view(active_agent_changed=True)

    def action_toggle_scroll_lock(self) -> None:
        """Toggle follow mode for the active output view."""

        self._auto_follow = not self._auto_follow
        self._refresh_view(active_agent_changed=True)

    def action_toggle_debug_view(self) -> None:
        """Switch between canonical stream and raw native debug output."""

        self._debug_view_enabled = not self._debug_view_enabled
        self._refresh_view(active_agent_changed=True)

    def poll_native_logs_now(self) -> None:
        """Synchronously poll native logs once.

        Exposed for deterministic tests and manual refresh points that should
        not wait for the periodic interval timer.
        """

        self._poll_native_logs()

    def get_rendered_text(self, agent_id: str | None = None, *, debug: bool | None = None) -> str:
        """Return the rendered text for tests and diagnostics."""

        target_id = agent_id or self._active_agent_id
        if target_id is None:
            return self._empty_message
        stream = self._streams.get(target_id)
        if stream is None:
            return self._empty_message
        use_debug = self._debug_view_enabled if debug is None else debug
        return self._build_debug_text(stream) if use_debug else self._build_canonical_text(stream)

    def get_buffer_line_count(self, agent_id: str, *, debug: bool = False) -> int:
        """Return the current buffer size for one agent."""

        stream = self._streams[agent_id]
        if debug:
            return len(stream.debug_lines)
        return len(stream.canonical_lines) + (1 if stream.assistant_partial else 0)

    def _ensure_stream(self, agent_id: str) -> AgentStreamState:
        stream = self._streams.get(agent_id)
        if stream is None:
            stream = AgentStreamState(agent_id=agent_id)
            self._streams[agent_id] = stream
        return stream

    def _resolve_active_agent(self, current_agent_id: str | None) -> str | None:
        if current_agent_id in self._agent_order:
            return current_agent_id
        if not self._agent_order:
            return None
        running_agents = [
            agent_id
            for agent_id in self._agent_order
            if self._streams.get(agent_id) is not None and self._streams[agent_id].status == AgentStatus.RUNNING.value
        ]
        if running_agents:
            return running_agents[-1]
        return self._agent_order[-1]

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
        if self._active_agent_id is None:
            meta.update(self._empty_message)
            return

        stream = self._streams[self._active_agent_id]
        mode = "debug" if self._debug_view_enabled else "stream"
        follow = "follow" if self._auto_follow else "locked"
        status = stream.status or "unknown"
        task = stream.task_id or "n/a"
        meta.update(
            f"Active: {stream.agent_id} · Task: {task} · Status: {status} · View: {mode} · {follow} · F5 next · S lock · D debug"
        )

    def _update_tabs(self) -> None:
        tabs = self.query_one("#agent-output-tabs", Static)
        if not self._agent_order:
            tabs.update("No agents yet")
            return

        parts: list[str] = []
        for agent_id in self._agent_order:
            stream = self._streams[agent_id]
            prefix = "▶" if agent_id == self._active_agent_id else "•"
            icon = _STATUS_ICONS.get(stream.status or "", "•")
            task_fragment = f"/{stream.task_id}" if stream.task_id else ""
            parts.append(f"{prefix} {icon} {agent_id}{task_fragment}")
        tabs.update("  |  ".join(parts))

    def _update_switcher(self) -> None:
        switcher = self.query_one("#agent-output-switcher", ContentSwitcher)
        switcher.current = "agent-output-debug-scroll" if self._debug_view_enabled else "agent-output-stream-scroll"

    def _update_active_body(self, *, active_agent_changed: bool = False) -> None:
        stream_body = self.query_one("#agent-output-stream", Static)
        debug_body = self.query_one("#agent-output-debug", Static)

        if self._active_agent_id is None:
            stream_body.update(self._empty_message)
            debug_body.update("No native logs yet.")
            return

        stream = self._streams[self._active_agent_id]
        stream_body.update(self._build_canonical_text(stream))
        debug_body.update(self._build_debug_text(stream))

        if self._auto_follow or active_agent_changed:
            scroll = self.query_one(
                "#agent-output-debug-scroll" if self._debug_view_enabled else "#agent-output-stream-scroll",
                VerticalScroll,
            )
            scroll.scroll_end(animate=False)

    def _build_canonical_text(self, stream: AgentStreamState) -> str:
        lines = list(stream.canonical_lines)
        if stream.assistant_partial:
            prefix = _timestamp_prefix(stream.assistant_partial_timestamp)
            lines.append(f"{prefix}🤖 {stream.assistant_partial}" if prefix else f"🤖 {stream.assistant_partial}")
        return "\n".join(lines) if lines else "Waiting for canonical events…"

    def _build_debug_text(self, stream: AgentStreamState) -> str:
        return "\n".join(stream.debug_lines) if stream.debug_lines else "Waiting for native debug output…"

    def _append_canonical_line(self, stream: AgentStreamState, line: str) -> None:
        stream.canonical_lines.append(line)

    def _append_debug_line(self, stream: AgentStreamState, line: str) -> None:
        stream.debug_lines.append(line)

    def _consume_assistant_delta(self, stream: AgentStreamState, delta: str, timestamp: str | None) -> None:
        if not delta:
            return
        if stream.assistant_partial_timestamp is None:
            stream.assistant_partial_timestamp = timestamp

        pending = f"{stream.assistant_partial}{delta}"
        stream.assistant_partial = ""
        for chunk in pending.splitlines(keepends=True):
            if chunk.endswith(("\n", "\r")):
                text = chunk.rstrip("\r\n")
                prefix = _timestamp_prefix(stream.assistant_partial_timestamp or timestamp)
                self._append_canonical_line(stream, f"{prefix}🤖 {text}" if prefix else f"🤖 {text}")
                stream.assistant_partial_timestamp = timestamp
            else:
                stream.assistant_partial = chunk
        if stream.assistant_partial and stream.assistant_partial_timestamp is None:
            stream.assistant_partial_timestamp = timestamp

    def _flush_assistant_partial(self, stream: AgentStreamState) -> None:
        if not stream.assistant_partial:
            stream.assistant_partial_timestamp = None
            return
        prefix = _timestamp_prefix(stream.assistant_partial_timestamp)
        self._append_canonical_line(stream, f"{prefix}🤖 {stream.assistant_partial}" if prefix else f"🤖 {stream.assistant_partial}")
        stream.assistant_partial = ""
        stream.assistant_partial_timestamp = None

    def _backfill_canonical_log(self, stream: AgentStreamState) -> None:
        path = stream.canonical_log_path
        if path is None or not path.exists():
            return
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return

        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                self._append_canonical_line(stream, raw_line)
                continue

            event = {
                "type": payload.get("event", "event"),
                "timestamp": payload.get("timestamp"),
                **(payload.get("data") if isinstance(payload.get("data"), dict) else {}),
                "agent_id": stream.agent_id,
                "task_id": stream.task_id,
            }
            self.ingest_canonical_event(event)
        stream.canonical_backfilled = True

    def _poll_native_logs(self) -> None:
        active_updated = False
        for agent_id in self._agent_order:
            stream = self._streams[agent_id]
            if self._tail_native_log(stream) and agent_id == self._active_agent_id:
                active_updated = True
        if active_updated and self._debug_view_enabled:
            self._refresh_view(active_agent_changed=False)

    def _tail_native_log(self, stream: AgentStreamState) -> bool:
        path = stream.native_log_path
        if path is None or not path.exists():
            return False

        try:
            size = path.stat().st_size
        except OSError:
            return False

        if size < stream.native_offset:
            stream.native_offset = 0
            stream.native_partial = ""
            stream.debug_lines.clear()

        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(stream.native_offset)
                chunk = handle.read()
                stream.native_offset = handle.tell()
        except OSError:
            return False

        if not chunk:
            return False

        pending = f"{stream.native_partial}{chunk}"
        lines = pending.split("\n")
        stream.native_partial = lines.pop()

        updated = False
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            updated = True
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                self._append_debug_line(stream, raw_line)
                continue
            self._append_debug_line(stream, _render_native_entry(payload))
        return updated


def _path_or_none(value: str | None) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value)



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



def _compose_line(event: dict[str, Any], message: str) -> str:
    prefix = _timestamp_prefix(_timestamp_text(event.get("timestamp")))
    return f"{prefix}{message}" if prefix else message



def _render_canonical_event_lines(event: dict[str, Any]) -> list[str]:
    event_type = str(event.get("type") or "event")

    if event_type == "session.started":
        cwd = event.get("cwd")
        return [_compose_line(event, f"session.started cwd={cwd}" if cwd else "session.started")]
    if event_type == "session.state.changed":
        state = event.get("state")
        return [_compose_line(event, f"session.state.changed {state}".strip())]
    if event_type == "thread.started":
        thread = event.get("thread") if isinstance(event.get("thread"), dict) else {}
        thread_id = thread.get("id")
        resumed = " resumed" if event.get("resumed") else ""
        detail = f"thread.started{resumed}"
        if thread_id:
            detail = f"{detail} id={thread_id}"
        return [_compose_line(event, detail)]
    if event_type == "turn.started":
        turn = event.get("turn") if isinstance(event.get("turn"), dict) else {}
        turn_id = turn.get("id")
        return [_compose_line(event, f"turn.started id={turn_id}" if turn_id else "turn.started")]
    if event_type == "turn.completed":
        turn = event.get("turn") if isinstance(event.get("turn"), dict) else {}
        turn_id = turn.get("id")
        return [_compose_line(event, f"turn.completed id={turn_id}" if turn_id else "turn.completed")]
    if event_type == "task.completed":
        return [_compose_line(event, "task.completed")]
    if event_type == "request.opened":
        request_kind = event.get("request_kind")
        method = event.get("method")
        detail = f"request.opened {request_kind or ''} {method or ''}".strip()
        return [_compose_line(event, detail)]
    if event_type == "request.resolved":
        request_id = event.get("request_id")
        detail = f"request.resolved id={request_id}" if request_id is not None else "request.resolved"
        return [_compose_line(event, detail)]
    if event_type == "user-input.requested":
        return [_compose_line(event, "⚠ user-input.requested")]
    if event_type == "runtime.error":
        return [_compose_line(event, f"✗ runtime.error {_error_text(event)}".rstrip())]
    if event_type == "content.delta":
        return []

    payload = {key: value for key, value in event.items() if key not in {"type", "timestamp", "provider", "agent_id", "task_id"}}
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else ""
    message = f"{event_type} {compact}".strip()
    return [_compose_line(event, message)]



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



def _split_visible_lines(text: str) -> list[str]:
    lines = [line.rstrip("\r") for line in text.splitlines()]
    if text and not lines:
        return [text]
    return lines or [text]



def _error_text(event: dict[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "")



def _render_native_entry(payload: dict[str, Any]) -> str:
    prefix = _timestamp_prefix(_timestamp_text(payload.get("timestamp")))
    event_name = payload.get("event") or "event"
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if event_name == "stderr.line":
        return f"{prefix}stderr {data.get('line', '')}".rstrip()
    compact = json.dumps(data, ensure_ascii=False, sort_keys=True) if data else ""
    return f"{prefix}{event_name} {compact}".strip()


__all__ = ["AgentOutput", "MAX_BUFFER_LINES"]
