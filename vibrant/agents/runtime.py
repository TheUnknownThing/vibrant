"""Agent runtime protocol — the service boundary the orchestrator depends on.

This module defines the contract between the orchestrator and agent
implementations.  The orchestrator never instantiates ``AgentBase``
subclasses directly; it programs against these protocols and value objects
instead.

Key types
---------
``AgentRuntime``
    Protocol that any agent implementation must satisfy.  It exposes
    ``start()`` and ``resume_run()`` coroutines that return an
    ``AgentHandle``.

``AgentHandle``
    Durable handle to a running or finished agent.  It exposes the
    provider-thread id for resume/recovery, explicit awaiting-input
    state with request metadata, ``respond_to_request()`` to answer
    pending provider requests, ``interrupt()`` for graceful turn
    cancellation, ``kill()`` for forceful teardown, and a ``wait()``
    coroutine that resolves to a ``NormalizedRunResult``.

``NormalizedRunResult``
    Canonical result the orchestrator can persist, expose to callbacks,
    and feed into review/retry pipelines.

``AgentRecordCallback``
    Callback signature the orchestrator provides so the runtime can
    push record updates (status transitions, pid, summary) without
    coupling to the persistence layer.

``InputRequest``
    Metadata about an interactive request the provider surfaced while
    the agent was running.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from vibrant.agents.base import AgentBase
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType, ProviderResumeHandle
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.invocation import ProviderInvocationPlan

logger = logging.getLogger(__name__)


# ── Value objects ────────────────────────────────────────────────────


class RunState(str, Enum):
    """Observable lifecycle state of an agent handle."""

    STARTING = "starting"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class InputRequest:
    """Metadata for a provider request that surfaced during the run."""

    request_id: str
    request_kind: str
    message: str | None = None


ProviderThreadHandle = ProviderResumeHandle
"""Backward-compatible alias for the durable provider resume model."""


@dataclass(slots=True)
class NormalizedRunResult:
    """Canonical agent-run result the orchestrator persists and routes.

    Every field is provider-agnostic; the orchestrator never needs to
    inspect adapter internals.
    """

    agent_record: AgentRecord
    state: RunState
    transcript: str = ""
    summary: str | None = None
    events: list[CanonicalEvent] = field(default_factory=list)
    exit_code: int | None = None
    error: str | None = None
    provider_thread: ProviderThreadHandle = field(default_factory=ProviderThreadHandle)
    input_requests: list[InputRequest] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    turn_result: Any | None = None

    @property
    def succeeded(self) -> bool:
        return self.state is RunState.COMPLETED and self.error is None

    @property
    def awaiting_input(self) -> bool:
        return self.state is RunState.AWAITING_INPUT


# ── Callback type ────────────────────────────────────────────────────

AgentRecordCallback = Callable[[AgentRecord], Any]
"""Invoked by the runtime on every record mutation so the orchestrator
can persist / broadcast without the runtime knowing about state stores."""


# ── Adapter accessor type ────────────────────────────────────────────

AdapterAccessor = Callable[[], Any | None]
"""Returns the live adapter from the underlying AgentBase, or None."""


# ── AgentHandle ──────────────────────────────────────────────────────


class AgentHandle:
    """Durable handle to a running or completed agent execution.

    The orchestrator holds onto this object to:
    * observe the current run-state and awaiting-input metadata
    * obtain the provider-thread handle for resume/recovery
    * respond to interactive provider requests via ``respond_to_request``
    * gracefully interrupt via ``interrupt``
    * forcefully tear down via ``kill``
    * ``await wait()`` for the final ``NormalizedRunResult``
    """

    def __init__(
        self,
        result_future: asyncio.Future[NormalizedRunResult],
        *,
        adapter_accessor: AdapterAccessor | None = None,
    ) -> None:
        self._future = result_future
        self._state: RunState = RunState.STARTING
        self._provider_thread = ProviderResumeHandle()
        self._input_requests: list[InputRequest] = []
        self._adapter_accessor = adapter_accessor

    # -- read accessors ------------------------------------------------

    @property
    def state(self) -> RunState:
        if self._future.done():
            try:
                result = self._future.result()
                return result.state
            except Exception:
                return RunState.FAILED
        return self._state

    @property
    def provider_thread(self) -> ProviderThreadHandle:
        return self._provider_thread

    @property
    def input_requests(self) -> list[InputRequest]:
        return list(self._input_requests)

    @property
    def awaiting_input(self) -> bool:
        return self.state is RunState.AWAITING_INPUT

    @property
    def done(self) -> bool:
        return self._future.done()

    # -- control methods (orchestrator-facing) -------------------------

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        """Respond to a pending interactive provider request.

        Delegates to the live adapter's ``respond_to_request``.
        Raises ``RuntimeError`` if the adapter is no longer available
        (agent already finished / torn down).
        """
        adapter = self._get_adapter("respond_to_request")
        await adapter.respond_to_request(request_id, result=result, error=error)
        # Clear the awaiting-input state for the responded request.
        self._input_requests = [
            r for r in self._input_requests if r.request_id != str(request_id)
        ]
        if not self._input_requests and self._state is RunState.AWAITING_INPUT:
            self._state = RunState.RUNNING

    async def interrupt(self) -> None:
        """Gracefully interrupt the running turn.

        Sends ``turn/interrupt`` to the provider, which will cause the
        current turn to complete early.  The run task will still finish
        and the future will resolve normally.
        """
        adapter = self._get_adapter("interrupt")
        await adapter.interrupt_turn()

    async def kill(self) -> None:
        """Forcefully tear down the adapter session.

        Stops the adapter session immediately.  The run task will
        observe the teardown and resolve the future with a FAILED
        result.
        """
        adapter = self._get_adapter("kill")
        try:
            await adapter.stop_session()
        except Exception:
            logger.debug("AgentHandle.kill: stop_session raised", exc_info=True)

    # -- mutators (used by the runtime, not the orchestrator) ----------

    def _set_state(self, state: RunState) -> None:
        self._state = state

    def _set_provider_thread(self, thread: ProviderThreadHandle) -> None:
        self._provider_thread = thread

    def _add_input_request(self, req: InputRequest) -> None:
        self._input_requests.append(req)
        self._state = RunState.AWAITING_INPUT

    # -- await ---------------------------------------------------------

    async def wait(self) -> NormalizedRunResult:
        """Block until the agent run completes and return the result."""
        return await self._future

    # -- internal ------------------------------------------------------

    def _get_adapter(self, method_name: str) -> Any:
        """Obtain the live adapter or raise."""
        if self._adapter_accessor is None:
            raise RuntimeError(
                f"AgentHandle.{method_name}() is not supported: "
                "no adapter accessor was provided"
            )
        adapter = self._adapter_accessor()
        if adapter is None:
            raise RuntimeError(
                f"AgentHandle.{method_name}() cannot be called: "
                "the adapter is no longer available (agent finished or not yet started)"
            )
        return adapter


# ── AgentRuntime protocol ────────────────────────────────────────────


@runtime_checkable
class AgentRuntime(Protocol):
    """Service-boundary contract the orchestrator programs against.

    Any implementation — ``BaseAgentRuntime`` wrapping an ``AgentBase``
    subclass, a future remote-agent adapter, a mock for testing — can
    satisfy this protocol.
    """

    async def start(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str | None = None,
        resume_thread_id: str | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
        """Launch the agent and return a handle immediately.

        Parameters
        ----------
        agent_record:
            Pre-built record describing the agent.  The runtime mutates
            status/timestamps on it and pushes updates through
            ``on_record_updated``.
        prompt:
            The task prompt to execute.
        cwd:
            Working directory. Falls back to the record's worktree_path.
        resume_thread_id:
            If provided, resume an existing provider thread instead of
            starting a new one.
        on_record_updated:
            Optional callback the orchestrator supplies so every record
            mutation (status transition, pid, summary, terminal state)
            is pushed back without the runtime knowing about persistence.
        """
        ...

    async def resume_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        provider_thread: ProviderThreadHandle,
        cwd: str | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
        """Resume a previously interrupted or awaiting-input agent.

        This re-attaches to the durable provider thread referenced by
        ``provider_thread`` and starts a new turn with the given prompt.
        The returned handle supports the same control surface as
        ``start()`` — ``respond_to_request``, ``interrupt``, ``kill``,
        ``wait``.

        Parameters
        ----------
        agent_record:
            The existing agent record (may carry state from the prior
            run).
        prompt:
            New prompt / follow-up input for the resumed turn.
        provider_thread:
            Durable thread handle obtained from a prior
            ``AgentHandle.provider_thread`` or ``NormalizedRunResult``.
        cwd:
            Working directory override.
        on_record_updated:
            Persistence callback, same semantics as ``start()``.
        """
        ...


# ── Concrete runtime wrapping AgentBase ──────────────────────────────


class BaseAgentRuntime:
    """Wraps any ``AgentBase`` subclass into the ``AgentRuntime`` protocol.

    This is the default concrete implementation used by the orchestrator.
    It:
    * Delegates the full adapter lifecycle to ``AgentBase.run()``.
    * Publishes record mutations through the ``on_record_updated`` callback.
    * Returns an ``AgentHandle`` whose future resolves to a
      ``NormalizedRunResult``.
    * Populates the durable ``ProviderThreadHandle`` for resume/recovery.
    * Tracks ``InputRequest`` metadata when the provider surfaces requests.
    * Exposes the live adapter through the handle for ``respond_to_request``,
      ``interrupt``, and ``kill``.
    """

    def __init__(self, agent: "AgentBase") -> None:
        self._agent = agent

    async def start(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str | None = None,
        resume_thread_id: str | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
        return await self._launch(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            on_record_updated=on_record_updated,
            invocation_plan=invocation_plan,
        )

    async def resume_run(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        provider_thread: ProviderThreadHandle,
        cwd: str | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
        if not provider_thread.resumable:
            raise ValueError(
                "Cannot resume: provider_thread has no thread_id"
            )
        return await self._launch(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=provider_thread.thread_id,
            on_record_updated=on_record_updated,
            invocation_plan=invocation_plan,
        )

    # ------------------------------------------------------------------
    # Internal launch implementation
    # ------------------------------------------------------------------

    async def _launch(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str | None,
        resume_thread_id: str | None,
        on_record_updated: AgentRecordCallback | None,
        invocation_plan: ProviderInvocationPlan | None,
    ) -> AgentHandle:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[NormalizedRunResult] = loop.create_future()

        # Build the adapter accessor so the handle can delegate control
        # methods to the live adapter held by AgentBase._live_adapter.
        agent = self._agent

        def _adapter_accessor() -> Any | None:
            return getattr(agent, "_live_adapter", None)

        handle = AgentHandle(future, adapter_accessor=_adapter_accessor)

        # Wire the record callback so the orchestrator can persist every
        # mutation without coupling to a specific store.
        original_callback = self._agent.on_agent_record_updated

        def _bridge_callback(record: AgentRecord) -> None:
            # Update handle state from the record status.
            _sync_handle_state(handle, record)
            # Forward to the original callback if set.
            if original_callback is not None:
                original_callback(record)
            # Forward to the orchestrator-supplied callback.
            if on_record_updated is not None:
                on_record_updated(record)

        self._agent.on_agent_record_updated = _bridge_callback

        # Capture input requests from the event stream.
        original_event_cb = self._agent.on_canonical_event

        async def _event_bridge(event: CanonicalEvent) -> None:
            event_type = str(event.get("type") or "")
            if event_type == "request.opened":
                req = InputRequest(
                    request_id=str(event.get("request_id") or ""),
                    request_kind=str(event.get("request_kind") or "request"),
                    message=event.get("message") if isinstance(event.get("message"), str) else None,
                )
                handle._add_input_request(req)
            if original_event_cb is not None:
                import inspect

                result = original_event_cb(event)
                if inspect.isawaitable(result):
                    await result

        self._agent.on_canonical_event = _event_bridge

        async def _run() -> NormalizedRunResult:
            try:
                from .base import AgentRunResult  # local import to avoid cycle

                run_result: AgentRunResult = await self._agent.run(
                    prompt=prompt,
                    agent_record=agent_record,
                    cwd=cwd,
                    resume_thread_id=resume_thread_id,
                    invocation_plan=invocation_plan,
                )

                provider_thread = ProviderResumeHandle.from_provider_metadata(agent_record.provider) or ProviderResumeHandle(
                    kind=agent_record.provider.kind
                )
                handle._set_provider_thread(provider_thread)

                state = RunState.COMPLETED if run_result.error is None else RunState.FAILED
                if agent_record.lifecycle.status is AgentStatus.AWAITING_INPUT:
                    state = RunState.AWAITING_INPUT

                return NormalizedRunResult(
                    agent_record=agent_record,
                    state=state,
                    transcript=run_result.transcript,
                    summary=agent_record.outcome.summary,
                    events=run_result.events,
                    exit_code=run_result.exit_code,
                    error=run_result.error,
                    provider_thread=provider_thread,
                    input_requests=list(handle._input_requests),
                    started_at=agent_record.lifecycle.started_at,
                    finished_at=agent_record.lifecycle.finished_at,
                    turn_result=run_result.turn_result,
                )
            except Exception as exc:
                return NormalizedRunResult(
                    agent_record=agent_record,
                    state=RunState.FAILED,
                    error=str(exc),
                    started_at=agent_record.lifecycle.started_at,
                    finished_at=datetime.now(timezone.utc),
                )

        async def _run_and_resolve() -> None:
            result = await _run()
            if not future.done():
                future.set_result(result)

        asyncio.create_task(_run_and_resolve(), name=f"agent-runtime-{agent_record.identity.agent_id}")
        # Yield control so the task has a chance to start.
        await asyncio.sleep(0)
        return handle


# ── Helpers ──────────────────────────────────────────────────────────


def _sync_handle_state(handle: AgentHandle, record: AgentRecord) -> None:
    """Map AgentStatus to RunState on the handle."""
    mapping: dict[AgentStatus, RunState] = {
        AgentStatus.SPAWNING: RunState.STARTING,
        AgentStatus.CONNECTING: RunState.STARTING,
        AgentStatus.RUNNING: RunState.RUNNING,
        AgentStatus.AWAITING_INPUT: RunState.AWAITING_INPUT,
        AgentStatus.COMPLETED: RunState.COMPLETED,
        AgentStatus.FAILED: RunState.FAILED,
        AgentStatus.KILLED: RunState.FAILED,
    }
    try:
        next_state = mapping[record.lifecycle.status]
    except KeyError as exc:
        raise ValueError(f"Unsupported agent status for runtime handle sync: {record.lifecycle.status!r}") from exc
    handle._set_state(next_state)
