"""High-level session orchestration for Codex threads.

Manages multiple provider-backed sessions and translates low-level
notifications into domain events that the TUI can subscribe to.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from .models import (
    AgentProviderMetadata,
    AgentRecord,
    AgentType,
    ApprovalMode,
    ItemInfo,
    ItemType,
    JsonRpcNotification,
    SessionConfig,
    ThreadInfo,
    ThreadStatus,
    TurnInfo,
    TurnRole,
    TurnStatus,
)
from .providers.base import RuntimeMode
from .providers.codex.adapter import CodexProviderAdapter
from .providers.codex.client import CodexClientError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain events emitted by the session manager
# ---------------------------------------------------------------------------

class SessionEvent:
    """Base for all session-level events."""
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id


class ThreadCreated(SessionEvent):
    def __init__(self, thread_id: str, thread: ThreadInfo) -> None:
        super().__init__(thread_id)
        self.thread = thread


class ThreadStatusChanged(SessionEvent):
    def __init__(self, thread_id: str, status: ThreadStatus, error: str | None = None) -> None:
        super().__init__(thread_id)
        self.status = status
        self.error = error


class TurnStarted(SessionEvent):
    def __init__(self, thread_id: str, turn_id: str) -> None:
        super().__init__(thread_id)
        self.turn_id = turn_id


class TurnCompleted(SessionEvent):
    def __init__(self, thread_id: str, turn_id: str) -> None:
        super().__init__(thread_id)
        self.turn_id = turn_id


class ItemAdded(SessionEvent):
    def __init__(self, thread_id: str, turn_id: str, item: ItemInfo) -> None:
        super().__init__(thread_id)
        self.turn_id = turn_id
        self.item = item


class StreamingDelta(SessionEvent):
    """Emitted on each streaming text delta for live UI updates."""
    def __init__(self, thread_id: str, turn_id: str, item_id: str, accumulated_text: str) -> None:
        super().__init__(thread_id)
        self.turn_id = turn_id
        self.item_id = item_id
        self.accumulated_text = accumulated_text


class ApprovalRequested(SessionEvent):
    def __init__(
        self, thread_id: str, request_id: str, jsonrpc_id: int | str,
        method: str, params: dict[str, Any],
    ) -> None:
        super().__init__(thread_id)
        self.request_id = request_id
        self.jsonrpc_id = jsonrpc_id
        self.method = method
        self.params = params


EventListener = Callable[[SessionEvent], Coroutine[Any, Any, None]]


def _map_approval_mode(mode: ApprovalMode) -> tuple[RuntimeMode, str]:
    """Convert app approval settings into provider runtime settings."""
    if mode in {ApprovalMode.SUGGEST, ApprovalMode.AUTO_EDIT}:
        return RuntimeMode.WORKSPACE_WRITE, 'on-request'
    return RuntimeMode.FULL_ACCESS, 'never'


# ---------------------------------------------------------------------------
# Streaming item accumulator — tracks in-progress items and their deltas
# ---------------------------------------------------------------------------

class _StreamingItem:
    """Tracks an in-progress item being streamed from the server."""
    __slots__ = ('item_id', 'item_type', 'text_parts', 'metadata')

    def __init__(self, item_id: str, item_type: str) -> None:
        self.item_id = item_id
        self.item_type = item_type
        self.text_parts: list[str] = []
        self.metadata: dict[str, Any] = {}

    @property
    def accumulated_text(self) -> str:
        return ''.join(self.text_parts)

    def append_delta(self, delta: str) -> None:
        self.text_parts.append(delta)


class SessionManager:
    """Orchestrates multiple provider-backed sessions (one per thread)."""

    def __init__(self, adapter_factory: Callable[..., CodexProviderAdapter] | None = None) -> None:
        self._adapter_factory = adapter_factory or CodexProviderAdapter
        self._adapters: dict[str, CodexProviderAdapter] = {}
        self._clients: dict[str, Any] = {}
        self._configs: dict[str, SessionConfig] = {}
        self._agent_records: dict[str, AgentRecord] = {}
        self._threads: dict[str, ThreadInfo] = {}
        self._current_turns: dict[str, str] = {}
        self._streaming_items: dict[str, _StreamingItem] = {}
        self._listeners: list[EventListener] = []

    # ------------------------------------------------------------------
    # Event system
    # ------------------------------------------------------------------

    def add_listener(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: EventListener) -> None:
        self._listeners.remove(listener)

    async def _emit(self, event: SessionEvent) -> None:
        for listener in self._listeners:
            try:
                await listener(event)
            except Exception:
                logger.exception('Error in session event listener')

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def create_session(self, config: SessionConfig) -> ThreadInfo:
        """Create a new thread and initialize a provider-backed session."""
        thread_id = str(uuid.uuid4())
        thread = ThreadInfo(
            id=thread_id,
            status=ThreadStatus.ACTIVE,
            model=config.model,
            cwd=config.cwd or os.getcwd(),
        )
        self._threads[thread_id] = thread
        self._configs[thread_id] = config

        agent_record = self._build_agent_record(thread_id, config)
        self._agent_records[thread_id] = agent_record

        adapter = self._adapter_factory(
            cwd=config.cwd,
            codex_binary=config.codex_binary,
            agent_record=agent_record,
            on_raw_notification=lambda n: self._on_notification(thread_id, n),
            on_stderr_line=lambda line: logger.debug('[%s] stderr: %s', thread_id[:8], line),
        )

        try:
            await adapter.start_session(cwd=config.cwd)
            self._adapters[thread_id] = adapter
            if adapter.client is not None:
                self._clients[thread_id] = adapter.client

            runtime_mode, approval_policy = _map_approval_mode(config.approval_mode)
            result = await adapter.start_thread(
                model=config.model,
                cwd=config.cwd or os.getcwd(),
                runtime_mode=runtime_mode,
                approval_policy=approval_policy,
            )

            if adapter.provider_thread_id:
                thread.codex_thread_id = adapter.provider_thread_id
            elif isinstance(result, dict):
                thread_obj = result.get('thread', result)
                codex_tid = thread_obj.get('id') or result.get('threadId')
                if codex_tid:
                    thread.codex_thread_id = str(codex_tid)

            thread.status = ThreadStatus.IDLE
            thread.updated_at = datetime.now(timezone.utc)
            await self._emit(ThreadCreated(thread_id, thread))
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.IDLE))
            logger.info('Session created: %s (codex_thread=%s)', thread_id[:8], thread.codex_thread_id)
            return thread

        except Exception as exc:
            thread.status = ThreadStatus.ERROR
            thread.error_message = str(exc)
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.ERROR, str(exc)))
            try:
                await adapter.stop_session()
            except Exception:
                logger.exception('Failed to stop adapter after create_session error')
            self._adapters.pop(thread_id, None)
            self._clients.pop(thread_id, None)
            raise

    async def resume_session(self, thread_id: str, codex_thread_id: str, config: SessionConfig) -> ThreadInfo:
        """Resume an existing provider thread."""
        thread = self._threads.get(thread_id)
        if not thread:
            thread = ThreadInfo(
                id=thread_id,
                codex_thread_id=codex_thread_id,
                status=ThreadStatus.ACTIVE,
                model=config.model,
                cwd=config.cwd or os.getcwd(),
            )
            self._threads[thread_id] = thread
        self._configs[thread_id] = config

        agent_record = self._agent_records.get(thread_id) or self._build_agent_record(thread_id, config)
        self._agent_records[thread_id] = agent_record

        adapter = self._adapter_factory(
            cwd=config.cwd,
            codex_binary=config.codex_binary,
            agent_record=agent_record,
            on_raw_notification=lambda n: self._on_notification(thread_id, n),
            on_stderr_line=lambda line: logger.debug('[%s] stderr: %s', thread_id[:8], line),
        )

        try:
            await adapter.start_session(cwd=config.cwd)
            self._adapters[thread_id] = adapter
            if adapter.client is not None:
                self._clients[thread_id] = adapter.client

            runtime_mode, approval_policy = _map_approval_mode(config.approval_mode)
            await adapter.resume_thread(
                codex_thread_id,
                runtime_mode=runtime_mode,
                approval_policy=approval_policy,
            )

            thread.codex_thread_id = adapter.provider_thread_id or codex_thread_id
            thread.status = ThreadStatus.IDLE
            thread.updated_at = datetime.now(timezone.utc)
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.IDLE))
            return thread

        except Exception as exc:
            thread.status = ThreadStatus.ERROR
            thread.error_message = str(exc)
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.ERROR, str(exc)))
            try:
                await adapter.stop_session()
            except Exception:
                logger.exception('Failed to stop adapter after resume_session error')
            self._adapters.pop(thread_id, None)
            self._clients.pop(thread_id, None)
            raise

    async def send_message(self, thread_id: str, text: str) -> None:
        """Send a user message (starts a new turn)."""
        adapter = self._adapters.get(thread_id)
        thread = self._threads.get(thread_id)
        if not adapter or not thread:
            raise CodexClientError(f'No active session for thread {thread_id}')
        if not thread.codex_thread_id:
            raise CodexClientError('Thread has no codex thread ID')

        user_turn = TurnInfo(
            role=TurnRole.USER,
            status=TurnStatus.COMPLETED,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            items=[ItemInfo(type=ItemType.TEXT, content=text)],
        )
        thread.turns.append(user_turn)

        assistant_turn = TurnInfo(
            role=TurnRole.ASSISTANT,
            status=TurnStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        thread.turns.append(assistant_turn)
        self._current_turns[thread_id] = assistant_turn.id
        thread.status = ThreadStatus.RUNNING
        thread.updated_at = datetime.now(timezone.utc)

        await self._emit(ItemAdded(thread_id, user_turn.id, user_turn.items[0]))
        await self._emit(TurnStarted(thread_id, assistant_turn.id))
        await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.RUNNING))

        try:
            config = self._configs.get(thread_id) or SessionConfig(model=thread.model or 'gpt-5.3-codex', cwd=thread.cwd)
            runtime_mode, approval_policy = _map_approval_mode(config.approval_mode)
            await adapter.start_turn(
                threadId=thread.codex_thread_id,
                input_items=[{'type': 'text', 'text': text, 'text_elements': []}],
                runtime_mode=runtime_mode,
                approval_policy=approval_policy,
            )
        except Exception as exc:
            assistant_turn.status = TurnStatus.ERROR
            thread.status = ThreadStatus.ERROR
            thread.error_message = str(exc)
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.ERROR, str(exc)))

    async def approve_request(self, thread_id: str, jsonrpc_id: int | str, approved: bool) -> None:
        """Respond to an approval request from the server."""
        adapter = self._adapters.get(thread_id)
        if not adapter:
            return
        await adapter.respond_to_request(jsonrpc_id, result={'approved': approved})

    async def stop_session(self, thread_id: str) -> None:
        """Gracefully stop a session."""
        adapter = self._adapters.pop(thread_id, None)
        self._clients.pop(thread_id, None)
        if adapter:
            await adapter.stop_session()
        thread = self._threads.get(thread_id)
        if thread:
            thread.status = ThreadStatus.STOPPED
            thread.updated_at = datetime.now(timezone.utc)
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.STOPPED))

    async def stop_all(self) -> None:
        """Stop all active sessions."""
        thread_ids = list(self._adapters.keys())
        await asyncio.gather(*(self.stop_session(tid) for tid in thread_ids))

    def get_thread(self, thread_id: str) -> ThreadInfo | None:
        return self._threads.get(thread_id)

    def list_threads(self) -> list[ThreadInfo]:
        return list(self._threads.values())

    def get_active_thread_ids(self) -> list[str]:
        return [tid for tid, adapter in self._adapters.items() if adapter.is_running]

    def get_agent_record(self, thread_id: str) -> AgentRecord | None:
        return self._agent_records.get(thread_id)

    def get_provider_log_paths(self, thread_id: str) -> tuple[str | None, str | None]:
        agent_record = self._agent_records.get(thread_id)
        if agent_record is None:
            return (None, None)
        return (agent_record.provider.native_event_log, agent_record.provider.canonical_event_log)

    def _build_agent_record(self, thread_id: str, config: SessionConfig) -> AgentRecord:
        cwd = Path(config.cwd or os.getcwd()).expanduser().resolve()
        native_log = cwd / '.vibrant' / 'logs' / 'providers' / 'native' / f'{thread_id}.ndjson'
        canonical_log = cwd / '.vibrant' / 'logs' / 'providers' / 'canonical' / f'{thread_id}.ndjson'
        return AgentRecord(
            agent_id=thread_id,
            task_id=thread_id,
            type=AgentType.CODE,
            branch=f'vibrant/{thread_id[:8]}',
            worktree_path=str(cwd),
            started_at=datetime.now(timezone.utc),
            provider=AgentProviderMetadata(
                native_event_log=str(native_log),
                canonical_event_log=str(canonical_log),
            ),
        )

    # ------------------------------------------------------------------
    # Notification handling
    # ------------------------------------------------------------------

    async def _on_notification(self, thread_id: str, notification: JsonRpcNotification) -> None:
        """Process notifications from the codex app-server.

        The server sends many duplicate events via different channels:
        - ``item/*``  — canonical structured events (we use these)
        - ``codex/event/*`` — legacy/compat events (we ignore these)

        Within ``item/*``, the lifecycle is:
            item/started → item/.../delta (many) → item/completed

        For streaming we accumulate deltas and only finalize on item/completed.
        """
        thread = self._threads.get(thread_id)
        if not thread:
            return

        method = notification.method
        params = notification.params or {}

        # ── Ignore legacy/duplicate event channels ──
        if method.startswith("codex/event/"):
            return

        # ── Handle approval / user input server requests ──
        jsonrpc_id = params.pop("_jsonrpc_id", None)
        if jsonrpc_id is not None and "requestApproval" in method:
            req_id = str(uuid.uuid4())
            await self._emit(ApprovalRequested(
                thread_id, req_id, jsonrpc_id, method, params
            ))
            return

        # ── Turn lifecycle ──
        if method == "turn/started":
            turn_data = params.get("turn", params)
            turn_id = turn_data.get("id", str(uuid.uuid4()))
            current_turn_id = self._current_turns.get(thread_id)
            if current_turn_id:
                for turn in thread.turns:
                    if turn.id == current_turn_id:
                        turn.id = turn_id
                        self._current_turns[thread_id] = turn_id
                        break
            return

        if method == "turn/completed":
            current_turn_id = self._current_turns.pop(thread_id, None)
            if current_turn_id:
                for turn in thread.turns:
                    if turn.id == current_turn_id:
                        turn.status = TurnStatus.COMPLETED
                        turn.completed_at = datetime.now(timezone.utc)
                        break
            thread.status = ThreadStatus.IDLE
            thread.updated_at = datetime.now(timezone.utc)
            await self._emit(TurnCompleted(thread_id, current_turn_id or ""))
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.IDLE))
            return

        if method == "turn/error":
            current_turn_id = self._current_turns.pop(thread_id, None)
            error_msg = params.get("error", {}).get("message", "Turn failed")
            if current_turn_id:
                for turn in thread.turns:
                    if turn.id == current_turn_id:
                        turn.status = TurnStatus.ERROR
                        turn.completed_at = datetime.now(timezone.utc)
                        break
            thread.status = ThreadStatus.IDLE
            thread.error_message = error_msg
            thread.updated_at = datetime.now(timezone.utc)
            item = ItemInfo(type=ItemType.TEXT, content=f"❌ Error: {error_msg}")
            self._add_item_to_current_turn(thread_id, item)
            await self._emit(ItemAdded(thread_id, current_turn_id or "", item))
            await self._emit(ThreadStatusChanged(thread_id, ThreadStatus.IDLE))
            return

        # ── Item lifecycle ──

        # item/started — begin tracking a new streaming item
        if method == "item/started":
            item_data = params.get("item", {})
            item_id = item_data.get("id", str(uuid.uuid4()))
            item_type = item_data.get("type", "unknown")
            # Skip user messages — we already add them in send_message()
            if item_type.lower() == "usermessage":
                return
            self._streaming_items[item_id] = _StreamingItem(item_id, item_type)
            return

        # item/*/delta — accumulate streaming text
        if "/delta" in method.lower() or "Delta" in method:
            delta_text = params.get("delta", "")
            item_id = params.get("itemId", "")
            if item_id and item_id in self._streaming_items:
                si = self._streaming_items[item_id]
                si.append_delta(delta_text)
                current_turn_id = self._current_turns.get(thread_id, "")
                await self._emit(StreamingDelta(
                    thread_id, current_turn_id, item_id, si.accumulated_text
                ))
            return

        # item/completed — finalize the item and add to thread
        if method == "item/completed":
            item_data = params.get("item", {})
            item_id = item_data.get("id", "")
            item_type = item_data.get("type", "unknown").lower()

            # Skip user messages — we already track them in send_message()
            if item_type == "usermessage":
                self._streaming_items.pop(item_id, None)
                return

            # Get accumulated text from streaming, or extract from completed data
            si = self._streaming_items.pop(item_id, None)

            if item_type == "agentmessage":
                # Agent text message
                text = item_data.get("text", "")
                if not text and si:
                    text = si.accumulated_text
                if text:
                    item = ItemInfo(
                        id=item_id,
                        type=ItemType.TEXT,
                        content=text,
                    )
                    self._add_item_to_current_turn(thread_id, item)
                    current_turn_id = self._current_turns.get(thread_id, "")
                    await self._emit(ItemAdded(thread_id, current_turn_id, item))

            elif item_type == "reasoning":
                # Reasoning — show fully accumulated streaming text, fall back to summary
                summary = item_data.get("summary", [])
                summary_text = "\n".join(summary) if isinstance(summary, list) else str(summary)
                full_text = si.accumulated_text if si else ""
                # Prefer full streaming text, fall back to summary
                text = full_text or summary_text
                if text:
                    item = ItemInfo(
                        id=item_id,
                        type=ItemType.TEXT,
                        content=text,
                        metadata={"is_reasoning": True},
                    )
                    self._add_item_to_current_turn(thread_id, item)
                    current_turn_id = self._current_turns.get(thread_id, "")
                    await self._emit(ItemAdded(thread_id, current_turn_id, item))

            elif item_type in ("commandexecution", "command_execution"):
                cmd = item_data.get("command", "")
                output = item_data.get("aggregatedOutput", "") or item_data.get("output", "")
                exit_code = item_data.get("exitCode")
                duration = item_data.get("durationMs")
                cwd = item_data.get("cwd", "")

                content = cmd
                item = ItemInfo(
                    id=item_id,
                    type=ItemType.COMMAND,
                    content=content,
                    metadata={
                        "command": cmd,
                        "output": output or "",
                        "exit_code": exit_code,
                        "duration_ms": duration,
                        "cwd": cwd,
                    },
                )
                self._add_item_to_current_turn(thread_id, item)
                current_turn_id = self._current_turns.get(thread_id, "")
                await self._emit(ItemAdded(thread_id, current_turn_id, item))

            elif item_type in ("filechange", "file_change"):
                filename = item_data.get("filename", item_data.get("path", ""))
                item = ItemInfo(
                    id=item_id,
                    type=ItemType.FILE_CHANGE,
                    content=str(filename),
                    metadata={"raw": item_data},
                )
                self._add_item_to_current_turn(thread_id, item)
                current_turn_id = self._current_turns.get(thread_id, "")
                await self._emit(ItemAdded(thread_id, current_turn_id, item))

            elif item_type in ("fileread", "file_read"):
                filename = item_data.get("filename", item_data.get("path", ""))
                item = ItemInfo(
                    id=item_id,
                    type=ItemType.FILE_READ,
                    content=str(filename),
                    metadata={"raw": item_data},
                )
                self._add_item_to_current_turn(thread_id, item)
                current_turn_id = self._current_turns.get(thread_id, "")
                await self._emit(ItemAdded(thread_id, current_turn_id, item))

            else:
                # Unknown item type — still register it but as unknown
                text = ""
                if si:
                    text = si.accumulated_text
                if not text:
                    text = str(item_data.get("text", item_data.get("content", "")))[:300]
                if text:
                    item = ItemInfo(
                        id=item_id,
                        type=ItemType.UNKNOWN,
                        content=text,
                        metadata={"item_type": item_type},
                    )
                    self._add_item_to_current_turn(thread_id, item)
                    current_turn_id = self._current_turns.get(thread_id, "")
                    await self._emit(ItemAdded(thread_id, current_turn_id, item))
            return

        # ── Catch-all: ignore unknown item/* methods silently ──
        # (e.g. item/reasoning/summaryTextDelta is handled by the delta branch above)

    def _add_item_to_current_turn(self, thread_id: str, item: ItemInfo) -> None:
        """Append an item to the current assistant turn."""
        thread = self._threads.get(thread_id)
        if not thread:
            return
        current_turn_id = self._current_turns.get(thread_id)
        if current_turn_id:
            for turn in thread.turns:
                if turn.id == current_turn_id:
                    turn.items.append(item)
                    return
        # Fallback: append to last turn
        if thread.turns:
            thread.turns[-1].items.append(item)
