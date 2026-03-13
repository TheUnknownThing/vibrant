"""Conversation rendering helpers for Gatekeeper transcript widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

MessageRole = Literal["user", "assistant", "system"]
ReasoningStatus = Literal["in_progress", "completed"]
ToolCallStatus = Literal["executing", "success", "failed"]


@dataclass(slots=True)
class TextPart:
    """Plain text message part."""

    text: str


@dataclass(slots=True)
class ReasoningPart:
    """Reasoning summary message part."""

    status: ReasoningStatus
    content: TextPart


@dataclass(slots=True)
class ToolCallPart:
    """Tool-call status message part."""

    tool_name: str
    status: ToolCallStatus


MessagePart = TextPart | ReasoningPart | ToolCallPart


@dataclass(slots=True)
class MessageBlock:
    """One conversation block in the chat panel."""

    message_id: str
    role: MessageRole
    parts: list[MessagePart] = field(default_factory=list)
    timestamp: datetime | None = None


def render_conversation(messages: list[MessageBlock]) -> str:
    """Render message blocks into a plain-text transcript."""

    if not messages:
        return "No Gatekeeper activity yet. Send a message to get started."
    return "\n\n".join(render_block(block) for block in messages)


def render_block(block: MessageBlock) -> str:
    """Render one message block."""

    rendered_parts = [render_part(part) for part in block.parts]
    rendered_parts = [part for part in rendered_parts if part]
    body = "\n\n".join(rendered_parts)
    return f"{role_label(block.role)}\n{body}" if body else role_label(block.role)


def render_part(part: MessagePart) -> str:
    """Render one message part."""

    if isinstance(part, TextPart):
        return part.text.strip()
    if isinstance(part, ReasoningPart):
        label = "Reasoning..." if part.status == "in_progress" else "Reasoning"
        content = part.content.text.strip()
        return f"{label}\n{indent(content)}" if content else label

    status = {
        "executing": "executing",
        "success": "done",
        "failed": "failed",
    }[part.status]
    return f"Tool · {part.tool_name} · {status}"


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
