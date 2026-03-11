"""Execution runtime service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from vibrant.agents.runtime import (
    AgentHandle,
    AgentRecordCallback,
    AgentRuntime,
    InputRequest,
    NormalizedRunResult,
    ProviderResumeHandle,
    ProviderThreadHandle,
    RunState,
)
from vibrant.models.agent import AgentRecord
from vibrant.orchestrator.execution.git_manager import GitWorktreeInfo
from vibrant.providers.base import CanonicalEvent

from ..types import RuntimeExecutionResult
from .registry import AgentRegistry

CanonicalEventCallback = Callable[[CanonicalEvent], Any]
RuntimeFactory = Callable[[AgentRecord], AgentRuntime]


@dataclass(slots=True)
class RuntimeHandleSnapshot:
    """Serializable view of an in-flight or completed agent handle."""

    agent_id: str
    task_id: str
    agent_type: str
    state: str
    status: str
    done: bool
    awaiting_input: bool
    summary: str | None = None
    error: str | None = None
    provider_thread_id: str | None = None
    provider_thread_path: str | None = None
    provider_resume_cursor: dict[str, Any] | None = None
    input_requests: list[InputRequest] = field(default_factory=list)


class AgentRuntimeService:
    """Own provider adapter session/thread/turn execution details.

    This service is the orchestrator's entry point for running agents.
    It programs against the ``AgentRuntime`` protocol rather than
    driving adapter internals directly, giving the orchestrator a clean
    service boundary for agent execution.

    The service also owns orchestrator-facing handle tracking so future
    MCP surfaces can list in-flight runs, inspect suspension state,
    resume them, and wait for normalized results without reaching into
    provider internals.
    """

    REQUEST_ERROR_MESSAGE = "Interactive provider requests are not supported during autonomous task execution."

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        adapter_factory: Any = None,
        config_getter: Callable[[], Any] | None = None,
        on_canonical_event: CanonicalEventCallback | None = None,
        agent_runtime: AgentRuntime | RuntimeFactory | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.adapter_factory = adapter_factory
        self.config_getter = config_getter
        self.on_canonical_event = on_canonical_event
        self._agent_runtime = agent_runtime
        self._handles: dict[str, AgentHandle] = {}

    @property
    def supports_handles(self) -> bool:
        return self._agent_runtime is not None

    def _make_record_callback(self) -> AgentRecordCallback:
        """Build a callback that persists record mutations through the registry."""
        registry = self.agent_registry

        def _persist(record: AgentRecord) -> None:
            registry.upsert(record)

        return _persist

    def _resolve_runtime(self, agent_record: AgentRecord) -> AgentRuntime:
        runtime = self._agent_runtime
        if runtime is None:
            raise RuntimeError("No protocol-based AgentRuntime configured")
        if hasattr(runtime, "start"):
            return runtime  # type: ignore[return-value]
        candidate = runtime(agent_record)
        if not hasattr(candidate, "start"):
            raise TypeError("agent_runtime factory must return an AgentRuntime-compatible object")
        return candidate

    def get_handle(self, agent_id: str) -> AgentHandle | None:
        """Return the currently tracked handle for an agent, if one exists."""
        return self._handles.get(agent_id)

    def release_handle(self, agent_id: str) -> AgentHandle | None:
        """Stop tracking a handle in the runtime service."""
        return self._handles.pop(agent_id, None)

    def _agent_id_for_handle(self, handle: AgentHandle) -> str | None:
        for candidate_agent_id, candidate_handle in self._handles.items():
            if candidate_handle is handle:
                return candidate_agent_id
        return None

    def _resolve_provider_thread(
        self,
        *,
        agent_record: AgentRecord,
        provider_thread: ProviderThreadHandle | None,
    ) -> ProviderThreadHandle:
        if provider_thread is not None:
            return provider_thread
        persisted = self.agent_registry.provider_thread_handle(agent_record.agent_id)
        if persisted is not None:
            return persisted
        return ProviderResumeHandle.from_provider_metadata(agent_record.provider) or ProviderResumeHandle(
            kind=agent_record.provider.kind
        )

    def snapshot_handle(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        agent_record: AgentRecord | None = None,
    ) -> RuntimeHandleSnapshot:
        """Return a serializable snapshot for a tracked handle."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        if agent_record is None:
            key = agent_id or self._agent_id_for_handle(handle)
            if key is None:
                raise ValueError("agent_id or agent_record is required")
            agent_record = self.agent_registry.get(key)
        if agent_record is None:
            raise KeyError(f"Unknown agent record: {agent_id}")

        provider_thread = handle.provider_thread
        if provider_thread.empty:
            provider_thread = self._resolve_provider_thread(agent_record=agent_record, provider_thread=None)
        return RuntimeHandleSnapshot(
            agent_id=agent_record.agent_id,
            task_id=agent_record.task_id,
            agent_type=agent_record.type.value,
            state=handle.state.value,
            status=agent_record.status.value,
            done=handle.done,
            awaiting_input=handle.awaiting_input,
            summary=agent_record.summary,
            error=agent_record.error,
            provider_thread_id=provider_thread.thread_id,
            provider_thread_path=provider_thread.thread_path,
            provider_resume_cursor=provider_thread.resume_cursor,
            input_requests=handle.input_requests,
        )

    def list_handle_snapshots(self, *, include_completed: bool = True) -> list[RuntimeHandleSnapshot]:
        """List tracked handle snapshots in stable agent-id order."""
        snapshots: list[RuntimeHandleSnapshot] = []
        for agent_id in sorted(self._handles):
            handle = self._handles[agent_id]
            record = self.agent_registry.get(agent_id)
            if record is None:
                continue
            snapshot = self.snapshot_handle(handle=handle, agent_record=record)
            if not include_completed and snapshot.done and not snapshot.awaiting_input:
                continue
            snapshots.append(snapshot)
        return snapshots

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

    async def start_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str,
        resume_thread_id: str | None = None,
        increment_spawn: bool = True,
    ) -> AgentHandle:
        """Start an agent run and return the durable handle immediately."""
        runtime = self._resolve_runtime(agent_record)
        agent_record.started_at = datetime.now(timezone.utc)
        self.agent_registry.upsert(agent_record, increment_spawn=increment_spawn)
        handle = await runtime.start(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            on_record_updated=self._make_record_callback(),
        )
        self._handles[agent_record.agent_id] = handle
        return handle

    async def resume_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        """Resume an existing handle-backed run using durable provider metadata."""
        runtime = self._resolve_runtime(agent_record)
        provider_thread = self._resolve_provider_thread(
            agent_record=agent_record,
            provider_thread=provider_thread,
        )
        if not provider_thread.resumable:
            raise ValueError(f"Agent {agent_record.agent_id} has no resumable provider thread")
        self.agent_registry.upsert(agent_record, increment_spawn=False)
        handle = await runtime.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            provider_thread=provider_thread,
            cwd=cwd,
            on_record_updated=self._make_record_callback(),
        )
        self._handles[agent_record.agent_id] = handle
        return handle

    async def start_task(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        resume_thread_id: str | None = None,
    ) -> AgentHandle:
        """Compatibility wrapper that starts a worktree-scoped agent run."""
        return await self.start_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(worktree.path),
            resume_thread_id=resume_thread_id,
            increment_spawn=True,
        )

    async def resume_task(
        self,
        *,
        worktree: GitWorktreeInfo,
        prompt: str,
        agent_record: AgentRecord,
        provider_thread: ProviderThreadHandle | None = None,
    ) -> AgentHandle:
        """Compatibility wrapper that resumes a worktree-scoped agent run."""
        return await self.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(worktree.path),
            provider_thread=provider_thread,
        )

    async def respond_to_request(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        request_id: int | str,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> RuntimeHandleSnapshot:
        """Forward a request response through the tracked handle."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        await handle.respond_to_request(request_id, result=result, error=error)
        agent_record = self.agent_registry.get(agent_id or self._agent_id_for_handle(handle) or "")
        return self.snapshot_handle(handle=handle, agent_record=agent_record)

    async def interrupt_run(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
    ) -> RuntimeHandleSnapshot:
        """Interrupt a tracked run and return the updated snapshot."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        await handle.interrupt()
        agent_record = self.agent_registry.get(agent_id or self._agent_id_for_handle(handle) or "")
        return self.snapshot_handle(handle=handle, agent_record=agent_record)

    async def kill_run(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
    ) -> RuntimeHandleSnapshot:
        """Forcefully tear down a tracked run and return the updated snapshot."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        await handle.kill()
        agent_record = self.agent_registry.get(agent_id or self._agent_id_for_handle(handle) or "")
        return self.snapshot_handle(handle=handle, agent_record=agent_record)

    async def wait_for_run(
        self,
        *,
        agent_id: str | None = None,
        handle: AgentHandle | None = None,
        release_terminal: bool = True,
    ) -> RuntimeExecutionResult:
        """Wait for a tracked handle and return the orchestrator-facing runtime result."""
        if handle is None:
            if agent_id is None:
                raise ValueError("agent_id or handle is required")
            handle = self.get_handle(agent_id)
            if handle is None:
                raise KeyError(f"Unknown agent handle: {agent_id}")
        normalized = await handle.wait()
        snapshot = self.snapshot_handle(handle=handle, agent_record=normalized.agent_record)
        result = _normalized_to_execution_result(normalized, snapshot=snapshot)
        if release_terminal and not result.awaiting_input:
            self.release_handle(normalized.agent_record.agent_id)
        return result

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
        return await self.wait_for_run(handle=handle)

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
        provider_thread = ProviderResumeHandle.from_provider_metadata(agent_record.provider) or ProviderResumeHandle(
            kind=agent_record.provider.kind
        )

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
                state=RunState.FAILED,
                provider_thread_id=provider_thread.thread_id,
                provider_thread_path=provider_thread.thread_path,
                provider_resume_cursor=provider_thread.resume_cursor,
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
            state=RunState.COMPLETED,
            provider_thread_id=provider_thread.thread_id,
            provider_thread_path=provider_thread.thread_path,
            provider_resume_cursor=provider_thread.resume_cursor,
        )


def _normalized_to_execution_result(
    result: NormalizedRunResult,
    *,
    snapshot: RuntimeHandleSnapshot | None = None,
) -> RuntimeExecutionResult:
    """Bridge a ``NormalizedRunResult`` to the orchestrator's runtime result type."""
    provider_thread = result.provider_thread
    if snapshot is not None:
        provider_thread = ProviderResumeHandle(
            thread_id=snapshot.provider_thread_id,
            thread_path=snapshot.provider_thread_path,
            resume_cursor=snapshot.provider_resume_cursor,
        )
    return RuntimeExecutionResult(
        agent_record=result.agent_record,
        events=result.events,
        summary=result.summary,
        error=result.error,
        turn_result=result.turn_result,
        state=result.state,
        awaiting_input=result.awaiting_input,
        provider_thread_id=provider_thread.thread_id,
        provider_thread_path=provider_thread.thread_path,
        provider_resume_cursor=provider_thread.resume_cursor,
        input_requests=list(result.input_requests),
        normalized_result=result,
    )
