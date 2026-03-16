"""Deterministic fixture provider used by backend E2E tests.

This adapter behaves like a normal provider from the orchestrator's point of
view:

- it emits canonical lifecycle events through the standard callback
- it writes native and canonical NDJSON logs to the run-record paths
- it persists resumable thread metadata on the agent record
- it supports deterministic prompt-driven side effects for tests

It intentionally mirrors only the stable provider-facing contract that the
orchestrator depends on. Exact provider subtleties are not promised here or by
the real providers: chunk sizes, auxiliary event presence, request mirroring,
resume-cursor details, and the ordering of terminal events may vary by backend.
Orchestrator code must rely on the canonical contract, not on provider-specific
incidental behavior.

Markers are parsed from the full prompt text. They constrain behavior shape but
do not replace the rest of the prompt.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from vibrant.config import DEFAULT_CONFIG_DIR
from vibrant.models.agent import AgentRecord
from vibrant.orchestrator.mcp import BINDING_HEADER_NAME
from vibrant.providers.base import CanonicalEvent, ProviderAdapter, RuntimeMode
from vibrant.providers.invocation import ProviderInvocationPlan
from vibrant.runtime_logging.ndjson_logger import CanonicalLogger, NativeLogger

_MARKER_PATTERN = re.compile(r"\[mock:(.+?)\]", re.IGNORECASE)
_READ_ONLY_RESOURCE_URIS: dict[str, str] = {
    "vibrant.get_consensus": "vibrant://consensus",
    "vibrant.get_roadmap": "vibrant://roadmap",
    "vibrant.get_workflow_status": "vibrant://workflow-status",
    "vibrant.get_workflow_session": "vibrant://workflow-session",
    "vibrant.get_gatekeeper_session": "vibrant://gatekeeper-session",
    "vibrant.list_pending_questions": "vibrant://pending-questions",
    "vibrant.list_active_runs": "vibrant://active-runs",
    "vibrant.list_active_attempts": "vibrant://active-attempts",
    "vibrant.list_pending_review_tickets": "vibrant://pending-review-tickets",
}
_PREFERRED_MCP_RESOURCES: tuple[str, ...] = (
    "vibrant.get_workflow_session",
    "vibrant.get_workflow_status",
    "vibrant.list_active_attempts",
    "vibrant.get_gatekeeper_session",
    "vibrant.get_consensus",
    "vibrant.get_roadmap",
)


@dataclass(frozen=True, slots=True)
class FileMutation:
    """One deterministic file mutation requested by prompt markers."""

    mode: Literal["write", "append"]
    relative_path: str


@dataclass(frozen=True, slots=True)
class FixtureScenario:
    """Structured behavior extracted from the prompt."""

    file_mutations: tuple[FileMutation, ...] = ()
    content: str | None = None
    ask_question: bool = False
    fail: bool = False
    long_response: bool = False


@dataclass(slots=True)
class ThreadState:
    """Durable per-thread metadata persisted beside provider artifacts."""

    thread_id: str
    turn_count: int = 0
    request_count: int = 0
    last_prompt_summary: str | None = None


@dataclass(frozen=True, slots=True)
class RequestResolution:
    """Structured request response captured for the active fixture turn."""

    result: Any | None = None
    error: dict[str, Any] | None = None

    @property
    def error_message(self) -> str | None:
        message = (self.error or {}).get("message")
        return message if isinstance(message, str) and message else None


@dataclass(frozen=True, slots=True)
class MCPResourceInteraction:
    """One real MCP resource read driven by fixture markers."""

    endpoint_url: str
    binding_id: str
    resource_name: str
    resource_uri: str


class FixtureProviderAdapter(ProviderAdapter):
    """Prompt-driven deterministic provider for backend E2E tests."""

    supports_inprocess_mcp = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs.get("on_canonical_event"))
        self.cwd = Path(kwargs.get("cwd") or ".").expanduser().resolve()
        self.agent_record = _coerce_agent_record(kwargs.get("agent_record"))
        self.provider_thread_id = _coerce_optional_str(kwargs.get("resume_thread_id"))
        invocation_plan = kwargs.get("invocation_plan")
        self._invocation_plan = invocation_plan if isinstance(invocation_plan, ProviderInvocationPlan) else None
        self.thread_path: str | None = None
        self._thread_state: ThreadState | None = None
        self._request_resolution: RequestResolution | None = None
        self._request_resolved = asyncio.Event()
        self._turn_task: asyncio.Task[None] | None = None
        self._active_turn_id: str | None = None
        self._native_logger: NativeLogger | None = None
        self._canonical_logger: CanonicalLogger | None = None
        process = type("FixtureProcess", (), {"pid": 4821, "returncode": None})()
        self.client = type("FixtureClient", (), {"is_running": True, "_process": process})()

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        if cwd is not None:
            self.cwd = Path(cwd).expanduser().resolve()
        self._ensure_loggers(cwd=str(self.cwd))
        self._log_native("fixture.session.started", {"cwd": str(self.cwd), "kwargs": dict(kwargs)})
        payload = {"serverInfo": {"name": "fixture-provider"}}
        await self._emit(
            {
                "type": "session.started",
                "cwd": str(self.cwd),
                "provider_payload": payload,
            }
        )
        return payload

    async def stop_session(self) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        if self.client._process.returncode is None:
            self.client._process.returncode = 0
        self.client.is_running = False
        self._log_native("fixture.session.stopped", {"cwd": str(self.cwd)})
        await self._emit({"type": "session.state.changed", "state": "stopped"})

    async def start_thread(self, **kwargs: Any) -> Any:
        self._ensure_loggers(cwd=str(self.cwd))
        self.provider_thread_id = f"fixture-thread-{uuid4().hex[:8]}"
        self._thread_state = ThreadState(thread_id=self.provider_thread_id)
        self.thread_path = str(self._thread_state_path(self.provider_thread_id))
        self._persist_thread_state()
        self._persist_thread_metadata()
        self._log_native("fixture.thread.started", {"thread_id": self.provider_thread_id, "kwargs": dict(kwargs)})
        thread = {"id": self.provider_thread_id, "path": self.thread_path}
        await self._emit(
            {
                "type": "thread.started",
                "provider_thread_id": self.provider_thread_id,
                "resumed": False,
                "thread": thread,
                "thread_path": self.thread_path,
            }
        )
        return {"thread": thread}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        self._ensure_loggers(cwd=str(self.cwd))
        self.provider_thread_id = provider_thread_id
        self._thread_state = self._load_thread_state(provider_thread_id)
        self.thread_path = str(self._thread_state_path(provider_thread_id))
        self._persist_thread_state()
        self._persist_thread_metadata()
        self._log_native("fixture.thread.resumed", {"thread_id": provider_thread_id, "kwargs": dict(kwargs)})
        thread = {"id": provider_thread_id, "path": self.thread_path}
        await self._emit(
            {
                "type": "thread.started",
                "provider_thread_id": self.provider_thread_id,
                "resumed": True,
                "thread": thread,
                "thread_path": self.thread_path,
            }
        )
        return {"thread": thread}

    async def start_turn(
        self,
        *,
        input_items: list[dict[str, Any]],
        runtime_mode: RuntimeMode,
        approval_policy: str,
        **kwargs: Any,
    ) -> Any:
        prompt = _prompt_text(input_items)
        scenario = parse_fixture_scenario(prompt)
        turn_id = f"fixture-turn-{uuid4().hex[:8]}"
        self._active_turn_id = turn_id
        self._request_resolution = None
        self._request_resolved = asyncio.Event()
        self._log_native(
            "fixture.turn.started",
            {
                "turn_id": turn_id,
                "runtime_mode": runtime_mode.value,
                "approval_policy": approval_policy,
                "prompt_summary": _compact_prompt_summary(prompt),
                "kwargs": dict(kwargs),
            },
        )
        self._turn_task = asyncio.create_task(
            self._run_turn(
                prompt=prompt,
                scenario=scenario,
                turn_id=turn_id,
                runtime_mode=runtime_mode,
                approval_policy=approval_policy,
            ),
            name=f"fixture-provider-{turn_id}",
        )
        return {"turn": {"id": turn_id, "status": "inProgress"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        self._log_native("fixture.turn.interrupt", {"turn_id": self._active_turn_id, "kwargs": dict(kwargs)})
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        if self._active_turn_id is not None:
            await self._emit(
                {
                    "type": "turn.completed",
                    "turn_id": self._active_turn_id,
                    "turn": {"id": self._active_turn_id, "status": "interrupted"},
                    "turn_status": "interrupted",
                }
            )
        return kwargs

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        payload = {"method": method, "params": dict(params or {}), **kwargs}
        self._log_native("fixture.request.sent", payload)
        return payload

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> Any:
        resolved_error = dict(error) if error else None
        self._request_resolution = RequestResolution(result=result, error=resolved_error)
        self._request_resolved.set()
        self._log_native(
            "fixture.request.responded",
            {
                "request_id": str(request_id),
                "result": result,
                "error": dict(resolved_error or {}),
            },
        )
        return {"request_id": str(request_id), "result": result, "error": dict(resolved_error or {})}

    async def on_canonical_event(self, event: CanonicalEvent) -> None:
        await self._emit(dict(event))

    async def _run_turn(
        self,
        *,
        prompt: str,
        scenario: FixtureScenario,
        turn_id: str,
        runtime_mode: RuntimeMode,
        approval_policy: str,
    ) -> None:
        try:
            self._bump_thread(prompt)
            await self._emit(
                {
                    "type": "turn.started",
                    "turn_id": turn_id,
                    "turn": {"id": turn_id, "status": "inProgress"},
                }
            )
            await self._emit_reasoning(turn_id, scenario, prompt=prompt)
            if self._should_use_mcp(prompt=prompt, scenario=scenario):
                await self._emit_mcp_interaction(turn_id, prompt=prompt)
            self._apply_file_mutations(scenario, runtime_mode=runtime_mode)

            if scenario.ask_question:
                request_id = self._next_request_id()
                await self._emit(
                    {
                        "type": "request.opened",
                        "turn_id": turn_id,
                        "request_id": request_id,
                        "request_kind": "user-input",
                        "method": "request_user_input",
                        "message": "Fixture provider needs one follow-up decision before continuing.",
                    }
                )
                await self._request_resolved.wait()
                resolution = self._request_resolution or RequestResolution()
                await self._emit(
                    {
                        "type": "request.resolved",
                        "turn_id": turn_id,
                        "request_id": request_id,
                        "request_kind": "user-input",
                        "method": "request_user_input",
                        "result": resolution.result,
                        "error": resolution.error,
                        "error_message": resolution.error_message,
                    }
                )
                if resolution.error is not None:
                    self.client._process.returncode = 1
                    self._log_native(
                        "fixture.turn.failed",
                        {
                            "turn_id": turn_id,
                            "request_id": request_id,
                            "error": resolution.error,
                        },
                    )
                    await self._emit(
                        {
                            "type": "runtime.error",
                            "turn_id": turn_id,
                            "error": resolution.error,
                            "error_message": resolution.error_message
                            or "Fixture provider request was rejected before completion.",
                        }
                    )
                    return

            if scenario.fail:
                self.client._process.returncode = 1
                self._log_native("fixture.turn.failed", {"turn_id": turn_id})
                await self._emit(
                    {
                        "type": "runtime.error",
                        "turn_id": turn_id,
                        "error_message": "Fixture provider forced a runtime failure.",
                    }
                )
                return

            response = _response_text(
                prompt=prompt,
                scenario=scenario,
                resolved_payload=None if self._request_resolution is None else self._request_resolution.result,
                runtime_mode=runtime_mode,
                approval_policy=approval_policy,
                mcp_used=self._should_use_mcp(prompt=prompt, scenario=scenario),
            )
            item_id = f"fixture-assistant-{uuid4().hex[:6]}"
            chunk_size = 18 if scenario.long_response else 28
            for chunk in _chunk_text(response, chunk_size=chunk_size):
                await self._emit(
                    {
                        "type": "content.delta",
                        "turn_id": turn_id,
                        "item_id": item_id,
                        "delta": chunk,
                    }
                )
                await asyncio.sleep(0.01)

            await self._emit(
                {
                    "type": "assistant.message.completed",
                    "turn_id": turn_id,
                    "item_id": item_id,
                    "text": response,
                }
            )
            await self._emit(
                {
                    "type": "turn.completed",
                    "turn_id": turn_id,
                    "turn": {"id": turn_id, "status": "completed"},
                    "turn_status": "completed",
                }
            )
            self.client._process.returncode = 0
            self._log_native("fixture.turn.completed", {"turn_id": turn_id})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.client._process.returncode = 1
            self._log_native(
                "fixture.turn.failed",
                {"turn_id": turn_id, "error_message": str(exc)},
            )
            await self._emit(
                {
                    "type": "runtime.error",
                    "turn_id": turn_id,
                    "error": {"message": str(exc)},
                    "error_message": str(exc),
                }
            )

    async def _emit_reasoning(self, turn_id: str, scenario: FixtureScenario, *, prompt: str) -> None:
        item_id = f"fixture-reasoning-{uuid4().hex[:6]}"
        reasoning = "Planning the deterministic fixture-provider response."
        if scenario.file_mutations:
            reasoning = "Preparing deterministic workspace edits requested by the prompt."
        if self._should_use_mcp(prompt=prompt, scenario=scenario):
            reasoning = "Preparing a deterministic MCP resource read through the bound orchestrator session."
        if scenario.ask_question:
            reasoning = "Waiting for one user-input request to be resolved before completing."
        if scenario.fail:
            reasoning = "Reproducing a deterministic runtime failure."

        for chunk in _chunk_text(reasoning, chunk_size=18):
            await self._emit(
                {
                    "type": "reasoning.summary.delta",
                    "turn_id": turn_id,
                    "item_id": item_id,
                    "delta": chunk,
                }
            )
            await asyncio.sleep(0.005)

        await self._emit(
            {
                "type": "task.progress",
                "turn_id": turn_id,
                "item": {"type": "reasoning", "id": item_id, "summary": [reasoning]},
            }
        )

    async def _emit_mcp_interaction(self, turn_id: str, *, prompt: str) -> None:
        interaction = self._require_mcp_resource_interaction(prompt=prompt)
        item_id = f"fixture-tool-{uuid4().hex[:6]}"
        tool_name = interaction.resource_name
        arguments = {
            "kind": "mcp.resource.read",
            "binding_id": interaction.binding_id,
            "uri": interaction.resource_uri,
        }
        self._log_native(
            "fixture.mcp.resource.read.started",
            {
                "binding_id": interaction.binding_id,
                "resource_name": interaction.resource_name,
                "resource_uri": interaction.resource_uri,
                "endpoint_url": interaction.endpoint_url,
            },
        )
        await self._emit(
            {
                "type": "tool.call.started",
                "turn_id": turn_id,
                "item_id": item_id,
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )
        payload_text = await self._read_mcp_resource(interaction)
        await self._emit(
            {
                "type": "tool.call.delta",
                "turn_id": turn_id,
                "item_id": item_id,
                "delta": interaction.resource_uri,
            }
        )
        await self._emit(
            {
                "type": "tool.call.completed",
                "turn_id": turn_id,
                "item_id": item_id,
                "tool_name": tool_name,
                "result": {
                    "binding_id": interaction.binding_id,
                    "resource_name": interaction.resource_name,
                    "resource_uri": interaction.resource_uri,
                    "payload": payload_text,
                },
            }
        )
        await self._emit(
            {
                "type": "task.progress",
                "turn_id": turn_id,
                "item": {
                    "type": "mcpResourceRead",
                    "id": item_id,
                    "resource": interaction.resource_name,
                    "uri": interaction.resource_uri,
                    "status": "completed",
                    "aggregatedOutput": payload_text,
                    "text": f"read MCP resource {interaction.resource_uri}\n{payload_text}",
                },
            }
        )
        self._log_native(
            "fixture.mcp.resource.read.completed",
            {
                "binding_id": interaction.binding_id,
                "resource_name": interaction.resource_name,
                "resource_uri": interaction.resource_uri,
                "payload": payload_text,
            },
        )

    def _can_use_mcp(self) -> bool:
        if self._invocation_plan is None:
            return False
        debug_access = self._invocation_plan.debug_metadata.get("mcp_access")
        if not isinstance(debug_access, dict):
            return False
        endpoint_url = _coerce_optional_str(debug_access.get("endpoint_url"))
        binding_id = _coerce_optional_str(self._invocation_plan.binding_id or debug_access.get("binding_id"))
        return endpoint_url is not None and binding_id is not None and any(
            resource_name in _READ_ONLY_RESOURCE_URIS for resource_name in self._invocation_plan.visible_resources
        )

    def _should_use_mcp(self, *, prompt: str, scenario: FixtureScenario) -> bool:
        return self._can_use_mcp() and not scenario.ask_question and _prompt_requests_mcp(prompt)

    def _require_mcp_resource_interaction(self, *, prompt: str) -> MCPResourceInteraction:
        if self._invocation_plan is None:
            raise RuntimeError("Fixture MCP interaction requested without an invocation plan.")
        debug_access = self._invocation_plan.debug_metadata.get("mcp_access")
        if not isinstance(debug_access, dict):
            raise RuntimeError("Fixture MCP interaction requested without bound MCP access metadata.")
        endpoint_url = _coerce_optional_str(debug_access.get("endpoint_url"))
        binding_id = _coerce_optional_str(self._invocation_plan.binding_id or debug_access.get("binding_id"))
        if endpoint_url is None or binding_id is None:
            raise RuntimeError("Fixture MCP interaction requested without a live MCP endpoint or binding id.")
        visible_resources = [
            resource_name
            for resource_name in self._invocation_plan.visible_resources
            if resource_name in _READ_ONLY_RESOURCE_URIS
        ]
        if not visible_resources:
            raise RuntimeError("Fixture MCP interaction requested but no readable MCP resources are available.")

        preferred_resources = [
            name
            for name in _mcp_resource_preferences(prompt)
            if name in visible_resources
        ]
        resource_name = preferred_resources[0] if preferred_resources else visible_resources[0]
        return MCPResourceInteraction(
            endpoint_url=endpoint_url,
            binding_id=binding_id,
            resource_name=resource_name,
            resource_uri=_READ_ONLY_RESOURCE_URIS[resource_name],
        )

    async def _read_mcp_resource(self, interaction: MCPResourceInteraction) -> str:
        asgi_app = None if self._invocation_plan is None else self._invocation_plan.debug_metadata.get("mcp_asgi_app")
        if asgi_app is not None:
            async with asgi_app.router.lifespan_context(asgi_app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=asgi_app),
                    base_url="http://127.0.0.1",
                    headers={BINDING_HEADER_NAME: interaction.binding_id},
                ) as http_client:
                    async with streamable_http_client(
                        interaction.endpoint_url,
                        http_client=http_client,
                        terminate_on_close=True,
                    ) as (read_stream, write_stream, _):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            resource_result = await session.read_resource(interaction.resource_uri)
        else:
            async with httpx.AsyncClient(headers={BINDING_HEADER_NAME: interaction.binding_id}) as http_client:
                async with streamable_http_client(
                    interaction.endpoint_url,
                    http_client=http_client,
                    terminate_on_close=True,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        resource_result = await session.read_resource(interaction.resource_uri)
        await asyncio.sleep(0.5)

        contents = getattr(resource_result, "contents", None)
        if isinstance(contents, list) and contents:
            first = contents[0]
            text = getattr(first, "text", None)
            if isinstance(text, str) and text:
                return text
            if hasattr(first, "model_dump"):
                return json.dumps(first.model_dump(mode="json"), sort_keys=True)
        if hasattr(resource_result, "model_dump"):
            return json.dumps(resource_result.model_dump(mode="json"), sort_keys=True)
        return json.dumps({"resource_uri": interaction.resource_uri})


    def _apply_file_mutations(self, scenario: FixtureScenario, *, runtime_mode: RuntimeMode) -> None:
        if not scenario.file_mutations:
            return
        if runtime_mode is RuntimeMode.READ_ONLY:
            self._log_native(
                "fixture.file.skipped",
                {"reason": "read_only_runtime_mode", "paths": [mutation.relative_path for mutation in scenario.file_mutations]},
            )
            return

        content = _normalized_file_content(scenario.content or "fixture-provider-change")
        for mutation in scenario.file_mutations:
            target_path = _resolve_workspace_path(self.cwd, mutation.relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if mutation.mode == "write":
                target_path.write_text(content, encoding="utf-8")
            else:
                with target_path.open("a", encoding="utf-8") as handle:
                    handle.write(content)
            self._log_native(
                f"fixture.file.{mutation.mode}",
                {"path": str(target_path), "content": content},
            )

    async def _emit(self, event: dict[str, Any]) -> None:
        self._ensure_loggers(cwd=str(self.cwd))
        event.setdefault("timestamp", _timestamp_now())
        event.setdefault("origin", "provider")
        event.setdefault("provider", "fixture")
        if self.agent_record is not None:
            event.setdefault("agent_id", self.agent_record.identity.agent_id)
            event.setdefault("run_id", self.agent_record.identity.run_id)
        if self.provider_thread_id is not None:
            event.setdefault("provider_thread_id", self.provider_thread_id)
        self._log_canonical(event)
        if self.canonical_event_handler is not None:
            result = self.canonical_event_handler(event)
            if inspect.isawaitable(result):
                await result

    def _ensure_loggers(self, *, cwd: str | None = None) -> None:
        if self._native_logger is not None and self._canonical_logger is not None:
            return

        base_cwd = Path(cwd or self.cwd).expanduser().resolve()
        native_path = self._resolve_log_path(kind="native", base_cwd=base_cwd)
        canonical_path = self._resolve_log_path(kind="canonical", base_cwd=base_cwd)
        if self.agent_record is not None:
            self.agent_record.provider.native_event_log = str(native_path)
            self.agent_record.provider.canonical_event_log = str(canonical_path)
        self._native_logger = NativeLogger(native_path)
        self._canonical_logger = CanonicalLogger(canonical_path)

    def _resolve_log_path(self, *, kind: Literal["native", "canonical"], base_cwd: Path) -> Path:
        if self.agent_record is not None:
            configured = (
                self.agent_record.provider.native_event_log
                if kind == "native"
                else self.agent_record.provider.canonical_event_log
            )
            if configured:
                return Path(configured).expanduser().resolve()

        base_vibrant_dir = _durable_vibrant_dir(base_cwd=base_cwd, agent_record=self.agent_record)
        run_id = self.agent_record.identity.run_id if self.agent_record is not None else "fixture-run"
        return base_vibrant_dir / "logs" / "providers" / kind / f"{run_id}.ndjson"

    def _thread_state_path(self, thread_id: str) -> Path:
        base_vibrant_dir = _durable_vibrant_dir(base_cwd=self.cwd, agent_record=self.agent_record)
        return base_vibrant_dir / "fixture-provider" / "threads" / f"{thread_id}.json"

    def _load_thread_state(self, thread_id: str) -> ThreadState:
        path = self._thread_state_path(thread_id)
        if not path.exists():
            return ThreadState(thread_id=thread_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ThreadState(
            thread_id=thread_id,
            turn_count=int(payload.get("turn_count", 0)),
            request_count=int(payload.get("request_count", 0)),
            last_prompt_summary=_coerce_optional_str(payload.get("last_prompt_summary")),
        )

    def _persist_thread_state(self) -> None:
        if self._thread_state is None:
            return
        path = self._thread_state_path(self._thread_state.thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self._thread_state), indent=2) + "\n", encoding="utf-8")

    def _persist_thread_metadata(self) -> None:
        if self.agent_record is None or self.provider_thread_id is None:
            return
        self.agent_record.provider.provider_thread_id = self.provider_thread_id
        self.agent_record.provider.thread_path = self.thread_path
        resume_cursor = {"threadId": self.provider_thread_id}
        if self._thread_state is not None:
            resume_cursor["turnCount"] = self._thread_state.turn_count
        self.agent_record.provider.resume_cursor = resume_cursor

    def _bump_thread(self, prompt: str) -> None:
        if self.provider_thread_id is None:
            self.provider_thread_id = f"fixture-thread-{uuid4().hex[:8]}"
        if self._thread_state is None:
            self._thread_state = ThreadState(thread_id=self.provider_thread_id)
        self._thread_state.turn_count += 1
        self._thread_state.last_prompt_summary = _compact_prompt_summary(prompt)
        self.thread_path = str(self._thread_state_path(self.provider_thread_id))
        self._persist_thread_state()
        self._persist_thread_metadata()

    def _next_request_id(self) -> str:
        if self._thread_state is None:
            if self.provider_thread_id is None:
                self.provider_thread_id = f"fixture-thread-{uuid4().hex[:8]}"
            self._thread_state = ThreadState(thread_id=self.provider_thread_id)
        self._thread_state.request_count += 1
        self._persist_thread_state()
        self._persist_thread_metadata()
        return f"fixture-request-{self._thread_state.request_count}"

    def _log_native(self, event: str, data: dict[str, Any]) -> None:
        self._ensure_loggers(cwd=str(self.cwd))
        if self._native_logger is not None:
            self._native_logger.log(event, data, timestamp=_timestamp_now())

    def _log_canonical(self, event: dict[str, Any]) -> None:
        if self._canonical_logger is not None:
            self._canonical_logger.log_canonical(
                str(event.get("type") or "event"),
                {key: value for key, value in event.items() if key not in {"type", "timestamp"}},
                timestamp=str(event.get("timestamp") or _timestamp_now()),
            )


def parse_fixture_scenario(prompt: str) -> FixtureScenario:
    """Parse deterministic mock markers out of a full prompt."""

    file_mutations: list[FileMutation] = []
    content = _infer_content_from_prompt(prompt)
    ask_question = False
    fail = False
    long_response = False

    for match in _MARKER_PATTERN.finditer(prompt):
        directive = match.group(1).strip()
        normalized = directive.lower()
        if normalized.startswith("write "):
            relative_path = directive[6:].strip()
            if relative_path:
                file_mutations.append(FileMutation(mode="write", relative_path=relative_path))
            continue
        if normalized.startswith("append "):
            relative_path = directive[7:].strip()
            if relative_path:
                file_mutations.append(FileMutation(mode="append", relative_path=relative_path))
            continue
        if normalized.startswith("content "):
            content = directive[8:].strip()
            continue
        tokens = [token for token in re.split(r"[\s,_-]+", normalized) if token]
        for token in tokens:
            if token in {"question", "ask"}:
                ask_question = True
            elif token == "error":
                fail = True
            elif token == "long":
                long_response = True

    return FixtureScenario(
        file_mutations=tuple(file_mutations),
        content=content,
        ask_question=ask_question,
        fail=fail,
        long_response=long_response,
    )


def _coerce_agent_record(value: Any) -> AgentRecord | None:
    return value if isinstance(value, AgentRecord) else None


def _coerce_optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _prompt_text(input_items: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in input_items:
        if item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    return "\n".join(chunks).strip()


def _response_text(
    *,
    prompt: str,
    scenario: FixtureScenario,
    resolved_payload: Any | None,
    runtime_mode: RuntimeMode,
    approval_policy: str,
    mcp_used: bool,
) -> str:
    if "User Answer:" in prompt:
        answer = prompt.split("User Answer:", 1)[1].strip().splitlines()[0]
        return (
            "Recorded the submitted user answer in fixture mode.\n\n"
            f"- Answer: {answer}\n"
            "- Status: the deterministic provider completed normally."
        )

    prompt_summary = _compact_prompt_summary(prompt)
    response_lines = [
        "Fixture provider response.",
        "",
        f"- Prompt summary: {prompt_summary}",
        f"- Runtime mode: {runtime_mode.value}",
        f"- Approval policy: {approval_policy}",
    ]
    if mcp_used:
        response_lines.append("- MCP access: completed one real orchestrator-bound resource read.")
    if scenario.file_mutations:
        rendered = ", ".join(f"{mutation.mode}:{mutation.relative_path}" for mutation in scenario.file_mutations)
        response_lines.append(f"- File edits: {rendered}")
    if resolved_payload is not None:
        response_lines.append(f"- Resolved request payload: {resolved_payload}")
    response_lines.append("- Status: stream completed normally.")
    if scenario.long_response:
        response_lines.extend(
            [
                "",
                "This extra paragraph exists to exercise chunking, transcript persistence,",
                "and longer assistant output in the backend E2E fixture provider.",
            ]
        )
    return "\n".join(response_lines)


def _compact_prompt_summary(prompt: str) -> str:
    cleaned = _MARKER_PATTERN.sub("", prompt).strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "No prompt text supplied."
    return f"{cleaned[:117]}..." if len(cleaned) > 120 else cleaned


def _normalized_file_content(content: str) -> str:
    stripped = content.rstrip("\n")
    return f"{stripped}\n"


def _infer_content_from_prompt(prompt: str) -> str | None:
    cleaned = _MARKER_PATTERN.sub("", prompt)
    patterns = (
        re.compile(r"contain(?:s)?\s+`([^`]+)`", re.IGNORECASE),
        re.compile(r"write(?:s|)\s+`([^`]+)`", re.IGNORECASE),
        re.compile(r"content\s+`([^`]+)`", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(cleaned)
        if match is not None:
            inferred = match.group(1).strip()
            if inferred:
                return inferred
    return None


def _chunk_text(text: str, *, chunk_size: int) -> list[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)] or [""]


def _mcp_resource_preferences(prompt: str) -> tuple[str, ...]:
    normalized_prompt = " ".join(_MARKER_PATTERN.sub("", prompt).lower().split())
    preferences: list[str] = []

    if any(token in normalized_prompt for token in ("question", "answer", "decision", "oauth", "auth")):
        preferences.extend(
            [
                "vibrant.list_pending_questions",
                "vibrant.get_gatekeeper_session",
                "vibrant.get_workflow_session",
            ]
        )
    if any(token in normalized_prompt for token in ("task", "attempt", "review", "workspace", "demo.txt")):
        preferences.extend(
            [
                "vibrant.list_active_attempts",
                "vibrant.get_workflow_session",
                "vibrant.list_pending_review_tickets",
            ]
        )
    if any(token in normalized_prompt for token in ("workflow", "plan", "planning", "status")):
        preferences.extend(
            [
                "vibrant.get_workflow_session",
                "vibrant.get_workflow_status",
                "vibrant.get_roadmap",
            ]
        )

    preferences.extend(_PREFERRED_MCP_RESOURCES)

    ordered: list[str] = []
    for resource_name in preferences:
        if resource_name not in ordered:
            ordered.append(resource_name)
    return tuple(ordered)


def _prompt_requests_mcp(prompt: str) -> bool:
    normalized_prompt = " ".join(_MARKER_PATTERN.sub("", prompt).lower().split())
    return any(
        token in normalized_prompt
        for token in (
            "inspect",
            "workflow",
            "status",
            "review",
            "evidence",
            "orchestrator",
            "roadmap",
            "consensus",
        )
    )


def _resolve_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    target_path = (workspace_root / relative_path).resolve()
    if not target_path.is_relative_to(workspace_root):
        raise ValueError(f"Fixture file mutation escapes workspace: {relative_path}")
    return target_path


def _durable_vibrant_dir(*, base_cwd: Path, agent_record: AgentRecord | None) -> Path:
    if agent_record is not None:
        for candidate in (agent_record.provider.canonical_event_log, agent_record.provider.native_event_log):
            if not candidate:
                continue
            resolved = Path(candidate).expanduser().resolve()
            vibrant_dir = _find_vibrant_dir(resolved)
            if vibrant_dir is not None:
                return vibrant_dir
    vibrant_dir = _find_vibrant_dir(base_cwd)
    if vibrant_dir is not None:
        return vibrant_dir
    return (base_cwd / DEFAULT_CONFIG_DIR).resolve()


def _find_vibrant_dir(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.name == DEFAULT_CONFIG_DIR:
            return candidate
    return None


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
