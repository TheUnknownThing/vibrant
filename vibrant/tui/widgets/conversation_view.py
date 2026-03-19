"""Conversation view and rendering helpers for Gatekeeper transcript widgets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Markdown, Static

from ...orchestrator.types import AgentConversationEntry, AgentConversationView, AgentStreamEvent

MessageRole = Literal["user", "assistant", "system"]
ReasoningStatus = Literal["in_progress", "completed"]
ToolCallStatus = Literal["executing", "success", "failed"]

EMPTY_CONVERSATION_MESSAGE = "No Gatekeeper messages yet. Start planning below."
OMITTED_STATUS_TEXTS = frozenset({"Turn started", "Turn completed"})
TOOL_STATUS_LABELS: dict[ToolCallStatus, str] = {
    "executing": "executing",
    "success": "done",
    "failed": "failed",
}


class TextPart(Markdown):
    """Markdown-backed message part widget."""

    def __init__(self, text: str, **kwargs: object) -> None:
        super().__init__(text, classes="conversation-part text-part msg-content", **kwargs)
        self.styles.height = "auto"
        self.text = text

    def _refresh(self) -> None:
        self.call_after_refresh(self.update, self.text)

    def plain_text(self) -> str:
        return self.text

    def clone(self) -> TextPart:
        return TextPart(self.text)

    def sync_from(self, other: TextPart) -> None:
        if self.text == other.text:
            return
        self.text = other.text
        self._refresh()


class ReasoningPart(Vertical):
    """Reasoning summary part widget."""

    def __init__(self, status: ReasoningStatus, content: TextPart, **kwargs: object) -> None:
        super().__init__(classes="conversation-part reasoning-part", **kwargs)
        self.styles.height = "auto"
        self.styles.opacity = 0.5
        self.status: ReasoningStatus = status
        self.content = content
        self.content.add_class("reasoning-content")
        self._label = Static("", markup=False, classes="reasoning-label")
        self._refresh()

    def compose(self) -> ComposeResult:
        yield self._label
        yield self.content

    def _refresh(self) -> None:
        self._label.update(_reasoning_label(self.status))

    def plain_text(self) -> str:
        content = self.content.plain_text().strip()
        label = _reasoning_label(self.status)
        return f"{label}\n{indent(content)}" if content else label

    def clone(self) -> ReasoningPart:
        return ReasoningPart(self.status, self.content.clone())

    def sync_from(self, other: ReasoningPart) -> None:
        if self.status != other.status:
            self.status = other.status
            self._refresh()
        self.content.sync_from(other.content)


class ToolCallPart(Static):
    """Tool-call status part widget."""

    def __init__(self, tool_name: str, status: ToolCallStatus, **kwargs: object) -> None:
        super().__init__("", markup=False, classes="conversation-part tool-call-part msg-tool", **kwargs)
        self.styles.height = "auto"
        self.tool_name = tool_name
        self.status = status
        self._refresh()

    def _refresh(self) -> None:
        self.update(_tool_status_text(self.tool_name, self.status))

    def plain_text(self) -> str:
        return _tool_status_text(self.tool_name, self.status)

    def clone(self) -> ToolCallPart:
        return ToolCallPart(self.tool_name, self.status)

    def sync_from(self, other: ToolCallPart) -> None:
        changed = False
        if self.tool_name != other.tool_name:
            self.tool_name = other.tool_name
            changed = True
        if self.status != other.status:
            self.status = other.status
            changed = True
        if changed:
            self._refresh()


@dataclass(slots=True)
class MessageBlock:
    """One conversation block in the chat panel."""

    message_id: str
    role: MessageRole
    turn_id: str | None = None
    title: str | None = None
    variant_class: str | None = None
    parts: list[TextPart | ReasoningPart | ToolCallPart] = field(default_factory=list)

    def plain_text(self) -> str:
        body = self.body_text()
        role_text = self.title or role_label(self.role)
        return f"{role_text}\n{body}" if body else role_text

    def body_text(self) -> str:
        rendered_parts = [part.plain_text() for part in self.parts]
        return "\n\n".join(part for part in rendered_parts if part)


class MessageBlockWidget(Vertical):
    """Widget wrapper for one conversation message block."""

    def __init__(self, block: MessageBlock, **kwargs: object) -> None:
        super().__init__(classes="conversation-block", **kwargs)
        self.styles.height = "auto"
        self.message_id = block.message_id
        self._role: MessageRole = block.role
        self._title = block.title
        self._variant_class = block.variant_class
        self._parts: list[TextPart | ReasoningPart | ToolCallPart] = [part.clone() for part in block.parts]
        self._role_header = Static("", markup=False, classes="conversation-role msg-role")
        self._parts_region = Vertical(classes="conversation-parts")
        self._parts_region.styles.height = "auto"

    def compose(self) -> ComposeResult:
        yield self._role_header
        yield self._parts_region

    def on_mount(self) -> None:
        self._sync_role_classes()
        self._role_header.update(self._title or role_label(self._role))
        self._rebuild_parts()

    def set_block(self, block: MessageBlock) -> None:
        if self._role != block.role or self._title != block.title or self._variant_class != block.variant_class:
            self._role = block.role
            self._title = block.title
            self._variant_class = block.variant_class
            self._sync_role_classes()
            self._role_header.update(self._title or role_label(self._role))
        self._sync_parts(block.parts)

    def _sync_role_classes(self) -> None:
        for role_class in ("user-msg", "assistant-msg", "system-msg", "question-msg"):
            self.remove_class(role_class)
        self.add_class(f"{self._role}-msg")
        if self._variant_class:
            self.add_class(self._variant_class)

    def _sync_parts(self, updated_parts: list[TextPart | ReasoningPart | ToolCallPart]) -> None:
        if len(self._parts) != len(updated_parts):
            self._parts = [part.clone() for part in updated_parts]
            self._rebuild_parts()
            return

        for current, updated in zip(self._parts, updated_parts):
            if type(current) is not type(updated):
                self._parts = [part.clone() for part in updated_parts]
                self._rebuild_parts()
                return

        for current, updated in zip(self._parts, updated_parts):
            current.sync_from(updated)

    def _rebuild_parts(self) -> None:
        self._parts_region.remove_children()
        for part in self._parts:
            self._parts_region.mount(part)


class ConversationRegion(VerticalScroll):
    """Conversation widget that incrementally updates chat blocks by message id."""

    def __init__(self, *, empty_message: str = EMPTY_CONVERSATION_MESSAGE, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.styles.height = "1fr"
        self._empty_message = empty_message
        self._message_order: list[str] = []
        self._message_widgets: dict[str, MessageBlockWidget] = {}

    def set_messages(self, messages: list[MessageBlock]) -> None:
        if not messages:
            self.remove_children()
            self._message_order = []
            self._message_widgets.clear()
            self.mount(Static(self._empty_message, markup=False, classes="conversation-empty"))
            return

        order = [block.message_id for block in messages]
        if order != self._message_order:
            self.remove_children()
            self._message_order = order
            self._message_widgets.clear()
            for block in messages:
                widget = MessageBlockWidget(block)
                self._message_widgets[block.message_id] = widget
                self.mount(widget)
            return

        for block in messages:
            widget = self._message_widgets.get(block.message_id)
            if widget is None:
                self._message_order = []
                self.set_messages(messages)
                return
            widget.set_block(block)


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

    ConversationView .question-msg {
        background: $warning 12%;
        border-left: tall $warning;
    }

    ConversationView .system-msg {
        background: $surface-lighten-1;
        border-left: tall $panel;
    }

    ConversationView .conversation-role {
        text-style: bold;
        margin-bottom: 0;
    }

    ConversationView .msg-content {
        margin-top: 0;
    }

    ConversationView .text-part {
        padding: 0;
    }

    ConversationView .msg-tool {
        padding: 0 1;
        margin: 0;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._scroll: ConversationRegion | None = None
        self._conversation: AgentConversationView | None = None
        self._pending_questions: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        self._scroll = ConversationRegion(id="conversation-scroll", empty_message=EMPTY_CONVERSATION_MESSAGE)
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

    def sync_state(
        self,
        *,
        conversation: AgentConversationView | None,
        pending_questions: list[str] | tuple[str, ...],
    ) -> None:
        """Update conversation and pending question state in one render pass."""

        self._conversation = _clone_conversation(conversation)
        self._pending_questions = tuple(question for question in pending_questions if question)
        self._render_once()

    def set_pending_questions(self, pending_questions: list[str] | tuple[str, ...]) -> None:
        """Render pending Gatekeeper questions inline after the conversation."""

        self._pending_questions = tuple(question for question in pending_questions if question)
        self._render_once()

    def ingest_stream_event(self, event: AgentStreamEvent) -> None:
        """Incrementally apply one stream frame to the local view model."""

        if self._conversation is None or self._conversation.conversation_id != event.conversation_id:
            self._conversation = AgentConversationView(
                conversation_id=event.conversation_id,
                run_ids=[],
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

    def snapshot_conversation(self) -> AgentConversationView | None:
        """Return a detached copy of the currently rendered conversation."""

        return _clone_conversation(self._conversation)

    def _render_once(self) -> None:
        if not self.is_mounted or self._scroll is None:
            return
        blocks = _render_blocks(self._conversation)
        pending_block = _pending_question_block(self._pending_questions)
        if pending_block is not None:
            blocks.append(pending_block)
        self._scroll.set_messages(blocks)
        self._scroll.scroll_end(animate=False)


def _reasoning_label(status: ReasoningStatus) -> str:
    return "Reasoning..." if status == "in_progress" else "Reasoning"


def _tool_status_text(tool_name: str, status: ToolCallStatus) -> str:
    return f"Tool · {tool_name} · {TOOL_STATUS_LABELS[status]}"


def _pending_question_block(pending_questions: tuple[str, ...]) -> MessageBlock | None:
    if not pending_questions:
        return None

    body = _pending_question_text(pending_questions)
    return MessageBlock(
        message_id="pending-question",
        role="assistant",
        turn_id=None,
        title="Gatekeeper Question",
        variant_class="question-msg",
        parts=[TextPart(body)],
    )


def _pending_question_text(pending_questions: tuple[str, ...]) -> str:
    if len(pending_questions) == 1:
        return pending_questions[0]
    return "\n\n".join(f"{index}. {question}" for index, question in enumerate(pending_questions, start=1))


def _tool_body(entry: AgentConversationEntry) -> str:
    payload = entry.payload or {}
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    if result is not None:
        return json.dumps(result, indent=2, sort_keys=True)

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


def _block_role(entry: AgentConversationEntry) -> MessageRole:
    if entry.role == "tool":
        return "assistant"
    return _message_role(entry.role)


def _message_role(role: str) -> MessageRole:
    if role in {"user", "assistant", "system"}:
        return role
    return "system"


def _omit_entry_from_blocks(entry: AgentConversationEntry) -> bool:
    if entry.role != "system" or entry.kind != "status":
        return False
    return (entry.text or "").strip() in OMITTED_STATUS_TEXTS


def _should_append_to_previous(
    rendered: list[MessageBlock],
    entry: AgentConversationEntry,
    role: MessageRole,
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
    stripped_text = entry.text.strip()

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


def _tool_status(entry: AgentConversationEntry) -> ToolCallStatus:
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
        run_ids=list(conversation.run_ids),
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
    if event.run_id and event.run_id not in conversation.run_ids:
        conversation.run_ids.append(event.run_id)
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
                text=event.text or _status_text(event),
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
        target = _find_open_entry(
            entries,
            role=role,
            kind=kind,
            turn_id=event.turn_id,
            item_id=event.item_id,
        )
        if target is None:
            entries.append(
                AgentConversationEntry(
                    role=role,
                    kind=kind,
                    turn_id=event.turn_id,
                    text=event.text or "",
                    payload=_entry_payload(event.payload, event.item_id),
                    started_at=event.created_at,
                    finished_at=None,
                )
            )
            return
        target.text = f"{target.text}{event.text or ''}"
        target.payload = _merged_entry_payload(target.payload, event.payload, event.item_id)
        return

    target = _find_open_entry(
        entries,
        role=role,
        kind=kind,
        turn_id=event.turn_id,
        item_id=event.item_id,
    )
    if target is None:
        entries.append(
            AgentConversationEntry(
                role=role,
                kind=kind,
                turn_id=event.turn_id,
                text=event.text or "",
                payload=_entry_payload(event.payload, event.item_id),
                started_at=event.created_at,
                finished_at=None if event.type == "conversation.tool_call.started" else event.created_at,
            )
        )
        return

    if event.text and not (target.text == event.text or target.text.endswith(event.text)):
        target.text = f"{target.text}{event.text}"
    target.payload = _merged_entry_payload(target.payload, event.payload, event.item_id)
    target.finished_at = event.created_at


def _request_status_text(payload: object, *, resolved: bool) -> str:
    request_kind = ""
    if isinstance(payload, dict):
        request_kind = str(payload.get("request_kind") or "").strip().lower()
    if request_kind == "approval":
        return "Approval resolved" if resolved else "Approval requested"
    if request_kind == "user-input":
        return "User input resolved" if resolved else "User input requested"
    return "Request resolved" if resolved else "Request opened"


def _status_text(event: AgentStreamEvent) -> str:
    return {
        "conversation.turn.started": "Turn started",
        "conversation.turn.completed": "Turn completed",
        "conversation.request.opened": _request_status_text(event.payload, resolved=False),
        "conversation.request.resolved": _request_status_text(event.payload, resolved=True),
    }.get(event.type, event.type)


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
    item_id: str | None,
) -> AgentConversationEntry | None:
    if not entries:
        return None

    entry = entries[-1]
    if entry.role != role or entry.kind != kind:
        return None
    if entry.turn_id != turn_id or entry.finished_at is not None:
        return None
    if item_id is not None and _entry_item_id(entry) not in {None, item_id}:
        return None
    return entry


def _entry_item_id(entry: AgentConversationEntry) -> str | None:
    payload = entry.payload or {}
    item_id = payload.get("item_id")
    if isinstance(item_id, str) and item_id:
        return item_id
    return None


def _entry_payload(payload: object, item_id: str | None) -> object:
    if item_id is None:
        return payload
    if not isinstance(payload, dict):
        return {"item_id": item_id}
    if payload.get("item_id") == item_id:
        return payload
    return {**payload, "item_id": item_id}


def _merged_entry_payload(current: object, incoming: object, item_id: str | None) -> object:
    payload = incoming or current
    return _entry_payload(payload, item_id)


def indent(text: str) -> str:
    """Indent multi-line sub-content for readability."""

    return "\n".join(f"  {line}" if line else "" for line in text.splitlines())


def role_label(role: MessageRole) -> str:
    """Return the display label for a message role."""

    if role == "user":
        return "You"
    if role == "assistant":
        return "Gatekeeper"
    return "System"


__all__ = [
    "ConversationRegion",
    "ConversationView",
    "EMPTY_CONVERSATION_MESSAGE",
    "MessageBlock",
    "MessageRole",
    "MessageBlockWidget",
    "ReasoningPart",
    "ReasoningStatus",
    "TextPart",
    "ToolCallPart",
    "ToolCallStatus",
    "indent",
    "role_label",
    "_render_blocks",
]
