"""Agent log streams widget for the Vibrant TUI."""

from __future__ import annotations

from collections.abc import Mapping
from collections import deque
from dataclasses import dataclass, field
import json
from typing import Any, Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Collapsible, ContentSwitcher, LoadingIndicator, Static

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
    """Per-agent stream buffers derived from orchestrator records and events."""

    agent_id: str
    task_id: str | None = None
    status: str | None = None
    provider_thread_id: str | None = None
    canonical_lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER_LINES))
    debug_lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER_LINES))
    thought_text: str = ""
    thought_timestamp: str | None = None
    active_thought_item_id: str | None = None


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

    #agent-output-thoughts {
        margin: 0 1 1 1;
    }

    #agent-output-thoughts-status {
        height: auto;
        min-height: 1;
        align: left middle;
        padding: 0 1 1 1;
    }

    #agent-output-thoughts-spinner {
        width: 3;
        margin-right: 1;
    }

    #agent-output-thoughts-label {
        color: $text-muted;
    }

    #agent-output-thoughts-body,
    #agent-output-stream,
    #agent-output-debug {
        width: 100%;
        padding: 0 1;
    }

    #agent-output-thoughts-body {
        padding-bottom: 1;
    }

    #agent-output-stream {
        margin-top: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._streams: dict[str, AgentStreamState] = {}
        self._agent_order: list[str] = []
        self._active_agent_id: str | None = None
        self._auto_follow = True
        self._debug_view_enabled = False
        self._empty_message = "No agent activity yet. Use /run to execute the next roadmap task."

    def compose(self) -> ComposeResult:
        yield Static("[b]Agent Logs[/b]", id="agent-output-header", markup=True)
        yield Static("", id="agent-output-meta")
        yield Static("", id="agent-output-tabs")
        with ContentSwitcher(initial="agent-output-stream-scroll", id="agent-output-switcher"):
            with VerticalScroll(id="agent-output-stream-scroll"):
                with Collapsible(title="💭 Agent thoughts", collapsed=True, id="agent-output-thoughts"):
                    with Horizontal(id="agent-output-thoughts-status"):
                        yield LoadingIndicator(id="agent-output-thoughts-spinner")
                        yield Static("", id="agent-output-thoughts-label")
                    yield Static("", id="agent-output-thoughts-body", markup=False)
                yield Static("", id="agent-output-stream", markup=False)
            with VerticalScroll(id="agent-output-debug-scroll"):
                yield Static("", id="agent-output-debug", markup=False)

    def on_mount(self) -> None:
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

    def sync_agents(
        self,
        agents: Iterable[object],
        *,
        task_ids_by_run: Mapping[str, str] | None = None,
    ) -> None:
        """Refresh known agents from orchestrator-owned runtime records."""

        ordered_agents = sorted(
            [agent for agent in agents if self._agent_id(agent) is not None],
            key=self._agent_sort_key,
        )

        for agent in ordered_agents:
            agent_id = self._agent_id(agent)
            if agent_id is None:
                continue
            stream = self._ensure_stream(agent_id)
            if task_ids_by_run is not None:
                stream.task_id = self._task_id_for_agent(agent, task_ids_by_run)
            stream.status = self._status(agent)
            stream.provider_thread_id = self._provider_thread_id(agent)

        self._agent_order = [agent_id for agent in (self._agent_id(agent) for agent in ordered_agents) if agent_id]
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
        debug_line = _render_debug_event_line(event)
        if debug_line:
            self._append_debug_line(stream, debug_line)

        if event_type == "reasoning.summary.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                stream.thought_text = f"{stream.thought_text}{delta}"
                item_id = event.get("item_id")
                if isinstance(item_id, str) and item_id:
                    stream.active_thought_item_id = item_id
                stream.thought_timestamp = _timestamp_text(event.get("timestamp"))
        elif event_type == "task.progress":
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            item_type = str(item.get("type") or event.get("item_type") or "").strip().lower()
            if item_type == "reasoning":
                final_text = _extract_reasoning_text(item, fallback=stream.thought_text)
                if final_text:
                    stream.thought_text = final_text
                stream.active_thought_item_id = None
                stream.thought_timestamp = _timestamp_text(event.get("timestamp"))
            else:
                for line in _render_task_progress_lines(event):
                    self._append_canonical_line(stream, line)
        elif event_type != "content.delta":
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
        """Switch between operational logs and canonical-event debug output."""

        self._debug_view_enabled = not self._debug_view_enabled
        self._refresh_view(active_agent_changed=True)

    def poll_native_logs_now(self) -> None:
        """Compatibility no-op now that the widget is event-driven only."""

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
        return len(stream.canonical_lines)

    def get_thoughts_text(self, agent_id: str | None = None) -> str:
        """Return the latest visible thought text for tests and diagnostics."""

        target_id = agent_id or self._active_agent_id
        if target_id is None:
            return ""
        stream = self._streams.get(target_id)
        if stream is None:
            return ""
        return stream.thought_text

    def thoughts_running(self, agent_id: str | None = None) -> bool:
        """Return whether the current agent is actively streaming thoughts."""

        target_id = agent_id or self._active_agent_id
        if target_id is None:
            return False
        stream = self._streams.get(target_id)
        if stream is None:
            return False
        return bool(stream.active_thought_item_id and stream.status == AgentStatus.RUNNING.value)

    def _agent_sort_key(self, agent: object) -> tuple[float, str]:
        agent_id = self._agent_id(agent) or ""
        started_at = self._started_at(agent)
        return (started_at.timestamp() if started_at is not None else 0.0, agent_id)

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
        if run_id is None:
            return None
        task_id = task_ids_by_run.get(run_id)
        return task_id if isinstance(task_id, str) and task_id else None

    def _status(self, agent: object) -> str | None:
        lifecycle = getattr(agent, "lifecycle", None)
        if lifecycle is not None:
            status = getattr(lifecycle, "status", None)
            if isinstance(status, str):
                return status
            value = getattr(status, "value", None)
            if isinstance(value, str):
                return value
        runtime = getattr(agent, "runtime", None)
        if runtime is not None:
            status = getattr(runtime, "status", None)
            if isinstance(status, str) and status:
                return status
        return None

    def _provider_thread_id(self, agent: object) -> str | None:
        provider = getattr(agent, "provider", None)
        if provider is None:
            return None
        for field_name in ("provider_thread_id", "thread_id"):
            value = getattr(provider, field_name, None)
            if isinstance(value, str) and value:
                return value
        return None

    def _started_at(self, agent: object):
        lifecycle = getattr(agent, "lifecycle", None)
        if lifecycle is not None:
            started_at = getattr(lifecycle, "started_at", None)
            if started_at is not None:
                return started_at
        runtime = getattr(agent, "runtime", None)
        if runtime is not None:
            return getattr(runtime, "started_at", None)
        return None

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
        mode = "raw" if self._debug_view_enabled else "logs"
        follow = "follow" if self._auto_follow else "locked"
        status = stream.status or "unknown"
        task = stream.task_id or "n/a"
        meta.update(
            f"Active: {stream.agent_id} · Task: {task} · Status: {status} · View: {mode} · {follow} · Tab next · S lock · D debug"
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
        self._update_thoughts()

        stream_body = self.query_one("#agent-output-stream", Static)
        debug_body = self.query_one("#agent-output-debug", Static)

        if self._active_agent_id is None:
            stream_body.update(self._empty_message)
            debug_body.update("No event debug output yet.")
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

    def _update_thoughts(self) -> None:
        thoughts = self.query_one("#agent-output-thoughts", Collapsible)
        spinner = self.query_one("#agent-output-thoughts-spinner", LoadingIndicator)
        label = self.query_one("#agent-output-thoughts-label", Static)
        body = self.query_one("#agent-output-thoughts-body", Static)

        if self._active_agent_id is None:
            thoughts.title = "💭 Agent thoughts"
            spinner.display = False
            label.update("")
            body.update("No agent thoughts yet.")
            return

        stream = self._streams[self._active_agent_id]
        is_running = self.thoughts_running(stream.agent_id)
        spinner.display = is_running
        thoughts.title = "💭 Agent thoughts (thinking…)" if is_running else "💭 Agent thoughts"

        if is_running:
            label.update("Streaming live reasoning")
        elif stream.thought_text:
            label.update("Latest reasoning summary")
        else:
            label.update("No reasoning captured yet")

        body.update(stream.thought_text or "No agent thoughts yet.")

    def _build_canonical_text(self, stream: AgentStreamState) -> str:
        return "\n".join(stream.canonical_lines) if stream.canonical_lines else "Operational activity will appear here…"

    def _build_debug_text(self, stream: AgentStreamState) -> str:
        return "\n".join(stream.debug_lines) if stream.debug_lines else "Waiting for event debug output…"

    def _append_canonical_line(self, stream: AgentStreamState, line: str) -> None:
        stream.canonical_lines.append(line)

    def _append_debug_line(self, stream: AgentStreamState, line: str) -> None:
        stream.debug_lines.append(line)


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


def _render_task_progress_lines(event: dict[str, Any]) -> list[str]:
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


def _error_text(event: dict[str, Any]) -> str:
    if isinstance(event.get("error_message"), str) and event["error_message"]:
        return event["error_message"]
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "")


def _render_debug_event_line(event: dict[str, Any]) -> str:
    prefix = _timestamp_prefix(_timestamp_text(event.get("timestamp")))
    event_type = str(event.get("type") or "event")
    ignored = {"timestamp", "type", "agent_id", "task_id"}
    payload = {key: value for key, value in event.items() if key not in ignored}
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) if payload else ""
    return f"{prefix}{event_type} {compact}".strip()


__all__ = ["AgentOutput", "MAX_BUFFER_LINES"]
