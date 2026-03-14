"""TUI-facing conversation stream projection service."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from vibrant.providers.base import CanonicalEvent

from ...types import (
    AgentConversationEntry,
    AgentConversationView,
    AgentStreamCallback,
    AgentStreamEvent,
    StreamSubscription,
    utc_now,
)
from .store import ConversationStore


@dataclass(slots=True)
class _ConversationSubscription(StreamSubscription):
    callback: AgentStreamCallback
    close_callback: Callable[[], None]

    def close(self) -> None:
        self.close_callback()


class ConversationStreamService:
    """Project canonical runtime events into durable conversation frames."""

    def __init__(self, store: ConversationStore) -> None:
        self.store = store
        self._subscribers: dict[str, list[AgentStreamCallback]] = {}
        self._agent_conversations: dict[str, str] = {}
        for manifest in self.store.list_manifests():
            for agent_id in manifest.agent_ids:
                self._agent_conversations[agent_id] = manifest.conversation_id

    def bind_agent(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        task_id: str | None,
        provider_thread_id: str | None = None,
    ) -> None:
        self.store.bind_agent(
            conversation_id=conversation_id,
            agent_id=agent_id,
            task_id=task_id,
            provider_thread_id=provider_thread_id,
        )
        self._agent_conversations[agent_id] = conversation_id

    def record_host_message(
        self,
        *,
        conversation_id: str,
        role: Literal["user", "system"],
        text: str,
        related_question_id: str | None = None,
    ) -> AgentStreamEvent:
        event = AgentStreamEvent(
            conversation_id=conversation_id,
            entry_id=str(uuid4()),
            source_event_id=None,
            sequence=self.store.allocate_sequence(conversation_id),
            agent_id=None,
            run_id=None,
            task_id=None,
            turn_id=None,
            item_id=None,
            type="conversation.user.message",
            text=text,
            payload={"role": role, "question_id": related_question_id},
            created_at=utc_now(),
        )
        return self._append_and_publish(event)

    def ingest_canonical(self, event: CanonicalEvent) -> list[AgentStreamEvent]:
        conversation_id = self._resolve_conversation_id(event)
        if conversation_id is None:
            return []
        projected = [
            self._to_stream_event(conversation_id, event, spec)
            for spec in self._project_event_types(event)
        ]
        stored: list[AgentStreamEvent] = []
        for stream_event in projected:
            stored.append(self._append_and_publish(stream_event))
        return stored

    def rebuild(self, conversation_id: str) -> AgentConversationView | None:
        manifest = self.store.manifest(conversation_id)
        if manifest is None:
            return None
        entries: list[AgentConversationEntry] = []
        for frame in self.store.load_frames(conversation_id):
            self._apply_frame(entries, frame)
        return AgentConversationView(
            conversation_id=conversation_id,
            agent_ids=list(manifest.agent_ids),
            task_ids=list(manifest.task_ids),
            active_turn_id=manifest.active_turn_id,
            entries=entries,
            updated_at=manifest.updated_at,
        )

    def subscribe(
        self,
        conversation_id: str,
        callback: AgentStreamCallback,
        *,
        replay: bool = False,
    ) -> StreamSubscription:
        callbacks = self._subscribers.setdefault(conversation_id, [])
        callbacks.append(callback)
        if replay:
            for frame in self.store.load_frames(conversation_id):
                result = callback(frame)
                if inspect.isawaitable(result):
                    raise RuntimeError("Conversation subscribers must be synchronous")
        return _ConversationSubscription(
            callback=callback,
            close_callback=lambda: self._unsubscribe(conversation_id, callback),
        )

    def _unsubscribe(self, conversation_id: str, callback: AgentStreamCallback) -> None:
        callbacks = self._subscribers.get(conversation_id)
        if callbacks is None:
            return
        try:
            callbacks.remove(callback)
        except ValueError:
            return
        if not callbacks:
            self._subscribers.pop(conversation_id, None)

    def _append_and_publish(self, event: AgentStreamEvent) -> AgentStreamEvent:
        stored = self.store.append_frame(event)
        for callback in tuple(self._subscribers.get(event.conversation_id, ())):
            result = callback(stored)
            if inspect.isawaitable(result):
                raise RuntimeError("Conversation subscribers must be synchronous")
        return stored

    def _resolve_conversation_id(self, event: CanonicalEvent) -> str | None:
        agent_id = event.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return self._agent_conversations.get(agent_id)
        return None

    def _project_event_types(self, event: CanonicalEvent) -> list[tuple[str, str | None, Mapping[str, Any] | None]]:
        event_type = str(event.get("type") or "")
        if event_type == "turn.started":
            return [("conversation.turn.started", None, None)]
        if event_type == "turn.completed":
            return [("conversation.turn.completed", None, None)]
        if event_type in {"content.delta", "assistant.message.delta"}:
            return [("conversation.assistant.message.delta", _coerce_text(event, "delta"), None)]
        if event_type == "assistant.message.completed":
            return [("conversation.assistant.message.completed", _coerce_text(event, "text"), None)]
        if event_type in {"reasoning.summary.delta", "assistant.thinking.delta"}:
            return [("conversation.assistant.thinking.delta", _coerce_text(event, "delta"), None)]
        if event_type == "assistant.thinking.completed":
            return [("conversation.assistant.thinking.completed", _coerce_text(event, "text"), None)]
        if event_type == "tool.call.started":
            return [("conversation.tool_call.started", _coerce_text(event, "name"), _payload(event))]
        if event_type == "tool.call.delta":
            return [("conversation.tool_call.delta", _coerce_text(event, "delta"), _payload(event))]
        if event_type == "tool.call.completed":
            return [("conversation.tool_call.completed", _coerce_text(event, "result"), _payload(event))]
        if event_type in {"request.opened", "user-input.requested"}:
            return [("conversation.request.opened", _request_text(event), _payload(event))]
        if event_type in {"request.resolved", "user-input.resolved"}:
            return [("conversation.request.resolved", _request_text(event), _payload(event))]
        if event_type == "runtime.error":
            return [("conversation.runtime.error", _coerce_text(event, "error_message"), _payload(event))]
        return []

    def _to_stream_event(
        self,
        conversation_id: str,
        event: CanonicalEvent,
        spec: tuple[str, str | None, Mapping[str, Any] | None],
    ) -> AgentStreamEvent:
        event_type, text, payload = spec
        turn_id = event.get("turn_id")
        item_id = event.get("item_id")
        return AgentStreamEvent(
            conversation_id=conversation_id,
            entry_id=str(uuid4()),
            source_event_id=_coerce_text(event, "event_id"),
            sequence=self.store.allocate_sequence(conversation_id),
            agent_id=_coerce_text(event, "agent_id"),
            run_id=_coerce_text(event, "run_id"),
            task_id=_coerce_text(event, "task_id"),
            turn_id=turn_id if isinstance(turn_id, str) else None,
            item_id=item_id if isinstance(item_id, str) else None,
            type=event_type,  # type: ignore[arg-type]
            text=text,
            payload=payload,
            created_at=_coerce_text(event, "timestamp") or utc_now(),
        )

    def _apply_frame(self, entries: list[AgentConversationEntry], frame: AgentStreamEvent) -> None:
        if frame.type == "conversation.user.message":
            role = "system" if (frame.payload or {}).get("role") == "system" else "user"
            entries.append(
                AgentConversationEntry(
                    role=role,
                    kind="message",
                    turn_id=frame.turn_id,
                    text=frame.text or "",
                    payload=frame.payload,
                    started_at=frame.created_at,
                    finished_at=frame.created_at,
                )
            )
            return

        if frame.type in {
            "conversation.turn.started",
            "conversation.turn.completed",
            "conversation.request.opened",
            "conversation.request.resolved",
        }:
            entries.append(
                AgentConversationEntry(
                    role="system",
                    kind="status",
                    turn_id=frame.turn_id,
                    text=frame.text or frame.type,
                    payload=frame.payload,
                    started_at=frame.created_at,
                    finished_at=frame.created_at,
                )
            )
            return

        if frame.type == "conversation.runtime.error":
            entries.append(
                AgentConversationEntry(
                    role="system",
                    kind="error",
                    turn_id=frame.turn_id,
                    text=frame.text or "Runtime error",
                    payload=frame.payload,
                    started_at=frame.created_at,
                    finished_at=frame.created_at,
                )
            )
            return

        role, kind = _entry_shape(frame.type)
        if role is None or kind is None:
            return

        if frame.type.endswith(".delta"):
            target = _find_open_entry(entries, role=role, kind=kind, turn_id=frame.turn_id)
            if target is None:
                target = AgentConversationEntry(
                    role=role,
                    kind=kind,
                    turn_id=frame.turn_id,
                    text=frame.text or "",
                    payload=frame.payload,
                    started_at=frame.created_at,
                    finished_at=None,
                )
                entries.append(target)
            else:
                target.text = f"{target.text}{frame.text or ''}"
                target.payload = frame.payload or target.payload
            return

        target = _find_open_entry(entries, role=role, kind=kind, turn_id=frame.turn_id)
        if target is None:
            entries.append(
                AgentConversationEntry(
                    role=role,
                    kind=kind,
                    turn_id=frame.turn_id,
                    text=frame.text or "",
                    payload=frame.payload,
                    started_at=frame.created_at,
                    finished_at=frame.created_at,
                )
            )
            return
        if frame.text and not (
            target.text == frame.text or target.text.endswith(frame.text)
        ):
            target.text = f"{target.text}{frame.text}"
        target.payload = frame.payload or target.payload
        target.finished_at = frame.created_at


def _coerce_text(event: Mapping[str, Any], key: str) -> str | None:
    value = event.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload = {key: value for key, value in event.items() if key not in {"type", "timestamp", "agent_id", "task_id"}}
    return payload or None


def _request_text(event: Mapping[str, Any]) -> str | None:
    for key in ("message", "method", "error_message"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _entry_shape(event_type: str) -> tuple[Literal["assistant", "tool"] | None, Literal["message", "thinking", "tool_call"] | None]:
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
    role: Literal["assistant", "tool"],
    kind: Literal["message", "thinking", "tool_call"],
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
