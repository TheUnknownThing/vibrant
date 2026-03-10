"""Execution runtime service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from vibrant.agents.runtime import (
    AgentHandle,
    AgentRecordCallback,
    AgentRuntime,
    NormalizedRunResult,
    ProviderThreadHandle,
)
from vibrant.models.agent import AgentRecord
from vibrant.orchestrator.git_manager import GitWorktreeInfo
from vibrant.providers.base import CanonicalEvent

from ..types import RuntimeExecutionResult
from .agents import AgentRegistry

CanonicalEventCallback = Callable[[CanonicalEvent], Any]


class AgentRuntimeService:
    """Own provider adapter session/thread/turn execution details.

    This service is the orchestrator's entry point for running agents.
    It programs against the ``AgentRuntime`` protocol rather than
    driving adapter internals directly, giving the orchestrator a clean
    service boundary for agent execution.

    When a protocol-based ``AgentRuntime`` is supplied, all execution
    is delegated through it.  When only a legacy ``adapter_factory`` is
    supplied, backwards-compatible inline execution is used.
    """

    REQUEST_ERROR_MESSAGE = "Interactive provider requests are not supported during autonomous task execution."

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        adapter_factory: Any = None,
        config_getter: Callable[[], Any] | None = None,
        on_canonical_event: CanonicalEventCallback | None = None,
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.adapter_factory = adapter_factory
        self.config_getter = config_getter
        self.on_canonical_event = on_canonical_event
        self._agent_runtime = agent_runtime

    def _make_record_callback(self) -> AgentRecordCallback:
        """Build a callback that persists record mutations through the registry."""
        registry = self.agent_registry

        def _persist(record: AgentRecord) -> None:
            registry.upsert(record)

        return _persist

    async def run_task(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        resume_thread_id: str | None = None,
    ) -> RuntimeExecutionResult:
        """Run a task using the protocol runtime or the legacy path."""
        if self._agent_runtime is not None:
            return await self._run_via_protocol(
                worktree=worktree,
                prompt=prompt,
                agent_record=agent_record,
                resume_thread_id=resume_thread_id,
            )
        return await self._run_legacy(
            worktree=worktree,
            prompt=prompt,
            agent_record=agent_record,
        )

    async def start_task(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        resume_thread_id: str | None = None,
    ) -> AgentHandle:
        """Start an agent and return the durable handle immediately.

        This is the preferred entry point for callers that want to
        observe run-state, await completion asynchronously, or inspect
        the provider-thread handle for resume/recovery.

        Requires a protocol-based ``AgentRuntime``.
        """
        if self._agent_runtime is None:
            raise RuntimeError(
                "start_task() requires a protocol-based AgentRuntime; "
                "configure agent_runtime on AgentRuntimeService"
            )
        agent_record.started_at = datetime.now(timezone.utc)
        self.agent_registry.upsert(agent_record, increment_spawn=True)

        return await self._agent_runtime.start(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(worktree.path),
            resume_thread_id=resume_thread_id,
            on_record_updated=self._make_record_callback(),
        )

    async def resume_task(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        provider_thread: ProviderThreadHandle,
    ) -> AgentHandle:
        """Resume a previously interrupted agent via its durable thread handle.

        Requires a protocol-based ``AgentRuntime`` that supports
        ``resume_run()``.
        """
        if self._agent_runtime is None:
            raise RuntimeError(
                "resume_task() requires a protocol-based AgentRuntime; "
                "configure agent_runtime on AgentRuntimeService"
            )
        self.agent_registry.upsert(agent_record)

        return await self._agent_runtime.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            provider_thread=provider_thread,
            cwd=str(worktree.path),
            on_record_updated=self._make_record_callback(),
        )

    # ------------------------------------------------------------------
    # Protocol-based execution
    # ------------------------------------------------------------------

    async def _run_via_protocol(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        resume_thread_id: str | None = None,
    ) -> RuntimeExecutionResult:
        handle = await self.start_task(
            worktree=worktree,
            prompt=prompt,
            agent_record=agent_record,
            resume_thread_id=resume_thread_id,
        )
        result = await handle.wait()
        return _normalized_to_execution_result(result)

    # ------------------------------------------------------------------
    # Legacy inline execution (adapter_factory path)
    # ------------------------------------------------------------------

    async def _run_legacy(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
    ) -> RuntimeExecutionResult:
        """Original inline adapter lifecycle — kept for backwards compat."""
        import asyncio

        from vibrant.agents.utils import (
            extract_error_message,
            extract_exit_code,
            extract_pid,
            extract_summary_from_turn_result,
            extract_text_from_progress_item,
            maybe_forward_event,
            parse_runtime_mode,
            stop_adapter_safely,
            transition_terminal_agent,
        )
        from vibrant.models.agent import AgentStatus

        assert self.config_getter is not None
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
                text = extract_text_from_progress_item(event_copy.get("item"))
                if text:
                    transcript_chunks.append(text)
            elif event_type == "runtime.error":
                runtime_error = extract_error_message(event_copy)
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

            await maybe_forward_event(self.on_canonical_event, event_copy)

        agent_record.started_at = datetime.now(timezone.utc)
        self.agent_registry.upsert(agent_record, increment_spawn=True)

        thread_runtime_mode = parse_runtime_mode(config.sandbox_mode)
        turn_runtime_mode = parse_runtime_mode(config.turn_sandbox_policy or config.sandbox_mode)
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
            agent_record.pid = extract_pid(adapter)
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
                await stop_adapter_safely(adapter)

        transcript = "".join(transcript_chunks).strip()
        exit_code = extract_exit_code(adapter)

        if runtime_error:
            agent_record.summary = transcript or agent_record.summary
            transition_terminal_agent(
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

        agent_record.summary = transcript or extract_summary_from_turn_result(turn_result)
        transition_terminal_agent(
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


def _normalized_to_execution_result(result: NormalizedRunResult) -> RuntimeExecutionResult:
    """Bridge a ``NormalizedRunResult`` to the orchestrator's existing type."""
    return RuntimeExecutionResult(
        agent_record=result.agent_record,
        events=result.events,
        summary=result.summary,
        error=result.error,
        turn_result=result.turn_result,
    )
