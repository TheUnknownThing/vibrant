"""TUI-facing conversation stream projection service."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from vibrant.providers.base import CanonicalEvent
from vibrant.type_defs import JSONMapping, JSONObject, JSONValue, is_json_mapping

from ...types import AgentConversationEntry, AgentConversationView, AgentStreamCallback, AgentStreamEvent, StreamSubscription, utc_now
from .store import ConversationStore

_ENTRY_PROJECTION_KEY = "_projection"
_MCP_USAGE_KEY = "mcp_usage"


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
        self._run_conversations: dict[str, str] = {}
        for manifest in self.store.list_manifests():
            for run_id in manifest.run_ids:
                self._run_conversations[run_id] = manifest.conversation_id

    def bind_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
    ) -> None:
        manifest = self.store.bind_run(
            conversation_id=conversation_id,
            run_id=run_id,
        )
        for manifest_run_id in manifest.run_ids:
            self._run_conversations[manifest_run_id] = conversation_id

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
            run_ids=list(manifest.run_ids),
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
        run_id = event.get("run_id")
        if isinstance(run_id, str) and run_id:
            return self._run_conversations.get(run_id)
        return None

    def _project_event_types(self, event: CanonicalEvent) -> list[tuple[str, str | None, JSONMapping | None]]:
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
            return [("conversation.tool_call.started", _coerce_text(event, "tool_name"), _payload(event))]
        if event_type == "tool.call.delta":
            return [("conversation.tool_call.delta", _coerce_text(event, "delta"), _payload(event))]
        if event_type == "tool.call.completed":
            return [("conversation.tool_call.completed", _coerce_text(event, "result"), _payload(event))]
        if event_type == "task.progress":
            return [("conversation.progress", _coerce_text(event, "text"), _payload(event))]
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
        spec: tuple[str, str | None, JSONMapping | None],
    ) -> AgentStreamEvent:
        event_type, text, payload = spec
        turn_id = event.get("turn_id")
        item_id = event.get("item_id")
        if not isinstance(item_id, str):
            progress_item = event.get("item")
            if is_json_mapping(progress_item):
                progress_item_id = progress_item.get("id")
                if isinstance(progress_item_id, str) and progress_item_id:
                    item_id = progress_item_id
        run_id = _coerce_text(event, "run_id")
        return AgentStreamEvent(
            conversation_id=conversation_id,
            entry_id=str(uuid4()),
            source_event_id=_coerce_text(event, "event_id"),
            sequence=self.store.allocate_sequence(conversation_id),
            agent_id=_coerce_text(event, "agent_id"),
            run_id=run_id,
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
            target = _find_open_entry(
                entries,
                role=role,
                kind=kind,
                turn_id=frame.turn_id,
                item_id=frame.item_id,
                sequence=frame.sequence,
            )
            if target is None:
                target = AgentConversationEntry(
                    role=role,
                    kind=kind,
                    turn_id=frame.turn_id,
                    text=_entry_text(frame, kind=kind),
                    payload=_merge_entry_payload(None, frame=frame, kind=kind),
                    started_at=frame.created_at,
                    finished_at=None,
                )
                entries.append(target)
            else:
                target.text = f"{target.text}{_entry_text(frame, kind=kind)}"
                target.payload = _merge_entry_payload(target.payload, frame=frame, kind=kind)
            return

        target = _find_open_entry(
            entries,
            role=role,
            kind=kind,
            turn_id=frame.turn_id,
            item_id=frame.item_id,
            sequence=frame.sequence,
        )
        if target is None:
            entries.append(
                AgentConversationEntry(
                    role=role,
                    kind=kind,
                    turn_id=frame.turn_id,
                    text=_entry_text(frame, kind=kind),
                    payload=_merge_entry_payload(None, frame=frame, kind=kind),
                    started_at=frame.created_at,
                    finished_at=_entry_finished_at(frame, kind=kind),
                )
            )
            return
        frame_text = _entry_text(frame, kind=kind)
        if frame_text and not (
            target.text == frame_text or target.text.endswith(frame_text)
        ):
            target.text = f"{target.text}{frame_text}"
        target.payload = _merge_entry_payload(target.payload, frame=frame, kind=kind)
        target.finished_at = frame.created_at


def _coerce_text(event: JSONMapping, key: str) -> str | None:
    value = event.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _payload(event: JSONMapping) -> JSONObject | None:
    payload: JSONObject = {
        key: value for key, value in event.items() if key not in {"type", "timestamp", "agent_id"}
    }
    return payload or None


def _request_text(event: JSONMapping) -> str | None:
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
    item_id: str | None,
    sequence: int,
) -> AgentConversationEntry | None:
    # Only a directly-adjacent open block for the same stream target can
    # absorb more content. If any other event arrived in between, start a new
    # block to preserve the visible transcript chronology.
    if not entries:
        return None
    entry = entries[-1]
    if entry.role != role or entry.kind != kind:
        return None
    if entry.turn_id != turn_id or entry.finished_at is not None:
        return None
    if _entry_item_id(entry) != item_id:
        return None
    last_sequence = _entry_projection(entry.payload).get("last_sequence")
    if not isinstance(last_sequence, int) or last_sequence != sequence - 1:
        return None
    return entry


def _entry_text(
    frame: AgentStreamEvent,
    *,
    kind: Literal["message", "thinking", "tool_call"],
) -> str:
    if kind == "tool_call" and frame.type == "conversation.tool_call.started":
        return ""
    return frame.text or ""


def _entry_finished_at(
    frame: AgentStreamEvent,
    *,
    kind: Literal["message", "thinking", "tool_call"],
) -> str | None:
    if kind == "tool_call" and frame.type == "conversation.tool_call.started":
        return None
    return frame.created_at


def _merge_entry_payload(
    existing_payload: JSONMapping | None,
    *,
    frame: AgentStreamEvent,
    kind: Literal["message", "thinking", "tool_call"],
) -> JSONObject | None:
    payload: JSONObject = {}
    if is_json_mapping(existing_payload):
        payload.update(existing_payload)
    if is_json_mapping(frame.payload):
        payload.update(frame.payload)

    projection = _entry_projection(payload)
    projection["last_sequence"] = frame.sequence
    if frame.item_id is not None:
        projection["item_id"] = frame.item_id
    if frame.source_event_id is not None:
        projection["source_event_id"] = frame.source_event_id
    payload[_ENTRY_PROJECTION_KEY] = projection

    if kind == "tool_call":
        mcp_usage = _merge_mcp_usage(payload, fallback_tool_name=_tool_name_from_payload(payload, fallback=frame.text))
        if mcp_usage is not None:
            payload[_MCP_USAGE_KEY] = mcp_usage

    return payload or None


def _entry_projection(payload: JSONMapping | None) -> JSONObject:
    if not is_json_mapping(payload):
        return {}
    metadata = payload.get(_ENTRY_PROJECTION_KEY)
    if is_json_mapping(metadata):
        return dict(metadata)
    return {}


def _entry_item_id(entry: AgentConversationEntry) -> str | None:
    metadata = _entry_projection(entry.payload)
    item_id = metadata.get("item_id")
    if isinstance(item_id, str) and item_id:
        return item_id
    payload = entry.payload
    if not is_json_mapping(payload):
        return None
    raw_item_id = payload.get("item_id")
    if isinstance(raw_item_id, str) and raw_item_id:
        return raw_item_id
    return None


def _tool_name_from_payload(payload: JSONMapping | None, *, fallback: str | None) -> str | None:
    if is_json_mapping(payload):
        tool_name = payload.get("tool_name") or payload.get("name")
        if isinstance(tool_name, str) and tool_name.strip():
            return tool_name.strip()
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None


def _merge_mcp_usage(payload: JSONMapping, *, fallback_tool_name: str | None) -> JSONObject | None:
    usage_value = payload.get(_MCP_USAGE_KEY)
    usage = dict(usage_value) if is_json_mapping(usage_value) else {}

    tool_name = _tool_name_from_payload(payload, fallback=fallback_tool_name)
    arguments = payload.get("arguments")
    result = payload.get("result")
    error = payload.get("error")

    if tool_name is not None:
        usage["tool_name"] = tool_name
    if arguments not in (None, "", {}):
        usage["arguments"] = arguments
    if result not in (None, "", {}):
        usage["result"] = result
    if error not in (None, "", {}):
        usage["error"] = error

    if is_json_mapping(arguments):
        kind = arguments.get("kind")
        if isinstance(kind, str) and kind.strip():
            usage["kind"] = kind.strip()
        for source_key, target_key in (
            ("binding_id", "binding_id"),
            ("resource_name", "resource_name"),
            ("resource_uri", "resource_uri"),
            ("uri", "resource_uri"),
        ):
            value = arguments.get(source_key)
            if isinstance(value, str) and value.strip():
                usage[target_key] = value.strip()

    if is_json_mapping(result):
        for key in ("binding_id", "resource_name", "resource_uri"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                usage[key] = value.strip()

    if not _looks_like_mcp_usage(tool_name=tool_name, arguments=arguments, result=result):
        return dict(usage_value) if is_json_mapping(usage_value) else None

    usage.setdefault("transport", "mcp")
    return usage or None


def _looks_like_mcp_usage(*, tool_name: str | None, arguments: JSONValue, result: JSONValue) -> bool:
    if isinstance(tool_name, str) and tool_name.startswith("vibrant."):
        return True
    for candidate in (arguments, result):
        if is_json_mapping(candidate):
            marker = candidate.get("kind")
            if isinstance(marker, str) and marker.startswith("mcp."):
                return True
            for key in ("binding_id", "resource_name", "resource_uri", "uri"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return True
    return False
