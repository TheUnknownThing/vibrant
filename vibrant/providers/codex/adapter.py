"""Codex-backed implementation of the provider adapter interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import Any, Callable

from ...runtime_logging.ndjson_logger import CanonicalLogger, NativeLogger
from ...models.agent import AgentRecord, ProviderResumeHandle
from ...models.wire import JsonRpcNotification
from ..base import CanonicalEvent, CanonicalEventHandler, CodexAuthConfig, CodexAuthMode, ProviderAdapter, RuntimeMode
from ..invocation import ProviderInvocationPlan
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
        launch_args: Sequence[str] | None = None,
        codex_home: str | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
        resume_thread_id: str | None = None,
        agent_record: AgentRecord | None = None,
        on_canonical_event: CanonicalEventHandler | None = None,
        on_raw_notification: NotificationHandler | None = None,
        on_stderr_line: StderrHandler | None = None,
        native_logger: NativeLogger | None = None,
        canonical_logger: CanonicalLogger | None = None,
        client_factory: Any | None = None,
        **_: Any,
    ) -> None:
        super().__init__(on_canonical_event=on_canonical_event)
        self.client = client
        self._client_factory = client_factory or CodexClient
        self._cwd = cwd
        self._codex_binary = codex_binary
        self._launch_args = list(launch_args or [])
        self._codex_home = codex_home
        self._invocation_plan = invocation_plan
        self.agent_record = agent_record
        self.provider_thread_id: str | None = None
        self.thread_metadata: dict[str, Any] = {}
        self.current_turn_id: str | None = None
        self._item_states: dict[str, dict[str, Any]] = {}
        self._session_started = False
        self._pending_requests: dict[int | str, dict[str, Any]] = {}
        self._awaiting_thread_started_for: str | None = None
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
            kwargs: dict[str, Any] = {
                "cwd": resolved_cwd,
                "codex_binary": self._codex_binary,
                "on_notification": self._handle_notification,
                "on_stderr": self._handle_stderr,
                "on_raw_event": self._handle_native_event,
            }
            launch_args = self._effective_launch_args()
            if launch_args:
                kwargs["launch_args"] = launch_args
            launch_env = self._effective_launch_env()
            if launch_env:
                kwargs["launch_env"] = launch_env
            if self._codex_home is not None:
                kwargs["codex_home"] = self._codex_home
            self.client = self._client_factory(**kwargs)
        self._bind_client_callbacks()
        return self.client

    def _ensure_loggers(self, cwd: str | None = None) -> None:
        if self._native_logger is not None and self._canonical_logger is not None:
            return

        if self.agent_record is None:
            return

        base_cwd = Path(cwd or self._cwd or Path.cwd()).expanduser().resolve()
        native_path = self.agent_record.provider.native_event_log or str(
            base_cwd / ".vibrant" / "logs" / "providers" / "native" / f"{self.agent_record.identity.run_id}.ndjson"
        )
        canonical_path = self.agent_record.provider.canonical_event_log or str(
            base_cwd / ".vibrant" / "logs" / "providers" / "canonical" / f"{self.agent_record.identity.run_id}.ndjson"
        )

        self.agent_record.provider.native_event_log = native_path
        self.agent_record.provider.canonical_event_log = canonical_path

        if self._native_logger is None:
            self._native_logger = NativeLogger(native_path)
        if self._canonical_logger is None:
            self._canonical_logger = CanonicalLogger(canonical_path)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        auth_config = _coerce_auth_config(kwargs.pop("auth_config", None) or kwargs.pop("auth", None))
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
        initialize_params.update(self._invocation_scope_options("initialize"))
        result = await client.send_request("initialize", initialize_params)
        client.send_notification("initialized")
        self._session_started = True
        await self._emit_canonical(
            "session.started",
            cwd=self._cwd,
            provider_payload=_coerce_provider_payload(result, field_name="initialize_result"),
        )

        if auth_config is not None and auth_config.mode is not CodexAuthMode.SYSTEM:
            await self.login(auth_config)
        return result

    async def stop_session(self) -> None:
        if self.client is not None:
            await self.client.stop()
        self._session_started = False
        self._pending_requests.clear()
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
        if self.provider_thread_id:
            self._awaiting_thread_started_for = self.provider_thread_id
        thread_payload = self.thread_metadata or self._extract_thread_payload(result)
        await self._emit_canonical(
            "thread.started",
            resumed=False,
            thread=thread_payload,
            thread_path=_thread_path_from_payload(thread_payload),
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
        if self.provider_thread_id:
            self._awaiting_thread_started_for = self.provider_thread_id
        thread_payload = self.thread_metadata or {"id": provider_thread_id}
        await self._emit_canonical(
            "thread.started",
            resumed=True,
            thread=thread_payload,
            thread_path=_thread_path_from_payload(thread_payload),
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
        effort = kwargs.pop("effort", kwargs.pop("reasoning_effort", None))
        summary = kwargs.pop("summary", kwargs.pop("reasoning_summary", None))
        payload = {
            "input": [dict(item) for item in input_items],
            "sandboxPolicy": runtime_mode.codex_turn_sandbox_policy,
            "approvalPolicy": approval_policy,
            **kwargs,
        }
        if effort is not None:
            payload["effort"] = effort
        if summary is not None:
            payload["summary"] = summary
        payload.update(self._invocation_scope_options("turn"))
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

    async def send_request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send an arbitrary Codex management request."""

        client = self._ensure_client(kwargs.pop("cwd", None))
        timeout = kwargs.pop("timeout", None)
        if kwargs:
            raise TypeError(f"Unexpected keyword arguments: {', '.join(sorted(kwargs))}")
        request_params = dict(params) if params is not None else None
        if timeout is None:
            return await client.send_request(method, request_params)
        return await client.send_request(method, request_params, timeout=float(timeout))

    async def read_account(self, *, refresh_token: bool = False) -> Any:
        """Return Codex auth/account state via ``account/read``."""

        return await self.send_request("account/read", {"refreshToken": bool(refresh_token)})

    async def login(self, auth_config: CodexAuthConfig) -> Any:
        """Log in to Codex using ``account/login/start``."""

        params = auth_config.to_login_params()
        if params is None:
            return await self.read_account(refresh_token=False)
        return await self.send_request("account/login/start", params)

    async def logout(self) -> Any:
        """Logout via ``account/logout``."""

        return await self.send_request("account/logout", None)

    async def list_skills(
        self,
        *,
        cwds: Sequence[str],
        force_reload: bool = False,
        per_cwd_extra_user_roots: Sequence[Mapping[str, Any]] | None = None,
    ) -> Any:
        """List skills via ``skills/list``."""

        params: dict[str, Any] = {
            "cwds": [str(Path(cwd)) for cwd in cwds],
            "forceReload": bool(force_reload),
        }
        if per_cwd_extra_user_roots is not None:
            params["perCwdExtraUserRoots"] = [dict(entry) for entry in per_cwd_extra_user_roots]
        return await self.send_request("skills/list", params)

    async def write_skill_config(self, *, path: str, enabled: bool) -> Any:
        """Enable/disable a skill via ``skills/config/write``."""

        return await self.send_request(
            "skills/config/write",
            {"path": str(Path(path)), "enabled": bool(enabled)},
        )

    async def reload_mcp_servers(self) -> Any:
        """Reload MCP server configuration from disk via ``config/mcpServer/reload``."""

        return await self.send_request("config/mcpServer/reload", None)

    async def list_mcp_server_status(self, *, cursor: str | None = None, limit: int | None = None) -> Any:
        """List MCP server status via ``mcpServerStatus/list``."""

        params: dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = int(limit)
        return await self.send_request("mcpServerStatus/list", params or None)

    async def start_mcp_oauth_login(self, *, name: str) -> Any:
        """Start an MCP OAuth login via ``mcpServer/oauth/login``."""

        return await self.send_request("mcpServer/oauth/login", {"name": name})

    async def detect_external_agent_config(
        self,
        *,
        include_home: bool = True,
        cwds: Sequence[str] | None = None,
    ) -> Any:
        """Detect migratable external-agent config via ``externalAgentConfig/detect``."""

        params: dict[str, Any] = {"includeHome": bool(include_home)}
        if cwds is not None:
            params["cwds"] = [str(Path(cwd)) for cwd in cwds]
        return await self.send_request("externalAgentConfig/detect", params)

    async def import_external_agent_config(self, *, migration_items: Sequence[Mapping[str, Any]]) -> Any:
        """Import external-agent config items via ``externalAgentConfig/import``."""

        return await self.send_request(
            "externalAgentConfig/import",
            {"migrationItems": [dict(item) for item in migration_items]},
        )

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
                error_message=_error_message(dict(error) if error else None),
            )
            if pending["request_kind"] == "user-input":
                await self._emit_canonical(
                    "user-input.resolved",
                    request_id=request_id,
                    method=pending["method"],
                    result=result,
                    error=dict(error) if error else None,
                    error_message=_error_message(dict(error) if error else None),
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

        request_id = params.pop("_jsonrpc_id", None)
        if request_id is not None:
            await self._handle_server_request(method, request_id, params)
            await self._forward_raw_notification(JsonRpcNotification(method=method, params=original_params or None))
            return

        if method == "sessionConfigured":
            await self._emit_canonical(
                "session.state.changed",
                state=params.get("state"),
                provider_payload=dict(params),
            )
        elif method == "thread/started":
            thread_payload = params.get("thread", params)
            self._persist_thread_metadata({"thread": thread_payload}, fallback_thread_id=thread_payload.get("id"))
            thread_id = thread_payload.get("id") if isinstance(thread_payload, Mapping) else None
            if thread_id and self._awaiting_thread_started_for == str(thread_id):
                self._awaiting_thread_started_for = None
            else:
                await self._emit_canonical(
                    "thread.started",
                    resumed=False,
                    thread=thread_payload,
                    thread_path=_thread_path_from_payload(thread_payload),
                )
        elif method == "turn/started":
            turn_payload = params.get("turn", params)
            self.current_turn_id = turn_payload.get("id") or self.current_turn_id
            await self._emit_canonical(
                "turn.started",
                turn_id=_turn_id_from_payload(turn_payload),
                turn_status=_turn_status_from_payload(turn_payload),
                turn=turn_payload,
            )
        elif method == "item/started":
            item_payload = _sanitize_progress_item(params.get("item", params))
            turn_id = params.get("turnId") or self.current_turn_id
            item_id = self._remember_item_state(item_payload, turn_id=turn_id)
            item_type = _normalize_item_type(item_payload.get("type"))
            if item_type == "commandexecution":
                await self._emit_command_started(item_payload, turn_id=turn_id, raw=params, item_id=item_id)
        elif method == "item/agentMessage/delta":
            await self._emit_canonical(
                "content.delta",
                item_id=params.get("itemId") or params.get("item_id"),
                turn_id=params.get("turnId") or self.current_turn_id,
                delta=params.get("delta", ""),
                provider_payload=dict(params),
            )
        elif method == "item/reasoning/summaryTextDelta":
            await self._handle_reasoning_delta(
                item_id=params.get("itemId") or params.get("item_id"),
                turn_id=params.get("turnId") or self.current_turn_id,
                delta=params.get("delta", ""),
                summary_index=params.get("summaryIndex"),
                raw=params,
                redacted_for_log=False,
            )
            await self._emit_canonical(
                "reasoning.summary.delta",
                item_id=params.get("itemId") or params.get("item_id"),
                turn_id=params.get("turnId") or self.current_turn_id,
                delta=params.get("delta", ""),
                summary_index=params.get("summaryIndex"),
                provider_payload=dict(params),
            )
        elif method == "item/reasoning/summaryPartAdded":
            item_id = params.get("itemId") or params.get("item_id")
            if isinstance(item_id, str) and item_id:
                self._ensure_item_state(item_id)["pending_reasoning_break"] = True
        elif method == "item/reasoning/textDelta":
            await self._handle_reasoning_delta(
                item_id=params.get("itemId") or params.get("item_id"),
                turn_id=params.get("turnId") or self.current_turn_id,
                delta=params.get("delta", ""),
                raw=params,
                redacted_for_log=True,
            )
        elif method == "item/commandExecution/outputDelta":
            await self._handle_command_output_delta(
                item_id=params.get("itemId") or params.get("item_id"),
                turn_id=params.get("turnId") or self.current_turn_id,
                delta=params.get("delta", ""),
                raw=params,
            )
        elif method == "item/completed":
            item_payload = params.get("item", params)
            item_payload = _sanitize_progress_item(item_payload)
            turn_id = params.get("turnId") or self.current_turn_id
            await self._handle_item_completed(item_payload, turn_id=turn_id, raw=params)
        elif method == "turn/completed":
            turn_payload = params.get("turn", params)
            self.current_turn_id = turn_payload.get("id") or self.current_turn_id
            turn_id = _turn_id_from_payload(turn_payload)
            turn_status = _turn_status_from_payload(turn_payload)
            await self._emit_canonical(
                "turn.completed",
                turn_id=turn_id,
                turn_status=turn_status,
                turn=turn_payload,
                provider_payload=dict(params),
            )
            await self._emit_canonical(
                "task.completed",
                turn_id=turn_id,
                turn_status=turn_status,
                turn=turn_payload,
                provider_payload=dict(params),
            )
            self._item_states.clear()
            self._pending_requests.clear()
        elif method in {"error", "turn/error"}:
            error_payload = params.get("error", params)
            await self._emit_canonical(
                "runtime.error",
                error=error_payload,
                error_message=_error_message(error_payload),
                error_code=_error_code(error_payload),
                provider_payload=dict(params),
            )
            self._item_states.clear()
            self._pending_requests.clear()

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
        payload.update(self._invocation_scope_options("thread"))
        return payload, runtime_mode, approval_policy, agent_record

    def _effective_launch_args(self) -> list[str]:
        launch_args = list(self._launch_args)
        if self._invocation_plan is not None:
            launch_args.extend(self._invocation_plan.launch_args)
        return launch_args

    def _effective_launch_env(self) -> dict[str, str]:
        if self._invocation_plan is None:
            return {}
        return dict(self._invocation_plan.launch_env)

    def _invocation_scope_options(self, scope: str) -> dict[str, Any]:
        if self._invocation_plan is None:
            return {}
        scoped = self._invocation_plan.session_options.get(scope)
        if not isinstance(scoped, Mapping):
            return {}
        return dict(scoped)

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

        resume_cursor: dict[str, Any] | None = None
        if thread_id is not None:
            resume_cursor = {"threadId": thread_id}
        if runtime_mode is not None:
            record.provider.runtime_mode = runtime_mode.codex_thread_sandbox
        thread_path = str(thread_payload["path"]) if thread_payload.get("path") else None
        rollout_path = thread_payload.get("rolloutPath") or thread_payload.get("rollout_path")
        if rollout_path:
            record.provider.rollout_path = str(rollout_path)
        if approval_policy is not None:
            resume_cursor = {**(resume_cursor or record.provider.resume_cursor or {}), "approvalPolicy": approval_policy}

        record.provider.set_resume_handle(
            ProviderResumeHandle(
                kind=record.provider.kind,
                thread_id=thread_id,
                thread_path=thread_path or record.provider.thread_path,
                resume_cursor=resume_cursor or record.provider.resume_cursor,
            )
        )

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

    async def _handle_reasoning_delta(
        self,
        *,
        item_id: Any,
        turn_id: str | None,
        delta: Any,
        raw: Mapping[str, Any],
        summary_index: Any | None = None,
        redacted_for_log: bool,
    ) -> None:
        if not isinstance(item_id, str) or not item_id:
            return

        text = str(delta or "")
        if not text:
            return

        state = self._ensure_item_state(item_id)
        if state.pop("pending_reasoning_break", False) and state.get("visible_reasoning_text"):
            text = f"\n{text}"
        state["visible_reasoning_text"] = f"{state.get('visible_reasoning_text', '')}{text}"
        item = {
            "type": "reasoning",
            "id": item_id,
            "text": text,
        }
        if summary_index is not None:
            item["summaryIndex"] = summary_index

        log_item = dict(item)
        if redacted_for_log:
            log_item["text"] = "[reasoning redacted]"

        await self._emit_task_progress(
            item=item,
            turn_id=turn_id,
            raw=raw,
            log_item=log_item,
        )

    async def _handle_command_output_delta(
        self,
        *,
        item_id: Any,
        turn_id: str | None,
        delta: Any,
        raw: Mapping[str, Any],
    ) -> None:
        if not isinstance(item_id, str) or not item_id:
            return

        text = str(delta or "")
        if not text:
            return

        state = self._ensure_item_state(item_id)
        state["command_output_emitted"] = True
        state["visible_command_output"] = f"{state.get('visible_command_output', '')}{text}"

        item = self._command_progress_item(
            state.get("item"),
            text=text,
            status="inProgress",
        )
        await self._emit_task_progress(item=item, turn_id=turn_id, raw=raw)

    async def _handle_item_completed(
        self,
        item_payload: Mapping[str, Any],
        *,
        turn_id: str | None,
        raw: Mapping[str, Any],
    ) -> None:
        item_id = self._remember_item_state(item_payload, turn_id=turn_id)
        item_type = _normalize_item_type(item_payload.get("type"))

        if item_type == "commandexecution":
            await self._emit_command_completed(item_payload, turn_id=turn_id, raw=raw, item_id=item_id)
            return

        if item_type == "reasoning":
            state = self._ensure_item_state(item_id) if item_id else None
            completed_text = _reasoning_summary_text(item_payload.get("summary")) or str(item_payload.get("text") or "").strip()
            visible_text = str((state or {}).get("visible_reasoning_text") or "").strip()
            if not completed_text and visible_text:
                return
            if completed_text and completed_text == visible_text:
                return

        await self._emit_task_progress(item=item_payload, turn_id=turn_id, raw=raw)

    async def _emit_command_started(
        self,
        item_payload: Mapping[str, Any],
        *,
        turn_id: str | None,
        raw: Mapping[str, Any],
        item_id: str | None,
    ) -> None:
        if not item_id:
            return
        state = self._ensure_item_state(item_id)
        if state.get("command_started_emitted"):
            return
        state["command_started_emitted"] = True
        item = self._command_progress_item(item_payload, text=_command_start_text(item_payload), status="inProgress")
        await self._emit_task_progress(item=item, turn_id=turn_id, raw=raw)

    async def _emit_command_completed(
        self,
        item_payload: Mapping[str, Any],
        *,
        turn_id: str | None,
        raw: Mapping[str, Any],
        item_id: str | None,
    ) -> None:
        if not item_id:
            return
        state = self._ensure_item_state(item_id)
        if state.get("command_completed_emitted"):
            return
        state["command_completed_emitted"] = True
        include_output = not bool(state.get("command_output_emitted"))
        item = self._command_progress_item(
            item_payload,
            text=_command_completion_text(item_payload, include_output=include_output),
            status=item_payload.get("status") or "completed",
        )
        await self._emit_task_progress(item=item, turn_id=turn_id, raw=raw)

    async def _emit_task_progress(
        self,
        *,
        item: Mapping[str, Any],
        turn_id: str | None,
        raw: Mapping[str, Any],
        log_item: Mapping[str, Any] | None = None,
    ) -> None:
        item_payload = dict(item)
        provider_payload = dict(raw)
        payload = {
            "item": item_payload,
            "item_type": item.get("type"),
            "turn_id": turn_id or self.current_turn_id,
            "text": _progress_text_from_item(item_payload),
            "provider_payload": provider_payload,
        }
        logged_item = dict(log_item) if log_item is not None else item_payload
        log_payload = {
            **payload,
            "item": logged_item,
            "text": _progress_text_from_item(logged_item),
            "provider_payload": provider_payload,
        }
        await self._emit_canonical("task.progress", _log_payload=log_payload, **payload)

    def _remember_item_state(self, item_payload: Mapping[str, Any], *, turn_id: str | None = None) -> str | None:
        item_id = item_payload.get("id")
        if not isinstance(item_id, str) or not item_id:
            return None
        state = self._ensure_item_state(item_id)
        state["item"] = dict(item_payload)
        if turn_id:
            state["turn_id"] = turn_id
        return item_id

    def _ensure_item_state(self, item_id: str) -> dict[str, Any]:
        state = self._item_states.get(item_id)
        if state is None:
            state = {
                "pending_reasoning_break": False,
                "visible_reasoning_text": "",
                "visible_command_output": "",
                "command_started_emitted": False,
                "command_completed_emitted": False,
                "command_output_emitted": False,
            }
            self._item_states[item_id] = state
        return state

    def _command_progress_item(
        self,
        item_payload: Mapping[str, Any] | None,
        *,
        text: str,
        status: Any,
    ) -> dict[str, Any]:
        item = dict(item_payload or {})
        item["type"] = item.get("type") or "commandExecution"
        if isinstance(text, str) and text:
            item["text"] = text
        if status is not None:
            item["status"] = status
        return item

    async def _emit_canonical(self, event_type: str, _log_payload: Mapping[str, Any] | None = None, **payload: Any) -> None:
        event: CanonicalEvent = {
            "type": event_type,
            "timestamp": _timestamp_now(),
            "origin": "provider",
            "provider": "codex",
        }
        if self.agent_record is not None:
            event["agent_id"] = self.agent_record.identity.agent_id
            event["run_id"] = self.agent_record.identity.run_id
            event["task_id"] = self.agent_record.identity.task_id
        if self.provider_thread_id is not None:
            event["provider_thread_id"] = self.provider_thread_id
        event.update(payload)

        if self._canonical_logger is not None:
            log_data = (
                dict(_log_payload)
                if _log_payload is not None
                else {key: value for key, value in event.items() if key not in {"type", "timestamp"}}
            )
            for key in ("agent_id", "run_id", "task_id", "provider_thread_id"):
                if key in event and key not in log_data:
                    log_data[key] = event[key]
            self._canonical_logger.log_canonical(
                event_type,
                log_data,
                timestamp=event["timestamp"],
            )
        await self.on_canonical_event(event)

    def _handle_native_event(self, raw_event: dict[str, Any]) -> None:
        if self._native_logger is None:
            self._ensure_loggers()
        if self._native_logger is None:
            return

        event_name = raw_event.get("event", "raw")
        data = raw_event.get("data") if isinstance(raw_event.get("data"), Mapping) else {"value": raw_event}
        safe_data = _sanitize_native_event_data(event_name, data, pending_requests=self._pending_requests)
        self._native_logger.log(event_name, safe_data)

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


def _coerce_auth_config(value: object) -> CodexAuthConfig | None:
    if value is None:
        return None
    if isinstance(value, CodexAuthConfig):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        raw_mode = data.pop("mode", None) or data.pop("auth_mode", None) or data.get("type")
        mode = _coerce_auth_mode(raw_mode)
        return CodexAuthConfig(
            mode=mode,
            api_key=data.get("api_key") or data.get("apiKey"),
            id_token=data.get("id_token") or data.get("idToken"),
            access_token=data.get("access_token") or data.get("accessToken"),
        )
    raise TypeError("auth_config must be CodexAuthConfig, a mapping, or None")


def _coerce_auth_mode(value: object) -> CodexAuthMode:
    if value is None:
        return CodexAuthMode.SYSTEM
    if isinstance(value, CodexAuthMode):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        lowered = normalized.replace("-", "_").lower()
        mapping = {
            "system": CodexAuthMode.SYSTEM,
            "default": CodexAuthMode.SYSTEM,
            "apikey": CodexAuthMode.API_KEY,
            "api_key": CodexAuthMode.API_KEY,
            "apikeys": CodexAuthMode.API_KEY,
            "apikeymode": CodexAuthMode.API_KEY,
            "apikeyauth": CodexAuthMode.API_KEY,
            "apiKey": CodexAuthMode.API_KEY,
            "chatgpt": CodexAuthMode.CHATGPT,
            "chatgptauthtokens": CodexAuthMode.CHATGPT_AUTH_TOKENS,
            "chatgpt_auth_tokens": CodexAuthMode.CHATGPT_AUTH_TOKENS,
        }
        if normalized in (mode.value for mode in CodexAuthMode):
            return CodexAuthMode(normalized)
        if lowered in mapping:
            return mapping[lowered]
    raise ValueError(f"Unsupported Codex auth mode: {value!r}")


def _coerce_provider_payload(value: Any, *, field_name: str | None = None) -> dict[str, Any]:
    if field_name is not None:
        return {field_name: value}
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}


