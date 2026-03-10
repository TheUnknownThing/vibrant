"""Execution runtime service."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from vibrant.gatekeeper.gatekeeper import _extract_error_message, _extract_text_from_progress_item, _stop_adapter_safely
from vibrant.models.agent import AgentRecord, AgentStatus
from vibrant.orchestrator.git_manager import GitWorktreeInfo
from vibrant.providers.base import CanonicalEvent, RuntimeMode

from ..types import RuntimeExecutionResult
from .agents import AgentRegistry

CanonicalEventCallback = Callable[[CanonicalEvent], Any]


class AgentRuntimeService:
    """Own provider adapter session/thread/turn execution details."""

    REQUEST_ERROR_MESSAGE = "Interactive provider requests are not supported during autonomous task execution."

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        adapter_factory: Any,
        config_getter: Callable[[], Any],
        on_canonical_event: CanonicalEventCallback | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.adapter_factory = adapter_factory
        self.config_getter = config_getter
        self.on_canonical_event = on_canonical_event

    async def run_task(self, *, worktree: GitWorktreeInfo, prompt: str, agent_record: AgentRecord) -> RuntimeExecutionResult:
        config = self.config_getter()
        events: list[CanonicalEvent] = []
        transcript_chunks: list[str] = []
        turn_finished = asyncio.Event()
        runtime_error: str | None = None
        adapter: Any | None = None

        async def handle_event(event: CanonicalEvent) -> None:
            nonlocal runtime_error, adapter

            event_copy = dict(event)
            events.append(event_copy)
            event_type = str(event_copy.get("type") or "")
            if event_type == "content.delta":
                transcript_chunks.append(str(event_copy.get("delta", "")))
            elif event_type == "task.progress":
                text = _extract_text_from_progress_item(event_copy.get("item"))
                if text:
                    transcript_chunks.append(text)
            elif event_type == "runtime.error":
                runtime_error = _extract_error_message(event_copy)
                turn_finished.set()
            elif event_type == "turn.completed":
                turn_finished.set()
            elif event_type == "request.opened":
                request_id = event_copy.get("request_id")
                request_kind = str(event_copy.get("request_kind") or "request")
                runtime_error = f"{self.REQUEST_ERROR_MESSAGE} ({request_kind})"
                if adapter is not None and request_id is not None:
                    await adapter.respond_to_request(
                        request_id,
                        error={"code": -32000, "message": runtime_error},
                    )
                turn_finished.set()

            await _maybe_forward_event(self.on_canonical_event, event_copy)

        agent_record.started_at = datetime.now(timezone.utc)
        self.agent_registry.upsert(agent_record, increment_spawn=True)

        thread_runtime_mode = _parse_runtime_mode(config.sandbox_mode)
        turn_runtime_mode = _parse_runtime_mode(config.turn_sandbox_policy or config.sandbox_mode)
        turn_result: Any | None = None

        try:
            agent_record.transition_to(AgentStatus.CONNECTING)
            self.agent_registry.upsert(agent_record)

            adapter = self.adapter_factory(
                cwd=str(worktree.path),
                codex_binary=config.codex_binary,
                codex_home=config.codex_home,
                agent_record=agent_record,
                on_canonical_event=handle_event,
            )
            await adapter.start_session(cwd=str(worktree.path))
            agent_record.pid = _extract_pid(adapter)
            self.agent_registry.upsert(agent_record)

            await adapter.start_thread(
                model=config.model,
                cwd=str(worktree.path),
                runtime_mode=thread_runtime_mode,
                approval_policy=config.approval_policy,
                model_provider=config.model_provider,
                reasoning_effort=config.reasoning_effort,
                reasoning_summary=config.reasoning_summary,
                extra_config=config.extra_config,
            )

            agent_record.transition_to(AgentStatus.RUNNING)
            self.agent_registry.upsert(agent_record)

            turn_result = await adapter.start_turn(
                input_items=[{"type": "text", "text": prompt, "text_elements": []}],
                runtime_mode=turn_runtime_mode,
                approval_policy=config.approval_policy,
            )
            await asyncio.wait_for(turn_finished.wait(), timeout=float(config.agent_timeout_seconds))
        except Exception as exc:
            if runtime_error is None:
                runtime_error = str(exc)
        finally:
            if adapter is not None:
                await _stop_adapter_safely(adapter)

        transcript = "".join(transcript_chunks).strip()
        exit_code = _extract_exit_code(adapter)

        if runtime_error:
            agent_record.summary = transcript or agent_record.summary
            _transition_terminal_agent(
                agent_record,
                AgentStatus.FAILED,
                exit_code=exit_code if exit_code is not None else 1,
                error=runtime_error,
            )
            self.agent_registry.upsert(agent_record)
            return RuntimeExecutionResult(
                agent_record=agent_record,
                events=events,
                summary=transcript or None,
                error=runtime_error,
                turn_result=turn_result,
            )

        agent_record.summary = transcript or _extract_summary_from_turn_result(turn_result)
        _transition_terminal_agent(
            agent_record,
            AgentStatus.COMPLETED,
            exit_code=exit_code if exit_code is not None else 0,
        )
        self.agent_registry.upsert(agent_record)
        return RuntimeExecutionResult(
            agent_record=agent_record,
            events=events,
            summary=agent_record.summary,
            turn_result=turn_result,
        )


async def _maybe_forward_event(handler: CanonicalEventCallback | None, event: CanonicalEvent) -> None:
    if handler is None:
        return
    callback_result = handler(event)
    if inspect.isawaitable(callback_result):
        await callback_result


def _parse_runtime_mode(value: str | None) -> RuntimeMode:
    normalized = (value or RuntimeMode.WORKSPACE_WRITE.value).strip()
    if not normalized:
        return RuntimeMode.WORKSPACE_WRITE

    key = normalized.replace("-", "_")
    lowered = key.lower()
    mapping = {
        "read_only": RuntimeMode.READ_ONLY,
        "readonly": RuntimeMode.READ_ONLY,
        "workspace_write": RuntimeMode.WORKSPACE_WRITE,
        "workspacewrite": RuntimeMode.WORKSPACE_WRITE,
        "full_access": RuntimeMode.FULL_ACCESS,
        "danger_full_access": RuntimeMode.FULL_ACCESS,
        "dangerfullaccess": RuntimeMode.FULL_ACCESS,
    }
    try:
        return mapping[lowered]
    except KeyError as exc:
        raise ValueError(f"Unsupported runtime mode: {value}") from exc


def _extract_pid(adapter: Any) -> int | None:
    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) else None


def _extract_exit_code(adapter: Any | None) -> int | None:
    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    returncode = getattr(process, "returncode", None)
    return returncode if isinstance(returncode, int) else None


def _extract_summary_from_turn_result(turn_result: Any) -> str | None:
    if not isinstance(turn_result, dict):
        return None

    candidates: list[Any] = [turn_result]
    turn_payload = turn_result.get("turn")
    if isinstance(turn_payload, dict):
        candidates.append(turn_payload)

    for candidate in candidates:
        if isinstance(candidate.get("summary"), str) and candidate["summary"].strip():
            return candidate["summary"].strip()
        output_text = candidate.get("outputText") or candidate.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

    return None


def _transition_terminal_agent(
    agent_record: AgentRecord,
    status: AgentStatus,
    *,
    exit_code: int,
    error: str | None = None,
) -> None:
    if agent_record.can_transition_to(status):
        agent_record.transition_to(status, exit_code=exit_code, error=error)
        return

    agent_record.exit_code = exit_code
    agent_record.error = error
    if agent_record.finished_at is None:
        agent_record.finished_at = datetime.now(timezone.utc)
