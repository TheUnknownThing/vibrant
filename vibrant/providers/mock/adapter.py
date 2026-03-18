"""Deterministic mock provider adapter for local TUI development."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from vibrant.models.agent import AgentRecord
from vibrant.providers.base import CanonicalEvent, CanonicalEventHandler, ProviderAdapter, RuntimeMode
from vibrant.type_defs import JSONMapping, JSONObject, JSONValue, RequestId, is_json_mapping

_MARKER_PATTERN = re.compile(r"\[mock:([a-z_, -]+)\]", re.IGNORECASE)


@dataclass(slots=True)
class _MockScenario:
    use_tool: bool = False
    ask_question: bool = False
    fail: bool = False
    long_response: bool = False


class MockCodexAdapter(ProviderAdapter):
    """Minimal Codex-like adapter that emits canonical events directly."""

    def __init__(
        self,
        *,
        cwd: str | None = None,
        agent_record: AgentRecord | None = None,
        resume_thread_id: str | None = None,
        on_canonical_event: CanonicalEventHandler | None = None,
        **_ignored: object,
    ) -> None:
        super().__init__(on_canonical_event)
        self.cwd = Path(cwd or ".").expanduser().resolve()
        self.agent_record = agent_record
        self.provider_thread_id = resume_thread_id
        self.thread_path: str | None = None
        self._resolved_request_payload: JSONValue | None = None
        self._request_resolved = asyncio.Event()
        self._turn_task: asyncio.Task[None] | None = None
        self._active_turn_id: str | None = None
        self._thread_instructions: str | None = None
        self._inject_thread_instructions_next_turn = False
        process = type("MockProcess", (), {"pid": 4512, "returncode": None})()
        self.client = type("MockClient", (), {"is_running": True, "_process": process})()

    async def start_session(self, *, cwd: str | None = None, **kwargs: JSONValue) -> JSONObject:
        if cwd is not None:
            self.cwd = Path(cwd).expanduser().resolve()
        await self._emit(
            {
                "type": "session.started",
                "cwd": str(self.cwd),
                "provider_payload": {"serverInfo": {"name": "mock-codex"}},
            }
        )
        return {"serverInfo": {"name": "mock-codex"}, "cwd": str(self.cwd), **kwargs}

    async def stop_session(self) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        self.client._process.returncode = 0
        self.client.is_running = False
        await self._emit({"type": "session.state.changed", "state": "stopped"})

    async def start_thread(self, **kwargs: JSONValue) -> JSONObject:
        instructions = kwargs.get("instructions")
        self._thread_instructions = instructions.strip() if isinstance(instructions, str) and instructions.strip() else None
        self._inject_thread_instructions_next_turn = bool(self._thread_instructions)
        self.provider_thread_id = f"mock-thread-{uuid4().hex[:8]}"
        self.thread_path = str(self.cwd / ".vibrant" / "mock" / f"{self.provider_thread_id}.jsonl")
        self._persist_thread_metadata()
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
        return {"thread": thread, **kwargs}

    async def resume_thread(self, provider_thread_id: str, **kwargs: JSONValue) -> JSONObject:
        instructions = kwargs.get("instructions")
        self._thread_instructions = instructions.strip() if isinstance(instructions, str) and instructions.strip() else None
        self._inject_thread_instructions_next_turn = bool(self._thread_instructions)
        self.provider_thread_id = provider_thread_id
        self.thread_path = str(self.cwd / ".vibrant" / "mock" / f"{provider_thread_id}.jsonl")
        self._persist_thread_metadata()
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
        return {"thread": thread, **kwargs}

    async def start_turn(
        self,
        *,
        input_items: Sequence[JSONMapping],
        runtime_mode: RuntimeMode,
        approval_policy: str,
        **kwargs: JSONValue,
    ) -> JSONObject:
        prompt = _prompt_text(input_items)
        if self._inject_thread_instructions_next_turn and self._thread_instructions:
            prompt = f"{self._thread_instructions}\n\n{prompt}".strip()
            self._inject_thread_instructions_next_turn = False
        turn_id = f"mock-turn-{uuid4().hex[:8]}"
        scenario = _parse_scenario(prompt)
        self._active_turn_id = turn_id
        self._resolved_request_payload = None
        self._request_resolved = asyncio.Event()
        self._turn_task = asyncio.create_task(
            self._run_turn(
                prompt=prompt,
                scenario=scenario,
                turn_id=turn_id,
                runtime_mode=runtime_mode,
                approval_policy=approval_policy,
            ),
            name=f"mock-provider-{turn_id}",
        )
        return {"turn": {"id": turn_id, "status": "inProgress"}, **kwargs}

    async def interrupt_turn(self, **kwargs: JSONValue) -> JSONObject:
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
        params: JSONMapping | None = None,
        **kwargs: JSONValue,
    ) -> JSONObject:
        return {"method": method, "params": dict(params or {}), **kwargs}

    async def respond_to_request(
        self,
        request_id: RequestId,
        *,
        result: JSONValue | None = None,
        error: JSONMapping | None = None,
    ) -> JSONObject:
        self._resolved_request_payload = result if error is None else dict(error)
        self._request_resolved.set()
        return {"request_id": str(request_id), "result": result, "error": dict(error or {})}

    async def on_canonical_event(self, event: CanonicalEvent) -> None:
        await self._emit(dict(event))

    async def _run_turn(
        self,
        *,
        prompt: str,
        scenario: _MockScenario,
        turn_id: str,
        runtime_mode: RuntimeMode,
        approval_policy: str,
    ) -> None:
        await self._emit({"type": "turn.started", "turn_id": turn_id, "turn": {"id": turn_id, "status": "inProgress"}})
        await self._emit_reasoning(turn_id, scenario)

        if scenario.use_tool:
            await self._emit_tool_call(turn_id)

        if scenario.ask_question:
            await self._emit(
                {
                    "type": "request.opened",
                    "turn_id": turn_id,
                    "request_id": "mock-request-1",
                    "request_kind": "user-input",
                    "method": "request_user_input",
                    "message": "Mock Gatekeeper needs one decision before continuing.",
                }
            )
            await self._request_resolved.wait()
            await self._emit(
                {
                    "type": "request.resolved",
                    "turn_id": turn_id,
                    "request_id": "mock-request-1",
                    "request_kind": "user-input",
                    "method": "request_user_input",
                    "result": self._resolved_request_payload,
                }
            )

        if scenario.fail:
            await self._emit(
                {
                    "type": "runtime.error",
                    "turn_id": turn_id,
                    "error_message": "Mock provider forced a runtime failure.",
                }
            )
            self.client._process.returncode = 1
            return

        response = _response_text(
            prompt=prompt,
            scenario=scenario,
            resolved_payload=self._resolved_request_payload,
            runtime_mode=runtime_mode,
            approval_policy=approval_policy,
        )
        item_id = f"mock-assistant-{uuid4().hex[:6]}"
        for chunk in _chunk_text(response, chunk_size=18 if scenario.long_response else 24):
            await self._emit({"type": "content.delta", "turn_id": turn_id, "item_id": item_id, "delta": chunk})
            await asyncio.sleep(0.03)

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

    async def _emit_reasoning(self, turn_id: str, scenario: _MockScenario) -> None:
        item_id = f"mock-reasoning-{uuid4().hex[:6]}"
        reasoning = "Planning the mock response path."
        if scenario.use_tool:
            reasoning = "Reviewing the request and preparing a mock tool call."
        if scenario.ask_question:
            reasoning = "Identifying the missing user decision before continuing."
        if scenario.fail:
            reasoning = "Reproducing an error state for the interface."

        for chunk in _chunk_text(reasoning, chunk_size=16):
            await self._emit(
                {
                    "type": "reasoning.summary.delta",
                    "turn_id": turn_id,
                    "item_id": item_id,
                    "delta": chunk,
                }
            )
            await asyncio.sleep(0.02)

        await self._emit(
            {
                "type": "task.progress",
                "turn_id": turn_id,
                "item": {"type": "reasoning", "id": item_id, "summary": [reasoning]},
            }
        )

    async def _emit_tool_call(self, turn_id: str) -> None:
        item_id = f"mock-tool-{uuid4().hex[:6]}"
        tool_name = "functions.exec_command"
        arguments = {"cmd": "echo mock-check"}
        await self._emit(
            {
                "type": "tool.call.started",
                "turn_id": turn_id,
                "item_id": item_id,
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )
        await asyncio.sleep(0.05)
        await self._emit(
            {
                "type": "tool.call.delta",
                "turn_id": turn_id,
                "item_id": item_id,
                "delta": "echo mock-check",
            }
        )
        await asyncio.sleep(0.05)
        await self._emit(
            {
                "type": "tool.call.completed",
                "turn_id": turn_id,
                "item_id": item_id,
                "tool_name": tool_name,
                "result": {"exitCode": 0, "output": "mock-check"},
            }
        )
        await self._emit(
            {
                "type": "task.progress",
                "turn_id": turn_id,
                "item": {
                    "type": "commandExecution",
                    "id": item_id,
                    "command": "echo mock-check",
                    "status": "completed",
                    "aggregatedOutput": "mock-check",
                    "exitCode": 0,
                    "text": "command completed (exit 0) [0ms]\nmock-check",
                },
            }
        )

    async def _emit(self, event: CanonicalEvent) -> None:
        event.setdefault("timestamp", _timestamp_now())
        event.setdefault("origin", "provider")
        event.setdefault("provider", "mock")
        event.setdefault("provider_thread_id", self.provider_thread_id)
        if self.canonical_event_handler is not None:
            result = self.canonical_event_handler(event)
            if asyncio.iscoroutine(result):
                await result

    def _persist_thread_metadata(self) -> None:
        if self.agent_record is None or self.provider_thread_id is None:
            return
        self.agent_record.provider.provider_thread_id = self.provider_thread_id
        self.agent_record.provider.thread_path = self.thread_path
        self.agent_record.provider.resume_cursor = {"threadId": self.provider_thread_id}


def _coerce_agent_record(value: object) -> AgentRecord | None:
    return value if isinstance(value, AgentRecord) else None


def _coerce_optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _prompt_text(input_items: Sequence[JSONMapping]) -> str:
    chunks: list[str] = []
    for item in input_items:
        if item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    return "\n".join(chunks).strip()


def _parse_scenario(prompt: str) -> _MockScenario:
    scenario = _MockScenario()
    for match in _MARKER_PATTERN.finditer(prompt):
        for token in re.split(r"[\s,_-]+", match.group(1).strip().lower()):
            if token == "tool":
                scenario.use_tool = True
            elif token in {"question", "ask"}:
                scenario.ask_question = True
            elif token == "error":
                scenario.fail = True
            elif token == "long":
                scenario.long_response = True
    return scenario


def _response_text(
    *,
    prompt: str,
    scenario: _MockScenario,
    resolved_payload: JSONValue | None,
    runtime_mode: RuntimeMode,
    approval_policy: str,
) -> str:
    if "User Answer:" in prompt:
        answer = prompt.split("User Answer:", 1)[1].strip().splitlines()[0]
        return (
            "Recorded your answer in mock mode.\n\n"
            f"- Answer: {answer}\n"
            "- Next step: continue the conversation without waiting for a real Codex run."
        )

    if scenario.ask_question and resolved_payload is not None:
        return (
            "The mock question was resolved.\n\n"
            f"- Captured response: {resolved_payload}\n"
            "- You can keep using mock mode to test additional turns."
        )

    prompt_summary = _compact_prompt_summary(prompt)
    response = (
        "Mock Gatekeeper response.\n\n"
        f"- Prompt summary: {prompt_summary}\n"
        f"- Runtime mode: {runtime_mode.value}\n"
        f"- Approval policy: {approval_policy}\n"
        "- Status: stream completed normally."
    )
    if scenario.long_response:
        response = (
            f"{response}\n\n"
            "This long-form mock response is meant to exercise scrolling, markdown wrapping, "
            "and transcript persistence across multiple turns in the TUI."
        )
    return response


def _compact_prompt_summary(prompt: str) -> str:
    cleaned = _MARKER_PATTERN.sub("", prompt).strip()
    if "## Trigger" in cleaned:
        cleaned = cleaned.split("## Trigger", 1)[1].strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "No prompt text supplied."
    return f"{cleaned[:117]}..." if len(cleaned) > 120 else cleaned


def _chunk_text(text: str, *, chunk_size: int) -> list[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)] or [""]


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
