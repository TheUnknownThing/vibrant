"""Conversation view widget backed by orchestrator conversation streams."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Markdown, Static

from ...orchestrator.types import AgentConversationEntry, AgentConversationView, AgentStreamEvent


class EntryBubble(Static):
    """A single conversation entry."""

    def __init__(self, entry: AgentConversationEntry, **kwargs) -> None:
        super().__init__(**kwargs)
        self.entry = entry

    def compose(self) -> ComposeResult:
        self.add_class(f"{self.entry.role}-msg")
        yield Static(f"[b]{_role_label(self.entry.role)}[/b]", classes="msg-role", markup=True)
        yield from _render_entry(self.entry)


class ConversationView(Static):
    """Scrollable view of one orchestrator-managed conversation."""

    DEFAULT_CSS = """
    ConversationView #empty-state {
        height: 100%;
        content-align: center middle;
        text-align: center;
        padding: 4;
    }

    ConversationView #conversation-scroll {
        height: 1fr;
        padding: 0 1;
    }

    ConversationView EntryBubble {
        margin: 1 0;
        padding: 0 1;
    }

    ConversationView .user-msg {
        background: $primary 15%;
        border-left: tall $primary;
    }

    ConversationView .assistant-msg {
        background: $secondary 10%;
        border-left: tall $secondary;
    }

    ConversationView .tool-msg,
    ConversationView .system-msg {
        background: $surface-lighten-1;
        border-left: tall $panel;
    }

    ConversationView .msg-role {
        margin-bottom: 0;
    }

    ConversationView .msg-content {
        margin-top: 0;
    }

    ConversationView .msg-status,
    ConversationView .msg-error,
    ConversationView .msg-tool {
        padding: 0 1;
        margin: 0;
    }

    ConversationView .msg-error {
        color: $error;
    }

    ConversationView .command-collapsible,
    ConversationView .reasoning-collapsible {
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._scroll: VerticalScroll | None = None
        self._conversation: AgentConversationView | None = None
        self._empty_text = "[dim]No Gatekeeper messages yet. Start planning below.[/dim]"

    def compose(self) -> ComposeResult:
        yield Static(self._empty_text, id="empty-state", markup=True)
        self._scroll = VerticalScroll(id="conversation-scroll")
        self._scroll.display = False
        yield self._scroll

    @property
    def current_conversation_id(self) -> str | None:
        """Return the active orchestrator conversation id."""

        if self._conversation is None:
            return None
        return self._conversation.conversation_id

    @property
    def entry_count(self) -> int:
        """Return the number of rendered conversation entries."""

        if self._conversation is None:
            return 0
        return len(self._conversation.entries)

    def show_conversation(self, conversation: AgentConversationView | None) -> None:
        """Display the supplied conversation view."""

        self._conversation = _clone_conversation(conversation)
        self._render_once()

    def ingest_stream_event(self, event: AgentStreamEvent) -> None:
        """Incrementally apply one stream frame to the local view model."""

        if self._conversation is None or self._conversation.conversation_id != event.conversation_id:
            self._conversation = AgentConversationView(
                conversation_id=event.conversation_id,
                agent_ids=[],
                task_ids=[],
                active_turn_id=None,
                entries=[],
                updated_at=event.created_at,
            )
        _apply_stream_event(self._conversation, event)
        self._render_once()

    def clear(self) -> None:
        """Clear the rendered conversation."""

        self._conversation = None
        self._render_once()

    def _render_once(self) -> None:
        if not self.is_mounted:
            return

        empty_state = self.query_one("#empty-state", Static)
        if self._scroll is None:
            return

        if self._conversation is None or not self._conversation.entries:
            empty_state.update(self._empty_text)
            empty_state.display = True
            self._scroll.display = False
            self._scroll.remove_children()
            return

        empty_state.display = False
        self._scroll.display = True
        self._scroll.remove_children()
        for entry in self._conversation.entries:
            if entry.text or entry.kind in {"status", "error", "tool_call"}:
                self._scroll.mount(EntryBubble(entry))
        self._scroll.scroll_end(animate=False)


def _render_entry(entry: AgentConversationEntry) -> list[Static | Markdown | Collapsible]:
    widgets: list[Static | Markdown | Collapsible] = []

    if entry.kind == "thinking":
        body = entry.text.strip() or "Thinking..."
        widgets.append(
            Collapsible(
                Static(body, markup=False),
                title="Thinking",
                collapsed=True,
                classes="reasoning-collapsible",
            )
        )
        return widgets

    if entry.kind == "tool_call":
        title = _tool_title(entry)
        body = _tool_body(entry)
        if body:
            widgets.append(
                Collapsible(
                    Static(body, markup=False, classes="msg-tool"),
                    title=title,
                    collapsed=True,
                    classes="command-collapsible",
                )
            )
        else:
            widgets.append(Static(title, classes="msg-tool", markup=False))
        return widgets

    if entry.kind == "status":
        widgets.append(Static(entry.text or "Status updated", classes="msg-status", markup=False))
        return widgets

    if entry.kind == "error":
        widgets.append(Static(entry.text or "Runtime error", classes="msg-error", markup=False))
        return widgets

    if entry.role == "assistant":
        widgets.append(Markdown(entry.text, classes="msg-content"))
    else:
        widgets.append(Static(entry.text, classes="msg-content", markup=False))
    return widgets


def _role_label(role: str) -> str:
    return {
        "user": "You",
        "assistant": "Gatekeeper",
        "tool": "Tool",
        "system": "System",
    }.get(role, "Message")


def _tool_title(entry: AgentConversationEntry) -> str:
    payload = entry.payload or {}
    name = payload.get("tool_name") or payload.get("name") or entry.text or "tool"
    name_text = str(name).strip() or "tool"
    return f"$ {name_text}"


def _tool_body(entry: AgentConversationEntry) -> str:
    payload = entry.payload or {}
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    text = entry.text.strip()
    title = _tool_title(entry).removeprefix("$ ").strip()
    if text and text != title:
        return text
    return ""


def _clone_conversation(conversation: AgentConversationView | None) -> AgentConversationView | None:
    if conversation is None:
        return None
    return AgentConversationView(
        conversation_id=conversation.conversation_id,
        agent_ids=list(conversation.agent_ids),
        task_ids=list(conversation.task_ids),
        active_turn_id=conversation.active_turn_id,
        entries=[
            AgentConversationEntry(
                role=entry.role,
                kind=entry.kind,
                turn_id=entry.turn_id,
                text=entry.text,
                payload=entry.payload,
                started_at=entry.started_at,
                finished_at=entry.finished_at,
            )
            for entry in conversation.entries
        ],
        updated_at=conversation.updated_at,
    )


def _apply_stream_event(conversation: AgentConversationView, event: AgentStreamEvent) -> None:
    if event.agent_id and event.agent_id not in conversation.agent_ids:
        conversation.agent_ids.append(event.agent_id)
    if event.task_id and event.task_id not in conversation.task_ids:
        conversation.task_ids.append(event.task_id)
    if event.type == "conversation.turn.started":
        conversation.active_turn_id = event.turn_id
    elif event.type == "conversation.turn.completed" and conversation.active_turn_id == event.turn_id:
        conversation.active_turn_id = None
    conversation.updated_at = event.created_at

    entries = conversation.entries
    if event.type == "conversation.user.message":
        role = "system" if (event.payload or {}).get("role") == "system" else "user"
        entries.append(
            AgentConversationEntry(
                role=role,
                kind="message",
                turn_id=event.turn_id,
                text=event.text or "",
                payload=event.payload,
                started_at=event.created_at,
                finished_at=event.created_at,
            )
        )
        return

    if event.type in {
        "conversation.turn.started",
        "conversation.turn.completed",
        "conversation.request.opened",
        "conversation.request.resolved",
    }:
        entries.append(
            AgentConversationEntry(
                role="system",
                kind="status",
                turn_id=event.turn_id,
                text=event.text or _status_text(event.type),
                payload=event.payload,
                started_at=event.created_at,
                finished_at=event.created_at,
            )
        )
        return

    if event.type == "conversation.runtime.error":
        entries.append(
            AgentConversationEntry(
                role="system",
                kind="error",
                turn_id=event.turn_id,
                text=event.text or "Runtime error",
                payload=event.payload,
                started_at=event.created_at,
                finished_at=event.created_at,
            )
        )
        return

    role, kind = _entry_shape(event.type)
    if role is None or kind is None:
        return

    if event.type.endswith(".delta"):
        target = _find_open_entry(entries, role=role, kind=kind, turn_id=event.turn_id)
        if target is None:
            entries.append(
                AgentConversationEntry(
                    role=role,
                    kind=kind,
                    turn_id=event.turn_id,
                    text=event.text or "",
                    payload=event.payload,
                    started_at=event.created_at,
                    finished_at=None,
                )
            )
            return
        target.text = f"{target.text}{event.text or ''}"
        target.payload = event.payload or target.payload
        return

    target = _find_open_entry(entries, role=role, kind=kind, turn_id=event.turn_id)
    if target is None:
        entries.append(
            AgentConversationEntry(
                role=role,
                kind=kind,
                turn_id=event.turn_id,
                text=event.text or "",
                payload=event.payload,
                started_at=event.created_at,
                finished_at=event.created_at,
            )
        )
        return

    if event.text and not (target.text == event.text or target.text.endswith(event.text)):
        target.text = f"{target.text}{event.text}"
    target.payload = event.payload or target.payload
    target.finished_at = event.created_at


def _status_text(event_type: str) -> str:
    return {
        "conversation.turn.started": "Turn started",
        "conversation.turn.completed": "Turn completed",
        "conversation.request.opened": "User input requested",
        "conversation.request.resolved": "User input resolved",
    }.get(event_type, event_type)


def _entry_shape(event_type: str) -> tuple[str | None, str | None]:
    if "assistant.message" in event_type:
        return "assistant", "message"
    if "assistant.thinking" in event_type:
        return "assistant", "thinking"
    if "tool_call" in event_type:
        return "tool", "tool_call"
    return None, None


def _find_open_entry(
    entries: list[AgentConversationEntry],
    *,
    role: str,
    kind: str,
    turn_id: str | None,
) -> AgentConversationEntry | None:
    for entry in reversed(entries):
        if entry.role != role or entry.kind != kind:
            continue
        if entry.turn_id != turn_id:
            continue
        if entry.finished_at is None:
            return entry
    return None