def _thread_path_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    path = payload.get("path") or payload.get("threadPath") or payload.get("thread_path")
    if isinstance(path, str) and path:
        return path
    return None


def _turn_id_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    turn_id = payload.get("id") or payload.get("turnId") or payload.get("turn_id")
    if isinstance(turn_id, str) and turn_id:
        return turn_id
    return None


def _turn_status_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return None


def _progress_text_from_item(item_payload: Any) -> str | None:
    if not isinstance(item_payload, Mapping):
        return None
    text = item_payload.get("text")
    if isinstance(text, str) and text:
        return text
    content = item_payload.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, Mapping):
                part = entry.get("text")
                if isinstance(part, str) and part:
                    parts.append(part)
        combined = "".join(parts).strip()
        return combined or None
    return None


def _error_message(error_payload: Any) -> str | None:
    if isinstance(error_payload, Mapping):
        message = error_payload.get("message")
        if isinstance(message, str) and message:
            return message
        if error_payload:
            return str(dict(error_payload))
        return None
    if error_payload is None:
        return None
    text = str(error_payload)
    return text or None


def _error_code(error_payload: Any) -> int | str | None:
    if not isinstance(error_payload, Mapping):
        return None
    code = error_payload.get("code")
    if isinstance(code, (int, str)):
        return code
    return None


