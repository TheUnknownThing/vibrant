"""Facade-driven Gatekeeper chat panel."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from ...agents.utils import extract_error_message
from ...models import ItemInfo, ItemType, ThreadInfo, ThreadStatus, TurnInfo, TurnRole, TurnStatus
from ...models.state import OrchestratorStatus, QuestionRecord
from ...orchestrator.facade import OrchestratorFacade
from ...orchestrator.types import AgentOutput, AgentRunSnapshot
from ..utility.gatekeeper import GatekeeperSnapshot, get_gatekeeper_snapshot
from .conversation_renderer import (
    MessageBlock,
    MessagePart,
    MessageRole,
    ReasoningPart,
    ReasoningStatus,
    TextPart,
    ToolCallPart,
    ToolCallStatus,
    render_conversation,
    render_part,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatPanelState:
    """Persistable state for the Gatekeeper chat panel."""

    provider_thread_id: str | None = None
    messages: list[MessageBlock] = field(default_factory=list)


class ChatPanel(Static):
    """Facade-driven chat panel for Gatekeeper conversations."""

    GATEKEEPER_THREAD_ID = "__gatekeeper__"

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        padding: 0;
        background: $surface;
    }

    ChatPanel.question-notification {
        border: tall $warning;
    }

    #chat-panel-layout {
        height: 1fr;
    }

    #chat-panel-header {
        height: 3;
        padding: 1 1 0 1;
        margin: 0 1;
        background: $primary-background;
        color: $text;
    }

    #chat-panel-subtitle {
        height: auto;
        padding: 0 1 1 1;
        margin: 0 1;
        color: $text-muted;
        background: $primary-background;
    }

    #chat-panel-notice {
        height: auto;
        padding: 1;
        margin: 1;
        background: $surface;
        color: $text-muted;
        display: none;
    }

    #chat-panel-notice.has-pending-question {
        color: $warning;
        background: $warning 12%;
        border-left: tall $warning;
    }

    #chat-panel-scroll {
        height: 1fr;
        padding: 0 1 1 1;
        scrollbar-size: 1 1;
    }

    #chat-panel-conversation {
        width: 100%;
    }
    """

    FLASH_DURATION_SECONDS = 1.5

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._facade: OrchestratorFacade | None = None
        self._subtitle_text = "Gatekeeper conversation"
        self._question_summary_text = ""
        self._has_pending_questions = False
        self._notification_token = 0
        self._state = ChatPanelState()
        self._conversation: Static | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-panel-layout"):
            yield Static("[b]Gatekeeper[/b]", id="chat-panel-header", markup=True)
            yield Static(self._subtitle_text, id="chat-panel-subtitle")
            yield Static(self._question_summary_text, id="chat-panel-notice", markup=False)
            with VerticalScroll(id="chat-panel-scroll"):
                self._conversation = Static("", id="chat-panel-conversation", markup=False)
                yield self._conversation

    def on_mount(self) -> None:
        self._refresh_view()

    def bind(self, facade: OrchestratorFacade | None) -> None:
        """Bind the panel to the current orchestrator facade."""

        self._facade = facade

    def sync(self, *, flash: bool = False) -> None:
        """Sync the panel from the current facade state."""

        snapshot = get_gatekeeper_snapshot(self._facade)
        if snapshot.provider_thread_id:
            self._state.provider_thread_id = snapshot.provider_thread_id

        assistant_messages = _assistant_messages(snapshot, self._facade)
        self._state.messages = _merge_messages(self._state.messages, assistant_messages)
        self._subtitle_text = _format_subtitle(
            snapshot.workflow_status,
            has_pending_questions=bool(snapshot.pending_questions),
        )
        self._question_summary_text = _render_question_summary(snapshot.questions)
        self._has_pending_questions = bool(snapshot.pending_questions)

        logger.debug(
            "Synced gatekeeper panel: %s messages, %s runs, %s pending questions",
            len(self._state.messages),
            len(snapshot.runs),
            len(snapshot.pending_questions),
        )

        self._refresh_view()
        if flash and snapshot.pending_questions:
            self.flash_question_notification()

    def append_user_message(self, text: str) -> None:
        """Append one local user message to the transcript."""

        normalized = text.strip()
        if not normalized:
            return

        self._state.messages = _merge_messages(
            self._state.messages,
            [
                MessageBlock(
                    message_id=f"user:{uuid4()}",
                    role="user",
                    parts=[TextPart(normalized)],
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )
        self._refresh_conversation()

    def export_state(self) -> ChatPanelState | None:
        """Return a copy of the current panel state."""

        if not self._state.messages and not self._state.provider_thread_id:
            return None
        return ChatPanelState(
            provider_thread_id=self._state.provider_thread_id,
            messages=[_clone_block(block) for block in self._state.messages],
        )

    def import_state(self, state: ChatPanelState | None) -> None:
        """Restore a previously exported panel state."""

        if state is None:
            return
        self._state = ChatPanelState(
            provider_thread_id=state.provider_thread_id,
            messages=[_clone_block(block) for block in state.messages],
        )
        self._refresh_view()

    def export_thread(self) -> ThreadInfo | None:
        """Serialize the panel state into the history-thread container."""

        state = self.export_state()
        if state is None or not state.messages:
            return None

        turns = [_turn_from_block(block) for block in state.messages]
        turns = [turn for turn in turns if turn is not None]
        if not turns:
            return None

        timestamps = [block.timestamp for block in state.messages if block.timestamp is not None]
        now = datetime.now(timezone.utc)
        created_at = min(timestamps, default=now)
        updated_at = max(timestamps, default=now)
        return ThreadInfo(
            id=self.GATEKEEPER_THREAD_ID,
            codex_thread_id=state.provider_thread_id,
            title="Gatekeeper",
            status=ThreadStatus.IDLE,
            model="gatekeeper",
            turns=turns,
            created_at=created_at,
            updated_at=updated_at,
        )

    def import_thread(self, thread: ThreadInfo) -> None:
        """Restore panel state from a history-thread container."""

        messages: list[MessageBlock] = []
        for turn in thread.turns:
            block = _block_from_turn(turn)
            if block is not None:
                messages.append(block)

        self.import_state(
            ChatPanelState(
                provider_thread_id=thread.codex_thread_id,
                messages=messages,
            )
        )

    def flash_question_notification(self) -> None:
        """Temporarily highlight the panel when a new question arrives."""

        self._notification_token += 1
        token = self._notification_token
        self.add_class("question-notification")
        self.set_timer(self.FLASH_DURATION_SECONDS, lambda: self._clear_question_notification(token))

    def _clear_question_notification(self, token: int) -> None:
        if token == self._notification_token:
            self.remove_class("question-notification")

    def _refresh_view(self) -> None:
        self._refresh_meta()
        self._refresh_conversation()

    def _refresh_meta(self) -> None:
        if not self.is_mounted:
            return

        self.query_one("#chat-panel-subtitle", Static).update(self._subtitle_text)
        notice = self.query_one("#chat-panel-notice", Static)
        if self._question_summary_text:
            notice.update(self._question_summary_text)
            notice.display = True
        else:
            notice.update("")
            notice.display = False
        notice.set_class(self._has_pending_questions, "has-pending-question")

    def _refresh_conversation(self) -> None:
        if not self.is_mounted or self._conversation is None:
            return

        self._conversation.update(render_conversation(self._state.messages))
        self.call_after_refresh(self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        with suppress(Exception):
            self.query_one("#chat-panel-scroll", VerticalScroll).scroll_end(animate=False)


def _assistant_messages(
    snapshot: GatekeeperSnapshot,
    facade: OrchestratorFacade | None,
) -> list[MessageBlock]:
    if facade is None:
        return []

    assistant_by_id: dict[str, MessageBlock] = {}
    for run in snapshot.runs:
        block = _block_from_run(run, facade.runs.events(run.run_id))
        if block is not None:
            assistant_by_id[block.message_id] = block

    active_run_id = None
    if snapshot.instance is not None:
        active_run_id = snapshot.instance.active_run_id or snapshot.instance.latest_run_id
    if snapshot.output is not None and active_run_id is not None:
        enriched = _overlay_output(
            assistant_by_id.get(active_run_id),
            output=snapshot.output,
            message_id=active_run_id,
        )
        if enriched is not None:
            assistant_by_id[active_run_id] = enriched

    messages = list(assistant_by_id.values())
    messages.sort(key=_message_sort_key)
    return messages


def _block_from_run(run: AgentRunSnapshot, events: list[dict[str, Any]]) -> MessageBlock | None:
    reasoning_text = ""
    reasoning_status: ReasoningStatus = "completed" if run.runtime.done else "in_progress"
    response_chunks: list[str] = []
    tool_parts: list[ToolCallPart] = []
    tool_indexes: dict[str, int] = {}
    error_text = ""
    last_timestamp = run.lifecycle.finished_at or run.lifecycle.started_at

    for event in events:
        event_type = str(event.get("type") or "")
        event_timestamp = _event_timestamp(event)
        if event_timestamp is not None:
            last_timestamp = _prefer_message_timestamp(last_timestamp, event_timestamp)

        if event_type == "reasoning.summary.delta":
            delta = str(event.get("delta") or "")
            if delta:
                reasoning_text = f"{reasoning_text}{delta}"
            continue

        if event_type == "task.progress":
            summary = _reasoning_summary_from_item(event.get("item"))
            if summary:
                reasoning_text = summary
                reasoning_status = "completed"
            continue

        if event_type == "request.opened" and event.get("request_kind") == "request":
            request_id = str(event.get("request_id") or "")
            tool_name = str(event.get("method") or "").strip()
            if request_id and tool_name:
                tool_indexes[request_id] = len(tool_parts)
                tool_parts.append(ToolCallPart(tool_name=tool_name, status="executing"))
            continue

        if event_type == "request.resolved" and event.get("request_kind") == "request":
            request_id = str(event.get("request_id") or "")
            tool_name = str(event.get("method") or "").strip()
            status: ToolCallStatus = "failed" if event.get("error") or event.get("error_message") else "success"
            if request_id in tool_indexes:
                tool_parts[tool_indexes[request_id]].status = status
            elif tool_name:
                tool_parts.append(ToolCallPart(tool_name=tool_name, status=status))
            continue

        if event_type == "content.delta":
            delta = str(event.get("delta") or "")
            if delta:
                response_chunks.append(delta)
            continue

        if event_type == "runtime.error":
            error_text = extract_error_message(event).strip()

    response_text = "".join(response_chunks).strip()
    parts: list[MessagePart] = []
    if reasoning_text.strip():
        parts.append(
            ReasoningPart(
                status=reasoning_status,
                content=TextPart(reasoning_text.strip()),
            )
        )
    parts.extend(tool_parts)
    if response_text:
        parts.append(TextPart(response_text))
    elif error_text:
        parts.append(TextPart(f"Error: {error_text}"))
    elif run.error:
        parts.append(TextPart(f"Error: {run.error}"))
    elif run.summary:
        parts.append(TextPart(run.summary))

    if not parts:
        return None
    return MessageBlock(
        message_id=run.run_id,
        role="assistant",
        parts=parts,
        timestamp=last_timestamp,
    )


def _overlay_output(
    block: MessageBlock | None,
    *,
    output: AgentOutput,
    message_id: str,
) -> MessageBlock | None:
    thinking_text = output.thinking.text.strip()
    response_text = output.partial_text.strip()
    if not response_text and output.status == "completed":
        response_text = "\n\n".join(
            segment.text.strip()
            for segment in output.segments
            if segment.kind == "response" and segment.text.strip()
        ).strip()
    error_text = output.error.message.strip() if output.error is not None else ""
    if not thinking_text and not response_text and not error_text:
        return block

    tool_parts = [part for part in block.parts if isinstance(part, ToolCallPart)] if block is not None else []
    parts: list[MessagePart] = []
    if thinking_text:
        reasoning_status: ReasoningStatus = (
            "completed" if output.thinking.status == "completed" else "in_progress"
        )
        parts.append(ReasoningPart(status=reasoning_status, content=TextPart(thinking_text)))
    parts.extend(_clone_part(part) for part in tool_parts)
    if response_text:
        parts.append(TextPart(response_text))
    elif error_text:
        parts.append(TextPart(f"Error: {error_text}"))

    if not parts:
        return block
    return MessageBlock(
        message_id=message_id,
        role="assistant",
        parts=parts,
        timestamp=_prefer_message_timestamp(block.timestamp if block is not None else None, output.updated_at),
    )


def _merge_messages(
    existing: list[MessageBlock],
    incoming: list[MessageBlock],
) -> list[MessageBlock]:
    if not incoming:
        return [_clone_block(block) for block in existing]

    incoming_by_id = {block.message_id: _clone_block(block) for block in incoming}
    merged: list[MessageBlock] = []
    seen_ids: set[str] = set()

    for block in existing:
        replacement = incoming_by_id.get(block.message_id)
        if replacement is not None:
            merged.append(replacement)
            seen_ids.add(block.message_id)
        else:
            merged.append(_clone_block(block))
            seen_ids.add(block.message_id)

    for block in incoming:
        if block.message_id not in seen_ids:
            merged.append(_clone_block(block))

    merged.sort(key=_message_sort_key)
    return merged


def _message_sort_key(block: MessageBlock) -> tuple[float, int, str]:
    timestamp = block.timestamp.timestamp() if block.timestamp is not None else 0.0
    role_rank = 0 if block.role == "user" else 1
    return (timestamp, role_rank, block.message_id)


def _prefer_message_timestamp(current: datetime | None, candidate: datetime | None) -> datetime | None:
    if current is None:
        return candidate
    if candidate is None:
        return current
    return candidate if candidate >= current else current


def _render_question_summary(records: tuple[QuestionRecord, ...]) -> str:
    if not records:
        return ""

    rendered_blocks: list[str] = []
    for record in records[-3:]:
        lines = [
            "Gatekeeper → User",
            f"Q: {record.text}",
        ]
        if record.answer:
            lines.extend(
                [
                    "You → Gatekeeper",
                    f"A: {record.answer}",
                ]
            )
        elif record.is_pending():
            lines.append("Status: awaiting your answer")
        rendered_blocks.append("\n".join(lines))

    return "\n\n".join(rendered_blocks)


def _format_subtitle(status: OrchestratorStatus | None, *, has_pending_questions: bool) -> str:
    if status is OrchestratorStatus.PLANNING:
        return "Planning · User ↔ Gatekeeper"
    if status is OrchestratorStatus.EXECUTING:
        return "Executing · Gatekeeper escalation" if has_pending_questions else "Executing · Gatekeeper history"
    if status is OrchestratorStatus.PAUSED:
        return "Paused · Review history"
    if status is OrchestratorStatus.COMPLETED:
        return "Completed · Review history"
    return "Gatekeeper conversation"


def _reasoning_summary_from_item(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    item_type = str(item.get("type") or "").strip().lower()
    if item_type != "reasoning":
        return ""

    for candidate in (item.get("summary"), item.get("text"), item.get("content")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, list):
            parts = [entry.get("text", "") for entry in candidate if isinstance(entry, dict)]
            joined = "".join(part for part in parts if part)
            if joined.strip():
                return joined.strip()
    return ""


def _event_timestamp(event: dict[str, Any]) -> datetime | None:
    value = event.get("timestamp")
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _turn_from_block(block: MessageBlock) -> TurnInfo | None:
    content = "\n\n".join(filter(None, (render_part(part) for part in block.parts))).strip()
    if not content:
        return None

    timestamp = block.timestamp
    return TurnInfo(
        role=_turn_role(block.role),
        status=TurnStatus.COMPLETED,
        started_at=timestamp,
        completed_at=timestamp,
        items=[
            ItemInfo(
                type=ItemType.TEXT,
                content=content,
                metadata={
                    "message_id": block.message_id,
                    "message_parts": [_serialize_part(part) for part in block.parts],
                },
            )
        ],
    )


def _block_from_turn(turn: TurnInfo) -> MessageBlock | None:
    metadata = _first_item_metadata(turn)
    message_id = metadata.get("message_id") if isinstance(metadata.get("message_id"), str) else turn.id
    serialized_parts = metadata.get("message_parts")
    parts = _deserialize_parts(serialized_parts)
    if not parts:
        content = "\n\n".join(item.content.strip() for item in turn.items if item.content.strip()).strip()
        if not content:
            return None
        parts = [TextPart(content)]

    role = turn.role.value if isinstance(turn.role, TurnRole) else str(turn.role or "user")
    normalized_role: MessageRole = "assistant" if role == "assistant" else "system" if role == "system" else "user"
    return MessageBlock(
        message_id=message_id,
        role=normalized_role,
        parts=parts,
        timestamp=turn.completed_at or turn.started_at,
    )


def _first_item_metadata(turn: TurnInfo) -> dict[str, Any]:
    for item in turn.items:
        if isinstance(item.metadata, dict):
            return item.metadata
    return {}


def _serialize_part(part: MessagePart) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ReasoningPart):
        return {
            "type": "reasoning",
            "status": part.status,
            "content": part.content.text,
        }
    return {
        "type": "tool_call",
        "tool_name": part.tool_name,
        "status": part.status,
    }


def _deserialize_parts(value: object) -> list[MessagePart]:
    if not isinstance(value, list):
        return []

    parts: list[MessagePart] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        part_type = str(entry.get("type") or "").strip().lower()
        if part_type == "text":
            text = str(entry.get("text") or "").strip()
            if text:
                parts.append(TextPart(text))
            continue
        if part_type == "reasoning":
            content = str(entry.get("content") or "").strip()
            status = str(entry.get("status") or "completed").strip().lower()
            if content:
                parts.append(
                    ReasoningPart(
                        status="in_progress" if status == "in_progress" else "completed",
                        content=TextPart(content),
                    )
                )
            continue
        if part_type == "tool_call":
            tool_name = str(entry.get("tool_name") or "").strip()
            status = str(entry.get("status") or "executing").strip().lower()
            if tool_name:
                normalized_status: ToolCallStatus = (
                    "success" if status == "success" else "failed" if status == "failed" else "executing"
                )
                parts.append(ToolCallPart(tool_name=tool_name, status=normalized_status))
    return parts


def _turn_role(role: MessageRole) -> TurnRole:
    if role == "assistant":
        return TurnRole.ASSISTANT
    if role == "system":
        return TurnRole.SYSTEM
    return TurnRole.USER


def _clone_block(block: MessageBlock) -> MessageBlock:
    return MessageBlock(
        message_id=block.message_id,
        role=block.role,
        parts=[_clone_part(part) for part in block.parts],
        timestamp=block.timestamp,
    )


def _clone_part(part: MessagePart) -> MessagePart:
    if isinstance(part, TextPart):
        return TextPart(part.text)
    if isinstance(part, ReasoningPart):
        return ReasoningPart(part.status, TextPart(part.content.text))
    return ToolCallPart(part.tool_name, part.status)
