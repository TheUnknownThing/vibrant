"""Conversation rendering helpers for Gatekeeper transcript widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

MessageRole = Literal["user", "assistant", "system"]
ReasoningStatus = Literal["in_progress", "completed"]
ToolCallStatus = Literal["executing", "success", "failed"]


class TextPart(Static):
    """Plain text message part widget."""

    def __init__(self, text: str, **kwargs: object) -> None:
        super().__init__("", markup=False, classes="conversation-part text-part", **kwargs)
        self.styles.height = "auto"
        self.text = text
        self._refresh()

    def _refresh(self) -> None:
        self.update(self.text)

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
        self.status: ReasoningStatus = status
        self.content = content
        self.content.add_class("reasoning-content")
        self._label = Static("", markup=False, classes="reasoning-label")
        self._refresh()

    def compose(self) -> ComposeResult:
        yield self._label
        yield self.content

    def _refresh(self) -> None:
        label = "Reasoning..." if self.status == "in_progress" else "Reasoning"
        self._label.update(label)

    def plain_text(self) -> str:
        label = "Reasoning..." if self.status == "in_progress" else "Reasoning"
        content = self.content.plain_text().strip()
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
        super().__init__("", markup=False, classes="conversation-part tool-call-part", **kwargs)
        self.styles.height = "auto"
        self.tool_name = tool_name
        self.status = status
        self._refresh()

    def _refresh(self) -> None:
        status_label = {
            "executing": "executing",
            "success": "done",
            "failed": "failed",
        }[self.status]
        self.update(f"Tool · {self.tool_name} · {status_label}")

    def plain_text(self) -> str:
        status_label = {
            "executing": "executing",
            "success": "done",
            "failed": "failed",
        }[self.status]
        return f"Tool · {self.tool_name} · {status_label}"

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


MessagePart = TextPart | ReasoningPart | ToolCallPart


@dataclass(slots=True)
class MessageBlock:
    """One conversation block in the chat panel."""

    message_id: str
    role: MessageRole
    parts: list[MessagePart] = field(default_factory=list)
    timestamp: datetime | None = None

    def plain_text(self) -> str:
        body = self.body_text()
        return f"{role_label(self.role)}\n{body}" if body else role_label(self.role)

    def body_text(self) -> str:
        rendered_parts = [part.plain_text() for part in self.parts]
        rendered_parts = [part for part in rendered_parts if part]
        return "\n\n".join(rendered_parts)


class MessageBlockWidget(Vertical):
    """Widget wrapper for one conversation message block."""

    def __init__(self, block: MessageBlock, **kwargs: object) -> None:
        super().__init__(classes="conversation-block", **kwargs)
        self.styles.height = "auto"
        self.message_id = block.message_id
        self._role: MessageRole = block.role
        self._timestamp = block.timestamp
        self._parts: list[MessagePart] = [part.clone() for part in block.parts]
        self._role_header = Static("", markup=False, classes="conversation-role")
        self._parts_region = Vertical(classes="conversation-parts")
        self._parts_region.styles.height = "auto"

    def compose(self) -> ComposeResult:
        yield self._role_header
        yield self._parts_region

    def on_mount(self) -> None:
        self._role_header.update(role_label(self._role))
        self._rebuild_parts()

    def set_block(self, block: MessageBlock) -> None:
        self._timestamp = block.timestamp
        if self._role != block.role:
            self._role = block.role
            self._role_header.update(role_label(self._role))
        self._sync_parts(block.parts)

    def _sync_parts(self, updated_parts: list[MessagePart]) -> None:
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

    def __init__(
        self,
        *,
        empty_message: str = "No Gatekeeper activity yet. Send a message to get started.",
        **kwargs: object,
    ) -> None:
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


def render_conversation(messages: list[MessageBlock]) -> str:
    """Render message blocks into a plain-text transcript."""

    if not messages:
        return "No Gatekeeper activity yet. Send a message to get started."
    return "\n\n".join(block.plain_text() for block in messages)


def render_block(block: MessageBlock) -> str:
    """Render one message block."""

    return block.plain_text()


def render_part(part: MessagePart) -> str:
    """Render one message part."""

    return part.plain_text()


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
    "MessageBlock",
    "MessagePart",
    "MessageRole",
    "ReasoningPart",
    "ReasoningStatus",
    "TextPart",
    "ToolCallPart",
    "ToolCallStatus",
    "indent",
    "render_block",
    "render_conversation",
    "render_part",
    "role_label",
]