def _sanitize_progress_item(item_payload: Any) -> Any:
    """Return a canonical-safe representation of an ``item/completed`` payload."""

    if not isinstance(item_payload, Mapping):
        return item_payload

    item = dict(item_payload)
    item_type = str(item.get("type") or "").lower()
    if item_type != "reasoning":
        return item

    summary_text = _reasoning_summary_text(item.get("summary"))
    if summary_text:
        item.setdefault("text", summary_text)

    # Avoid writing raw reasoning content to canonical logs.
    item.pop("content", None)
    return item


def _normalize_item_type(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _command_start_text(item_payload: Mapping[str, Any]) -> str:
    command = str(item_payload.get("command") or "").strip()
    return f"$ {command}" if command else "command started"


def _command_completion_text(item_payload: Mapping[str, Any], *, include_output: bool) -> str:
    exit_code = item_payload.get("exitCode")
    duration_ms = item_payload.get("durationMs")
    summary = "command completed"
    if isinstance(exit_code, int):
        summary = f"{summary} (exit {exit_code})"
    if isinstance(duration_ms, int):
        summary = f"{summary} [{duration_ms}ms]"

    if not include_output:
        return summary

    output = str(item_payload.get("aggregatedOutput") or "").rstrip()
    if not output:
        return summary
    return f"{summary}\n{output}"


def _reasoning_summary_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, Mapping):
                text = entry.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return str(value).strip()


