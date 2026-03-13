"""Claude-backed implementation of the provider adapter interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import Any

from ...models.agent import AgentRecord, ProviderResumeHandle
from ...runtime_logging.ndjson_logger import CanonicalLogger, NativeLogger
from ..base import CanonicalEvent, CanonicalEventHandler, ProviderAdapter, RuntimeMode
from ..invocation import ProviderInvocationPlan

try:
    from claude_agent_sdk import ClaudeSDKClient, get_session_messages, list_sessions
    from claude_agent_sdk.types import (
        AssistantMessage,
        ClaudeAgentOptions,
        PermissionResultAllow,
        PermissionResultDeny,
        ResultMessage,
        TaskNotificationMessage,
        TaskProgressMessage,
        TaskStartedMessage,
        TextBlock,
        ThinkingBlock,
    )
except ImportError as exc:  # pragma: no cover - exercised only when dependency is absent
    ClaudeSDKClient = None  # type: ignore[assignment]
    ClaudeAgentOptions = None  # type: ignore[assignment]
    AssistantMessage = ResultMessage = TaskNotificationMessage = TaskProgressMessage = TaskStartedMessage = object
    TextBlock = ThinkingBlock = object
    PermissionResultAllow = PermissionResultDeny = None  # type: ignore[assignment]
    list_sessions = get_session_messages = None  # type: ignore[assignment]
    _CLAUDE_SDK_IMPORT_ERROR = exc
else:
    _CLAUDE_SDK_IMPORT_ERROR = None

DEFAULT_PERMISSION_MODE = "default"
READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite"}
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
TURN_RESULT_SUBTYPE_ERROR = {"error_max_turns", "error_during_execution", "error_permission", "error"}
SUPPORTED_LIST_METHODS = {"session/list", "session/messages", "mcp/status", "server/info"}


@dataclass(slots=True)
class _PermissionAllowShim:
    updated_input: dict[str, Any] | None = None
    updated_permissions: list[Any] | None = None
    behavior: str = "allow"


@dataclass(slots=True)
class _PermissionDenyShim:
    message: str = ""
    interrupt: bool = False
    behavior: str = "deny"


class ClaudeProviderAdapter(ProviderAdapter):
    """Provider adapter over ``ClaudeSDKClient`` with canonical normalization."""

    def __init__(
        self,
        client: ClaudeSDKClient | Any | None = None,
        *,
        cwd: str | None = None,
        launch_args: Sequence[str] | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
        resume_thread_id: str | None = None,
        claude_cli_path: str | None = None,
        claude_settings: str | None = None,
        claude_add_dirs: Sequence[str] | None = None,
        claude_allowed_tools: Sequence[str] | None = None,
        claude_disallowed_tools: Sequence[str] | None = None,
        claude_fallback_model: str | None = None,
        claude_setting_sources: Sequence[str] | None = None,
        claude_model: str | None = None,
        claude_effort: str | None = None,
        claude_extra_config: Mapping[str, Any] | None = None,
        codex_binary: str | None = None,
        codex_home: str | None = None,
        agent_record: AgentRecord | None = None,
        on_canonical_event: CanonicalEventHandler | None = None,
        on_stderr_line: Any | None = None,
        native_logger: NativeLogger | None = None,
        canonical_logger: CanonicalLogger | None = None,
        client_factory: Any | None = None,
        **_: Any,
    ) -> None:
        super().__init__(on_canonical_event=on_canonical_event)
        self.client = client
        self._client_factory = client_factory or ClaudeSDKClient
        self._cwd = cwd
        self._launch_args = [str(arg) for arg in launch_args or []]
        self._invocation_plan = invocation_plan
        self._initial_resume_thread_id = resume_thread_id
        self._claude_cli_path = claude_cli_path
        self._claude_settings = claude_settings
        self._claude_add_dirs = [str(Path(path)) for path in claude_add_dirs or []]
        self._claude_allowed_tools = [str(tool) for tool in dict.fromkeys(claude_allowed_tools or [])]
        self._claude_disallowed_tools = [str(tool) for tool in dict.fromkeys(claude_disallowed_tools or [])]
        self._claude_disallowed_tool_set = set(self._claude_disallowed_tools)
        self._claude_fallback_model = claude_fallback_model
        self._claude_setting_sources = list(claude_setting_sources or ["user", "project", "local"])
        self._model = claude_model
        self._effort = claude_effort
        self._extra_config = dict(claude_extra_config or {})
        self.agent_record = agent_record
        self.provider_thread_id: str | None = None
        self.thread_metadata: dict[str, Any] = {}
        self.current_turn_id: str | None = None
        self._turn_counter = 0
        self._runtime_mode = RuntimeMode.WORKSPACE_WRITE
        self._approval_policy = "never"
        self._session_started = False
        self._thread_started_emitted = False
        self._thread_resumed = False
        self._last_result: dict[str, Any] | None = None
        self._last_server_info: dict[str, Any] | None = None
        self._on_stderr_line = on_stderr_line
        self._native_logger = native_logger
        self._canonical_logger = canonical_logger

        self._ensure_loggers()
        ignored_constructor_extras: dict[str, Any] = {}
        if codex_binary:
            ignored_constructor_extras["codex_binary"] = codex_binary
        if codex_home:
            ignored_constructor_extras["codex_home"] = codex_home
        if self._launch_args:
            ignored_constructor_extras["launch_args"] = list(self._launch_args)
        if invocation_plan is not None and invocation_plan.launch_args:
            ignored_constructor_extras["invocation_launch_args"] = list(invocation_plan.launch_args)
        if ignored_constructor_extras:
            self._handle_native_event(
                {
                    "event": "provider.constructor.extra_ignored",
                    "data": ignored_constructor_extras,
                }
            )

    @property
    def is_running(self) -> bool:
        return self._session_started and self.client is not None

    def _ensure_client(self, cwd: str | None = None) -> Any:
        self._ensure_loggers(cwd)
        if cwd is not None:
            self._cwd = cwd
        if self.client is not None:
            return self.client

        if self._client_factory is None or ClaudeAgentOptions is None:
            raise RuntimeError(
                "Claude provider support requires the claude-agent-sdk package"
            ) from _CLAUDE_SDK_IMPORT_ERROR

        options = self._build_client_options()
        self.client = self._client_factory(options=options)
        return self.client

    def _build_client_options(self) -> Any:
        if ClaudeAgentOptions is None:
            raise RuntimeError(
                "Claude provider support requires the claude-agent-sdk package"
            ) from _CLAUDE_SDK_IMPORT_ERROR

        option_kwargs: dict[str, Any] = {
            "cwd": self._cwd,
            "cli_path": self._claude_cli_path,
            "settings": self._claude_settings,
            "add_dirs": list(self._claude_add_dirs),
            "allowed_tools": list(self._claude_allowed_tools),
            "disallowed_tools": list(self._claude_disallowed_tools),
            "model": self._model,
            "fallback_model": self._claude_fallback_model,
            "setting_sources": list(self._claude_setting_sources),
            "permission_mode": DEFAULT_PERMISSION_MODE,
            "stderr": self._handle_stderr,
            "can_use_tool": self._can_use_tool,
            "sandbox": {
                "enabled": True,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": True,
            },
        }
        if self._initial_resume_thread_id:
            option_kwargs["resume"] = self._initial_resume_thread_id
        if self._effort in {"low", "medium", "high", "max"}:
            option_kwargs["effort"] = self._effort

        for key in (
            "max_turns",
            "max_budget_usd",
            "betas",
            "output_format",
            "user",
            "env",
            "include_partial_messages",
            "enable_file_checkpointing",
            "agents",
            "plugins",
        ):
            value = self._extra_config.get(key)
            if value is not None:
                option_kwargs[key] = value

        self._apply_invocation_plan(option_kwargs)

        return ClaudeAgentOptions(**option_kwargs)

    def _apply_invocation_plan(self, option_kwargs: dict[str, Any]) -> None:
        if self._invocation_plan is None:
            return

        overrides = dict(self._invocation_plan.session_options)

        base_env = option_kwargs.get("env")
        merged_env = dict(base_env) if isinstance(base_env, Mapping) else {}
        merged_env.update(self._invocation_plan.launch_env)
        env_override = overrides.pop("env", None)
        if isinstance(env_override, Mapping):
            merged_env.update({str(key): value for key, value in env_override.items()})
        if merged_env:
            option_kwargs["env"] = merged_env

        for key in ("add_dirs", "allowed_tools", "disallowed_tools", "setting_sources"):
            values = overrides.pop(key, None)
            if _is_string_sequence(values):
                option_kwargs[key] = _merge_unique_strings(option_kwargs.get(key), values)

        option_kwargs.update(overrides)

    def _ensure_loggers(self, cwd: str | None = None) -> None:
        if self._native_logger is not None and self._canonical_logger is not None:
            return

        if self.agent_record is None:
            return

        base_cwd = Path(cwd or self._cwd or Path.cwd()).expanduser().resolve()
        native_path = self.agent_record.provider.native_event_log or str(
            base_cwd / ".vibrant" / "logs" / "providers" / "native" / f"{self.agent_record.identity.agent_id}.ndjson"
        )
        canonical_path = self.agent_record.provider.canonical_event_log or str(
            base_cwd / ".vibrant" / "logs" / "providers" / "canonical" / f"{self.agent_record.identity.agent_id}.ndjson"
        )

        self.agent_record.provider.native_event_log = native_path
        self.agent_record.provider.canonical_event_log = canonical_path
        self.agent_record.provider.kind = "claude"
        self.agent_record.provider.transport = "sdk-stream-json"

        if self._native_logger is None:
            self._native_logger = NativeLogger(native_path)
        if self._canonical_logger is None:
            self._canonical_logger = CanonicalLogger(canonical_path)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        client = self._ensure_client(cwd)
        if kwargs:
            self._handle_native_event({"event": "session.start.extra", "data": dict(kwargs)})

        await client.connect()
        self._session_started = True

        server_info = await _maybe_call(client, "get_server_info")
        self._last_server_info = _coerce_provider_payload(server_info)
        await self._emit_canonical(
            "session.started",
            cwd=self._cwd,
            provider_payload={"server_info": self._last_server_info},
        )
        return server_info

    async def stop_session(self) -> None:
        if self.client is not None:
            disconnect = getattr(self.client, "disconnect", None)
            if disconnect is not None:
                result = disconnect()
                if inspect.isawaitable(result):
                    await result
        self._session_started = False
        await self._emit_canonical("session.state.changed", state="stopped")

    async def start_thread(self, **kwargs: Any) -> Any:
        await self._configure_thread(kwargs)
        self._thread_resumed = False
        self._thread_started_emitted = False
        self.thread_metadata = {}
        return {"thread": {}}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        await self._configure_thread(kwargs)
        self.provider_thread_id = provider_thread_id
        self._thread_resumed = True
        self._thread_started_emitted = True
        self._persist_thread_metadata(provider_thread_id)
        await self._emit_canonical(
            "thread.started",
            resumed=True,
            thread={"id": provider_thread_id},
        )
        return {"thread": {"id": provider_thread_id}}

    async def _configure_thread(self, kwargs: Mapping[str, Any]) -> None:
        client = self._ensure_client()
        data = dict(kwargs)
        runtime_mode = RuntimeMode(data.pop("runtime_mode", self._runtime_mode))
        approval_policy = str(data.pop("approval_policy", self._approval_policy))
        if approval_policy != "never":
            raise ValueError("Claude provider currently supports approval_policy='never' only")

        model = data.pop("model", self._model)
        data.pop("model_provider", None)
        data.pop("reasoning_summary", None)
        data.pop("persist_extended_history", None)
        data.pop("extra_config", None)
        data.pop("agent_record", None)
        data.pop("cwd", None)
        data.pop("reasoning_effort", None)

        self._runtime_mode = runtime_mode
        self._approval_policy = approval_policy
        self._model = model

        if model is not None and hasattr(client, "set_model"):
            await client.set_model(model)

        if hasattr(client, "set_permission_mode"):
            await client.set_permission_mode(DEFAULT_PERMISSION_MODE)

        if self.agent_record is not None:
            self.agent_record.provider.kind = "claude"
            self.agent_record.provider.transport = "sdk-stream-json"
            self.agent_record.provider.runtime_mode = runtime_mode.codex_thread_sandbox

        if data:
            self._handle_native_event({"event": "thread.configure.extra_ignored", "data": data})

    async def start_turn(
        self,
        *,
        input_items: Sequence[Mapping[str, Any]],
        runtime_mode: RuntimeMode,
        approval_policy: str,
        **kwargs: Any,
    ) -> Any:
        if approval_policy != "never":
            raise ValueError("Claude provider currently supports approval_policy='never' only")

        client = self._ensure_client(kwargs.pop("cwd", None))
        self._runtime_mode = RuntimeMode(runtime_mode)
        self._approval_policy = approval_policy
        self._turn_counter += 1
        self.current_turn_id = f"turn-{self._turn_counter}"
        prompt = _prompt_from_input_items(input_items)
        self._last_result = None

        if kwargs:
            self._handle_native_event({"event": "turn.start.extra", "data": dict(kwargs)})

        await self._emit_canonical(
            "turn.started",
            turn_id=self.current_turn_id,
            turn_status="running",
            turn={"id": self.current_turn_id, "status": "running"},
        )
        self._handle_native_event(
            {
                "event": "sdk.query.sent",
                "data": {
                    "turn_id": self.current_turn_id,
                    "prompt": prompt,
                },
            }
        )

        await client.query(prompt)

        received_result = False
        async for message in client.receive_response():
            await self._handle_message(message)
            if isinstance(message, ResultMessage):
                received_result = True

        if not received_result:
            raise RuntimeError("Claude response stream ended without a terminal result message")
        return self._last_result or {}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        client = self._ensure_client(kwargs.pop("cwd", None))
        if kwargs:
            self._handle_native_event({"event": "turn.interrupt.extra", "data": dict(kwargs)})
        await client.interrupt()
        return {"interrupted": True, "turn_id": self.current_turn_id}

    async def send_request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if kwargs:
            raise TypeError(f"Unexpected keyword arguments: {', '.join(sorted(kwargs))}")
        request_params = dict(params or {})

        if method not in SUPPORTED_LIST_METHODS:
            raise NotImplementedError(f"Claude provider does not support send_request method {method!r}")

        if method == "session/list":
            if list_sessions is None:
                raise RuntimeError("Claude provider support requires the claude-agent-sdk package") from _CLAUDE_SDK_IMPORT_ERROR
            return [
                _serialize_value(item)
                for item in list_sessions(
                    directory=request_params.get("directory"),
                    limit=request_params.get("limit"),
                    include_worktrees=bool(request_params.get("include_worktrees", True)),
                )
            ]

        if method == "session/messages":
            if get_session_messages is None:
                raise RuntimeError("Claude provider support requires the claude-agent-sdk package") from _CLAUDE_SDK_IMPORT_ERROR
            session_id = request_params.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("session/messages requires params.session_id")
            return [
                _serialize_value(item)
                for item in get_session_messages(
                    session_id=session_id,
                    directory=request_params.get("directory"),
                )
            ]

        client = self._ensure_client()
        if method == "mcp/status":
            return await client.get_mcp_status()
        return await client.get_server_info()

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> Any:
        raise RuntimeError(
            f"Claude provider does not expose interactive request handling (request_id={request_id!r}, "
            f"result={result!r}, error={dict(error) if error else None!r})"
        )

    async def on_canonical_event(self, event: CanonicalEvent) -> None:
        if self.canonical_event_handler is not None:
            callback_result = self.canonical_event_handler(dict(event))
            if inspect.isawaitable(callback_result):
                await callback_result

    async def _handle_message(self, message: Any) -> None:
        self._handle_native_event({"event": "sdk.message.received", "data": _serialize_value(message)})
        session_id = _message_session_id(message)
        if session_id:
            await self._capture_thread_id(session_id)

        if isinstance(message, TaskStartedMessage):
            await self._emit_task_progress(
                item={
                    "type": "task",
                    "id": message.task_id,
                    "status": "started",
                    "text": message.description,
                    "usage": None,
                }
            )
            return

        if isinstance(message, TaskProgressMessage):
            await self._emit_task_progress(
                item={
                    "type": "task",
                    "id": message.task_id,
                    "status": "inProgress",
                    "text": message.description,
                    "usage": dict(message.usage),
                    "lastToolName": message.last_tool_name,
                }
            )
            return

        if isinstance(message, TaskNotificationMessage):
            await self._emit_task_progress(
                item={
                    "type": "task",
                    "id": message.task_id,
                    "status": message.status,
                    "text": message.summary,
                    "usage": dict(message.usage) if isinstance(message.usage, Mapping) else message.usage,
                    "outputFile": message.output_file,
                }
            )
            return

        if isinstance(message, AssistantMessage):
            for index, block in enumerate(message.content):
                item_id = f"{self.current_turn_id or 'turn'}:assistant:{index}"
                if isinstance(block, TextBlock):
                    await self._emit_canonical(
                        "content.delta",
                        item_id=item_id,
                        turn_id=self.current_turn_id,
                        delta=block.text,
                        provider_payload={"block": _serialize_value(block)},
                    )
                elif isinstance(block, ThinkingBlock):
                    await self._emit_canonical(
                        "reasoning.summary.delta",
                        item_id=item_id,
                        turn_id=self.current_turn_id,
                        delta="[reasoning redacted]",
                        provider_payload={"block": {"type": "thinking"}},
                    )
            return

        if isinstance(message, ResultMessage):
            payload = _result_payload(message)
            self._last_result = payload
            turn_status = "failed" if message.is_error or message.subtype in TURN_RESULT_SUBTYPE_ERROR else "completed"
            if turn_status == "failed":
                await self._emit_canonical(
                    "runtime.error",
                    error=payload,
                    error_message=str(message.result or message.stop_reason or "Claude task failed"),
                    provider_payload=payload,
                )
            await self._emit_canonical(
                "turn.completed",
                turn_id=self.current_turn_id,
                turn_status=turn_status,
                turn={
                    "id": self.current_turn_id,
                    "status": turn_status,
                    "session_id": message.session_id,
                    "summary": message.result,
                },
                provider_payload=payload,
            )
            await self._emit_canonical(
                "task.completed",
                turn_id=self.current_turn_id,
                turn_status=turn_status,
                turn={
                    "id": self.current_turn_id,
                    "status": turn_status,
                    "session_id": message.session_id,
                    "summary": message.result,
                },
                provider_payload=payload,
            )

    async def _capture_thread_id(self, session_id: str) -> None:
        if self.provider_thread_id == session_id and self._thread_started_emitted:
            return

        self.provider_thread_id = session_id
        self.thread_metadata = {"id": session_id}
        self._persist_thread_metadata(session_id)

        if not self._thread_started_emitted:
            self._thread_started_emitted = True
            await self._emit_canonical(
                "thread.started",
                resumed=self._thread_resumed,
                thread={"id": session_id},
            )

    def _persist_thread_metadata(self, thread_id: str) -> None:
        record = self.agent_record
        if record is None:
            return
        record.provider.kind = "claude"
        record.provider.transport = "sdk-stream-json"
        record.provider.runtime_mode = self._runtime_mode.codex_thread_sandbox
        record.provider.set_resume_handle(
            ProviderResumeHandle(
                kind=record.provider.kind,
                thread_id=thread_id,
                thread_path=None,
                resume_cursor={"sessionId": thread_id},
            )
        )

    async def _emit_task_progress(self, *, item: Mapping[str, Any]) -> None:
        await self._emit_canonical(
            "task.progress",
            item=dict(item),
            item_type=item.get("type"),
            turn_id=self.current_turn_id,
            text=_progress_text_from_item(item),
            provider_payload={"item": dict(item)},
        )

    async def _emit_canonical(self, event_type: str, **payload: Any) -> None:
        event: CanonicalEvent = {
            "type": event_type,
            "timestamp": _timestamp_now(),
            "origin": "provider",
            "provider": "claude",
        }
        if self.agent_record is not None:
            event["agent_id"] = self.agent_record.identity.agent_id
            event["task_id"] = self.agent_record.identity.task_id
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

    async def _can_use_tool(self, tool_name: str, input_data: dict[str, Any], context: Any) -> Any:
        request_payload = {
            "tool_name": tool_name,
            "input": dict(input_data),
            "suggestions": getattr(context, "suggestions", None),
            "runtime_mode": self._runtime_mode.value,
        }
        self._handle_native_event({"event": "tool.permission.requested", "data": request_payload})

        deny_message = self._tool_denial_message(tool_name, input_data)
        if deny_message is None:
            response = _permission_allow(updated_input=dict(input_data))
            self._handle_native_event(
                {
                    "event": "tool.permission.resolved",
                    "data": {**request_payload, "behavior": "allow"},
                }
            )
            return response

        response = _permission_deny(message=deny_message, interrupt=False)
        self._handle_native_event(
            {
                "event": "tool.permission.resolved",
                "data": {**request_payload, "behavior": "deny", "message": deny_message},
            }
        )
        return response

    def _tool_denial_message(self, tool_name: str, input_data: Mapping[str, Any]) -> str | None:
        if tool_name in self._claude_disallowed_tool_set:
            return f"Tool {tool_name} is disallowed by Vibrant configuration."

        if self._runtime_mode is RuntimeMode.READ_ONLY:
            if tool_name in READ_ONLY_TOOLS:
                return None
            return "This Claude run is read-only. Claude must not execute mutating or shell tools."

        if tool_name in MUTATING_TOOLS:
            candidate = _tool_path_candidate(tool_name, input_data)
            if candidate is not None and self._runtime_mode is not RuntimeMode.FULL_ACCESS:
                if not _path_within_any_root(candidate, self._workspace_roots()):
                    return f"File edits must stay inside the workspace roots: {candidate}"
            return None

        if tool_name == "Bash":
            if self._runtime_mode is not RuntimeMode.FULL_ACCESS and input_data.get("dangerouslyDisableSandbox"):
                return "Unsandboxed Bash commands are only allowed in full-access mode."
            return None

        if tool_name == "Task" and self._runtime_mode is RuntimeMode.READ_ONLY:
            return "Sub-agent task execution is disabled in read-only mode."

        return None

    def _workspace_roots(self) -> list[Path]:
        roots: list[Path] = []
        if self._cwd:
            roots.append(Path(self._cwd).expanduser().resolve())
        roots.extend(Path(path).expanduser().resolve() for path in self._claude_add_dirs)
        return roots

    def _handle_native_event(self, raw_event: Mapping[str, Any]) -> None:
        if self._native_logger is None:
            self._ensure_loggers()
        if self._native_logger is None:
            return
        event_name = str(raw_event.get("event") or "raw")
        data = raw_event.get("data") if isinstance(raw_event.get("data"), Mapping) else {"value": _serialize_value(raw_event)}
        self._native_logger.log(event_name, data)

    def _handle_stderr(self, line: str) -> None:
        if self._native_logger is not None:
            self._native_logger.log_stderr(line)
        if self._on_stderr_line is not None:
            callback_result = self._on_stderr_line(line)
            if inspect.isawaitable(callback_result):
                raise RuntimeError("on_stderr_line must be synchronous")


async def _maybe_call(target: Any, method_name: str) -> Any:
    method = getattr(target, method_name, None)
    if method is None:
        return None
    result = method()
    if inspect.isawaitable(result):
        return await result
    return result


def _permission_allow(*, updated_input: dict[str, Any] | None = None) -> Any:
    if PermissionResultAllow is not None:
        return PermissionResultAllow(updated_input=updated_input)
    return _PermissionAllowShim(updated_input=updated_input)


def _permission_deny(*, message: str, interrupt: bool) -> Any:
    if PermissionResultDeny is not None:
        return PermissionResultDeny(message=message, interrupt=interrupt)
    return _PermissionDenyShim(message=message, interrupt=interrupt)


def _tool_path_candidate(tool_name: str, input_data: Mapping[str, Any]) -> Path | None:
    keys_by_tool = {
        "Write": ("file_path", "path"),
        "Edit": ("file_path", "path"),
        "MultiEdit": ("file_path", "path"),
        "NotebookEdit": ("notebook_path", "file_path", "path"),
    }
    keys = keys_by_tool.get(tool_name, ("file_path", "path"))
    for key in keys:
        raw = input_data.get(key)
        if isinstance(raw, str) and raw.strip():
            return Path(raw)
    return None


def _path_within_any_root(candidate: Path, roots: Sequence[Path]) -> bool:
    if not roots:
        return False
    for root in roots:
        root = root.expanduser().resolve()
        base_candidate = candidate if candidate.is_absolute() else root / candidate
        try:
            resolved_candidate = base_candidate.expanduser().resolve()
        except FileNotFoundError:
            resolved_candidate = base_candidate.expanduser().absolute()
        try:
            resolved_candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _message_session_id(message: Any) -> str | None:
    session_id = getattr(message, "session_id", None)
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def _result_payload(message: ResultMessage) -> dict[str, Any]:
    return {
        "subtype": message.subtype,
        "duration_ms": message.duration_ms,
        "duration_api_ms": message.duration_api_ms,
        "is_error": message.is_error,
        "num_turns": message.num_turns,
        "session_id": message.session_id,
        "stop_reason": message.stop_reason,
        "total_cost_usd": message.total_cost_usd,
        "usage": _serialize_value(message.usage),
        "result": message.result,
        "structured_output": _serialize_value(message.structured_output),
        "summary": message.result,
    }


def _serialize_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _coerce_provider_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    return {"value": _serialize_value(value)}


def _is_string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _merge_unique_strings(existing: Any, extra: Sequence[Any]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for source in (existing or [], extra):
        for item in source:
            text = str(item)
            if text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def _prompt_from_input_items(input_items: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for item in input_items:
        text = item.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
            continue
        parts.append(str(_serialize_value(item)))
    return "\n\n".join(part for part in parts if part).strip()


def _progress_text_from_item(item_payload: Mapping[str, Any]) -> str | None:
    text = item_payload.get("text")
    if isinstance(text, str) and text:
        return text
    return None


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
