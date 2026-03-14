"""Conversation view widget backed by orchestrator conversation streams."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from ...orchestrator.types import AgentConversationEntry, AgentConversationView, AgentStreamEvent
from .conversation_renderer import (
    ConversationRegion,
    MessageBlock,
    ReasoningPart,
    TextPart,
    ToolCallPart,
)


class ConversationView(Static):
    """Scrollable view of one orchestrator-managed conversation."""

    DEFAULT_CSS = """
    ConversationView #conversation-scroll {
        height: 1fr;
        padding: 0 1;
    }

    ConversationView .conversation-block {
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

    ConversationView .msg-role,
    ConversationView .conversation-role {
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
        self._scroll: ConversationRegion | None = None
        self._conversation: AgentConversationView | None = None
        self._empty_text = "[dim]No Gatekeeper messages yet. Start planning below.[/dim]"

    def compose(self) -> ComposeResult:
        self._scroll = ConversationRegion(
            id="conversation-scroll",
            empty_message="No Gatekeeper messages yet. Start planning below.",
        )
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

        if self._scroll is None:
            return

        self._scroll.set_messages(_render_blocks(self._conversation))
        self._scroll.scroll_end(animate=False)


def _tool_body(entry: AgentConversationEntry) -> str:
    payload = entry.payload or {}
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    text = entry.text.strip()
    title = _tool_name(entry)
    if text and text != title:
        return text
    return ""


def _render_blocks(conversation: AgentConversationView | None) -> list[MessageBlock]:
    if conversation is None:
        return []

    rendered: list[MessageBlock] = []
    for index, entry in enumerate(conversation.entries):
        if _omit_entry_from_blocks(entry):
            continue
        parts = _message_parts(entry)
        if not parts:
            continue
        role = _block_role(entry)
        if _should_append_to_previous(rendered, entry, role):
            rendered[-1].parts.extend(parts)
            continue
        rendered.append(
            MessageBlock(
                message_id=_message_id(entry, index),
                role=role,
                turn_id=entry.turn_id,
                parts=parts,
            )
        )
    return rendered


def _message_id(entry: AgentConversationEntry, index: int) -> str:
    return f"{index}:{entry.turn_id or '-'}:{entry.role}:{entry.kind}"


def _block_role(entry: AgentConversationEntry) -> str:
    if entry.role == "tool":
        return "assistant"
    return _message_role(entry.role)


def _message_role(role: str) -> str:
    if role in {"user", "assistant", "system"}:
        return role
    return "system"


def _omit_entry_from_blocks(entry: AgentConversationEntry) -> bool:
    if entry.role != "system" or entry.kind != "status":
        return False
    return (entry.text or "").strip() in {"Turn started", "Turn completed"}


def _should_append_to_previous(
    rendered: list[MessageBlock],
    entry: AgentConversationEntry,
    role: str,
) -> bool:
    if not rendered:
        return False
    previous = rendered[-1]
    if previous.role != role:
        return False

    previous_turn_id = previous.turn_id
    if entry.turn_id and previous_turn_id and entry.turn_id != previous_turn_id:
        return False
    if role == "system":
        return entry.turn_id == previous_turn_id
    return True


def _message_parts(entry: AgentConversationEntry) -> list[TextPart | ReasoningPart | ToolCallPart]:
    text = entry.text or ""
    stripped_text = text.strip()

    if entry.kind == "thinking":
        return [
            ReasoningPart(
                "in_progress" if entry.finished_at is None else "completed",
                TextPart(stripped_text or "Thinking..."),
            )
        ]

    if entry.kind == "tool_call":
        parts: list[TextPart | ReasoningPart | ToolCallPart] = [
            ToolCallPart(_tool_name(entry), _tool_status(entry))
        ]
        body = _tool_body(entry)
        if body:
            parts.append(TextPart(body))
        return parts

    if entry.kind == "status":
        return [TextPart(stripped_text or "Status updated")]

    if entry.kind == "error":
        error_text = stripped_text or "Runtime error"
        if not error_text.lower().startswith("error"):
            error_text = f"Error: {error_text}"
        return [TextPart(error_text)]

    if stripped_text:
        return [TextPart(stripped_text)]
    return []


def _tool_name(entry: AgentConversationEntry) -> str:
    payload = entry.payload or {}
    name = payload.get("tool_name") or payload.get("name") or entry.text or "tool"
    name_text = str(name).strip()
    return name_text or "tool"


def _tool_status(entry: AgentConversationEntry) -> str:
    payload = entry.payload or {}
    error = payload.get("error")
    if error not in (None, "", {}):
        return "failed"
    if entry.finished_at is None:
        return "executing"
    return "success"


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
    # Only the trailing open widget can keep streaming content. If a later
    # message/tool widget has already been appended, preserve transcript order
    # by starting a fresh widget for subsequent deltas.
    if not entries:
        return None
    entry = entries[-1]
    if entry.role != role or entry.kind != kind:
        return None
    if entry.turn_id != turn_id or entry.finished_at is not None:
        return None
    return entry