def _sanitize_native_event_data(
    event_name: object,
    data: Mapping[str, Any],
    *,
    pending_requests: Mapping[int | str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Redact sensitive fields before writing native debug logs."""

    sanitized = dict(data)
    event = str(event_name or "raw")

    method = sanitized.get("method")
    params = sanitized.get("params")

    if isinstance(method, str) and isinstance(params, Mapping):
        if method == "account/login/start":
            sanitized["params"] = _redact_keys(params, {"apiKey", "idToken", "accessToken"})
        elif method == "item/reasoning/textDelta":
            sanitized["params"] = _redact_keys(params, {"delta"})

    if event == "jsonrpc.response.sent":
        request_id = sanitized.get("id")
        pending = (pending_requests or {}).get(request_id) if request_id is not None else None
        pending_method = pending.get("method") if isinstance(pending, Mapping) else None
        if pending_method == "account/chatgptAuthTokens/refresh":
            result = sanitized.get("result")
            if isinstance(result, Mapping):
                sanitized["result"] = _redact_keys(result, {"idToken", "accessToken"})

    return sanitized


def _redact_keys(payload: Mapping[str, Any], keys: set[str]) -> dict[str, Any]:
    redacted = dict(payload)
    for key in keys:
        if key in redacted and redacted[key] is not None:
            redacted[key] = "***REDACTED***"
    return redacted
