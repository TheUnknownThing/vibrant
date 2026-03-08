"""Codex-backed implementation of the provider adapter interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import Any, Callable

from ...logging.ndjson_logger import CanonicalLogger, NativeLogger
from ...models.agent import AgentRecord
from ...models.wire import JsonRpcNotification
from ..base import CanonicalEvent, CanonicalEventHandler, ProviderAdapter, RuntimeMode
from .client import CodexClient

DEFAULT_CLIENT_INFO = {"name": "vibrant", "title": "Vibrant", "version": "0.1.0"}
NotificationHandler = Callable[[JsonRpcNotification], Any]
StderrHandler = Callable[[str], Any]


class CodexProviderAdapter(ProviderAdapter):
    """Provider adapter over :class:`CodexClient` with handshake and normalization."""

    def __init__(
        self,
        client: CodexClient | Any | None = None,
        *,
        cwd: str | None = None,
        codex_binary: str = "codex",
        codex_home: str | None = None,
        agent_record: AgentRecord | None = None,
        on_canonical_event: CanonicalEventHandler | None = None,
        on_raw_notification: NotificationHandler | None = None,
        on_stderr_line: StderrHandler | None = None,
        native_logger: NativeLogger | None = None,
        canonical_logger: CanonicalLogger | None = None,
        client_factory: Any | None = None,
    ) -> None:
        super().__init__(on_canonical_event=on_canonical_event)
        self.client = client
        self._client_factory = client_factory or CodexClient
        self._cwd = cwd
        self._codex_binary = codex_binary
        self._codex_home = codex_home
        self.agent_record = agent_record
        self.provider_thread_id: str | None = None
        self.thread_metadata: dict[str, Any] = {}
        self.current_turn_id: str | None = None
        self._session_started = False
        self._pending_requests: dict[int | str, dict[str, Any]] = {}
        self._on_raw_notification = on_raw_notification
        self._on_stderr_line = on_stderr_line
        self._native_logger = native_logger
        self._canonical_logger = canonical_logger

        self._ensure_loggers()
        self._bind_client_callbacks()

    @property
    def is_running(self) -> bool:
        return bool(self.client is not None and getattr(self.client, "is_running", False))

    def _bind_client_callbacks(self) -> None:
        if self.client is None:
            return
        if hasattr(self.client, "_on_notification"):
            self.client._on_notification = self._handle_notification
        if hasattr(self.client, "_on_stderr"):
            self.client._on_stderr = self._handle_stderr
        if hasattr(self.client, "_on_raw_event"):
            self.client._on_raw_event = self._handle_native_event

    def _ensure_client(self, cwd: str | None = None) -> Any:
        self._ensure_loggers(cwd)
        if self.client is None:
            resolved_cwd = cwd or self._cwd
            self.client = self._client_factory(
                cwd=resolved_cwd,
                codex_binary=self._codex_binary,
                codex_home=self._codex_home,
                on_notification=self._handle_notification,
                on_stderr=self._handle_stderr,
                on_raw_event=self._handle_native_event,
            )
        self._bind_client_callbacks()
        return self.client

    def _ensure_loggers(self, cwd: str | None = None) -> None:
        if self._native_logger is not None and self._canonical_logger is not None:
            return

        if self.agent_record is None:
            return

        base_cwd = Path(cwd or self._cwd or Path.cwd()).expanduser().resolve()
        native_path = self.agent_record.provider.native_event_log or str(
            base_cwd / ".vibrant" / "logs" / "providers" / "native" / f"{self.agent_record.agent_id}.ndjson"
        )
        canonical_path = self.agent_record.provider.canonical_event_log or str(
            base_cwd / ".vibrant" / "logs" / "providers" / "canonical" / f"{self.agent_record.agent_id}.ndjson"
        )

        self.agent_record.provider.native_event_log = native_path
        self.agent_record.provider.canonical_event_log = canonical_path

        if self._native_logger is None:
            self._native_logger = NativeLogger(native_path)
        if self._canonical_logger is None:
            self._canonical_logger = CanonicalLogger(canonical_path)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        client = self._ensure_client(cwd)
        if cwd is not None:
            self._cwd = cwd

        await client.start()

        client_info = {**DEFAULT_CLIENT_INFO, **dict(kwargs.pop("client_info", {}))}
        capabilities = {"experimentalApi": True, **dict(kwargs.pop("capabilities", {}))}
        initialize_params = {
            "clientInfo": client_info,
            "capabilities": capabilities,
            **kwargs,
        }
        result = await client.send_request("initialize", initialize_params)
        client.send_notification("initialized")
        self._session_started = True
        await self._emit_canonical("session.started", cwd=self._cwd, initialize_result=result)
        return result

    async def stop_session(self) -> None:
        if self.client is not None:
            await self.client.stop()
        self._session_started = False
        await self._emit_canonical("session.state.changed", state="stopped")

    async def start_thread(self, **kwargs: Any) -> Any:
        client = self._ensure_client()
        payload, runtime_mode, approval_policy, agent_record = self._build_thread_payload(kwargs)
        result = await client.send_request("thread/start", payload)
        self._persist_thread_metadata(
            result,
            runtime_mode=runtime_mode,
            approval_policy=approval_policy,
            agent_record=agent_record,
        )
        await self._emit_canonical(
            "thread.started",
            resumed=False,
            thread=self.thread_metadata or self._extract_thread_payload(result),
        )
        return result

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        client = self._ensure_client()
        payload, runtime_mode, approval_policy, agent_record = self._build_thread_payload(kwargs)
        payload = {"threadId": provider_thread_id, **payload}
        result = await client.send_request("thread/resume", payload)
        self._persist_thread_metadata(
            result,
            runtime_mode=runtime_mode,
            approval_policy=approval_policy,
            agent_record=agent_record,
            fallback_thread_id=provider_thread_id,
        )
        await self._emit_canonical(
            "thread.started",
            resumed=True,
            thread=self.thread_metadata or {"id": provider_thread_id},
        )
        return result

    async def start_turn(
        self,
        *,
        input_items: Sequence[Mapping[str, Any]],
        runtime_mode: RuntimeMode,
        approval_policy: str,
        **kwargs: Any,
    ) -> Any:
        client = self._ensure_client()
        runtime_mode = RuntimeMode(runtime_mode)
        payload = {
            "input": [dict(item) for item in input_items],
            "sandboxPolicy": runtime_mode.codex_turn_sandbox_policy,
            "approvalPolicy": approval_policy,
            **kwargs,
        }
        if self.provider_thread_id and "threadId" not in payload:
            payload["threadId"] = self.provider_thread_id
        return await client.send_request("turn/start", payload)

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        client = self._ensure_client()
        payload = dict(kwargs)
        if self.current_turn_id and "turnId" not in payload:
            payload["turnId"] = self.current_turn_id
        if self.provider_thread_id and "threadId" not in payload:
            payload["threadId"] = self.provider_thread_id
        return await client.send_request("turn/interrupt", payload or None)

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        client = self._ensure_client()
        client.respond_to_server_request(request_id, result=result, error=dict(error) if error else None)

        pending = self._pending_requests.pop(request_id, None)
        if pending is not None:
            await self._emit_canonical(
                "request.resolved",
                request_id=request_id,
                request_kind=pending["request_kind"],
                method=pending["method"],
                result=result,
                error=dict(error) if error else None,
            )
            if pending["request_kind"] == "user-input":
                await self._emit_canonical(
                    "user-input.resolved",
                    request_id=request_id,
                    method=pending["method"],
                    result=result,
                    error=dict(error) if error else None,
                )

    async def on_canonical_event(self, event: CanonicalEvent) -> None:
        if self.canonical_event_handler is not None:
            callback_result = self.canonical_event_handler(dict(event))
            if inspect.isawaitable(callback_result):
                await callback_result

    async def _handle_notification(self, notification: JsonRpcNotification) -> None:
        method = notification.method
        original_params = dict(notification.params or {})
        params = dict(original_params)

        if method.startswith("codex/event/"):
            await self._forward_raw_notification(JsonRpcNotification(method=method, params=original_params or None))
            return

        request_id = params.pop("_jsonrpc_id", None)
        if request_id is not None:
            await self._handle_server_request(method, request_id, params)
            await self._forward_raw_notification(JsonRpcNotification(method=method, params=original_params or None))
            return

        if method == "sessionConfigured":
            await self._emit_canonical("session.state.changed", state=params.get("state"), payload=params)
        elif method == "thread/started":
            thread_payload = params.get("thread", params)
            self._persist_thread_metadata({"thread": thread_payload}, fallback_thread_id=thread_payload.get("id"))
            await self._emit_canonical("thread.started", resumed=False, thread=thread_payload)
        elif method == "turn/started":
            turn_payload = params.get("turn", params)
            self.current_turn_id = turn_payload.get("id") or self.current_turn_id
            await self._emit_canonical("turn.started", turn=turn_payload)
        elif method == "item/agentMessage/delta":
            await self._emit_canonical(
                "content.delta",
                item_id=params.get("itemId") or params.get("item_id"),
                turn_id=params.get("turnId") or self.current_turn_id,
                delta=params.get("delta", ""),
                raw=params,
            )
        elif method == "item/completed":
            item_payload = params.get("item", params)
            await self._emit_canonical(
                "task.progress",
                item=item_payload,
                item_type=item_payload.get("type"),
            )
        elif method == "turn/completed":
            turn_payload = params.get("turn", params)
            self.current_turn_id = turn_payload.get("id") or self.current_turn_id
            await self._emit_canonical("turn.completed", turn=turn_payload, raw=params)
            await self._emit_canonical("task.completed", turn=turn_payload, raw=params)
        elif method in {"error", "turn/error"}:
            await self._emit_canonical("runtime.error", error=params.get("error", params), raw=params)

        await self._forward_raw_notification(JsonRpcNotification(method=method, params=original_params or None))

    async def _handle_server_request(self, method: str, request_id: int | str, params: dict[str, Any]) -> None:
        request_kind = self._classify_request_kind(method)
        self._pending_requests[request_id] = {
            "method": method,
            "request_kind": request_kind,
            "params": params,
        }
        await self._emit_canonical(
            "request.opened",
            request_id=request_id,
            request_kind=request_kind,
            method=method,
            params=params,
        )
        if request_kind == "user-input":
            await self._emit_canonical(
                "user-input.requested",
                request_id=request_id,
                method=method,
                params=params,
            )

    def _build_thread_payload(
        self,
        kwargs: dict[str, Any],
    ) -> tuple[dict[str, Any], RuntimeMode, str, AgentRecord | None]:
        data = dict(kwargs)
        runtime_mode = RuntimeMode(data.pop("runtime_mode", RuntimeMode.WORKSPACE_WRITE))
        approval_policy = data.pop("approval_policy", "never")
        agent_record = data.pop("agent_record", None) or self.agent_record
        cwd = data.pop("cwd", self._cwd)
        persist_extended_history = data.pop("persist_extended_history", True)
        extra_config = dict(data.pop("extra_config", {}) or {})
        model_provider = data.pop("model_provider", None)
        reasoning_effort = data.pop("reasoning_effort", None)
        reasoning_summary = data.pop("reasoning_summary", None)

        payload = {**data, "approvalPolicy": approval_policy, "sandbox": runtime_mode.codex_thread_sandbox}
        if cwd is not None:
            payload["cwd"] = str(Path(cwd))
        if model_provider is not None:
            payload["modelProvider"] = model_provider
        if reasoning_effort is not None:
            payload["reasoningEffort"] = reasoning_effort
        if reasoning_summary is not None:
            payload["reasoningSummary"] = reasoning_summary
        if persist_extended_history is not None:
            payload["persistExtendedHistory"] = persist_extended_history
        payload.update(extra_config)
        return payload, runtime_mode, approval_policy, agent_record

    def _persist_thread_metadata(
        self,
        result: Any,
        *,
        runtime_mode: RuntimeMode | None = None,
        approval_policy: str | None = None,
        agent_record: AgentRecord | None = None,
        fallback_thread_id: str | None = None,
    ) -> None:
        thread_payload = self._extract_thread_payload(result)
        thread_id = str(thread_payload.get("id") or fallback_thread_id or self.provider_thread_id or "") or None
        if thread_id is not None:
            self.provider_thread_id = thread_id
        self.thread_metadata = thread_payload

        record = agent_record or self.agent_record
        if record is None:
            return

        if thread_id is not None:
            record.provider.provider_thread_id = thread_id
            record.provider.resume_cursor = {"threadId": thread_id}
        if runtime_mode is not None:
            record.provider.runtime_mode = runtime_mode.codex_thread_sandbox
        if thread_payload.get("path"):
            record.provider.thread_path = str(thread_payload["path"])
        rollout_path = thread_payload.get("rolloutPath") or thread_payload.get("rollout_path")
        if rollout_path:
            record.provider.rollout_path = str(rollout_path)
        if approval_policy is not None:
            record.provider.resume_cursor = {**(record.provider.resume_cursor or {}), "approvalPolicy": approval_policy}

    def _extract_thread_payload(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        if isinstance(result.get("thread"), dict):
            return dict(result["thread"])
        return dict(result)

    def _classify_request_kind(self, method: str) -> str:
        if "requestUserInput" in method:
            return "user-input"
        if "requestApproval" in method:
            return "approval"
        return "request"

    async def _emit_canonical(self, event_type: str, **payload: Any) -> None:
        event: CanonicalEvent = {
            "type": event_type,
            "timestamp": _timestamp_now(),
            "provider": "codex",
        }
        if self.agent_record is not None:
            event["agent_id"] = self.agent_record.agent_id
            event["task_id"] = self.agent_record.task_id
        if self.provider_thread_id is not None:
            event["provider_thread_id"] = self.provider_thread_id
        event.update(payload)

        if self._canonical_logger is not None:
            self._canonical_logger.log_canonical(
                event_type,
                {key: value for key, value in event.items() if key not in {"type", "timestamp"}},
                timestamp=event["timestamp"],
            )
        await self.on_canonical_event(event)

    def _handle_native_event(self, raw_event: dict[str, Any]) -> None:
        if self._native_logger is None:
            self._ensure_loggers()
        if self._native_logger is not None:
            self._native_logger.log(
                raw_event.get("event", "raw"),
                raw_event.get("data") if isinstance(raw_event.get("data"), Mapping) else {"value": raw_event},
            )

    def _handle_stderr(self, line: str) -> None:
        client_has_raw_hook = bool(self.client is not None and getattr(self.client, "_on_raw_event", None) is not None)
        if self._native_logger is not None and not client_has_raw_hook:
            self._native_logger.log_stderr(line)
        if self._on_stderr_line is not None:
            callback_result = self._on_stderr_line(line)
            if inspect.isawaitable(callback_result):
                raise RuntimeError("on_stderr_line must be synchronous")

    async def _forward_raw_notification(self, notification: JsonRpcNotification) -> None:
        if self._on_raw_notification is None:
            return
        callback_result = self._on_raw_notification(notification)
        if inspect.isawaitable(callback_result):
            await callback_result


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
