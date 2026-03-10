"""Agent base classes providing the reusable adapter lifecycle.

``AgentBase`` manages the full provider-adapter session for a single agent
run: create adapter -> start session -> start/resume thread -> start turn
-> collect events/transcript -> wait for completion -> stop adapter ->
return ``AgentRunResult``.

Subclasses customise behaviour through a small set of overridable hooks
(runtime mode, event enrichment, summary extraction, etc.) while the core
``run()`` contract remains uniform across all agent types.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vibrant.config import DEFAULT_CONFIG_DIR, VibrantConfig, find_project_root, load_config
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.providers.base import CanonicalEvent, CanonicalEventHandler, RuntimeMode
from vibrant.providers.codex.adapter import CodexProviderAdapter

from .utils import (
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

AgentRecordCallback = Callable[[AgentRecord], Any]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AgentRunResult:
    """Structured outcome from a single agent run through a provider adapter."""

    transcript: str
    events: list[CanonicalEvent]
    agent_record: AgentRecord
    turn_result: Any | None = None
    exit_code: int | None = None
    pid: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# AgentBase
# ---------------------------------------------------------------------------

class AgentBase(ABC):
    """Base class managing one agent's full lifecycle through a provider adapter.

    Subclasses customise behaviour via overridable hooks.  The base class
    handles the adapter lifecycle:

        create adapter -> start session -> start/resume thread ->
        start turn -> wait for completion -> collect results -> stop.

    The agent instance is **reusable** across multiple ``run()`` calls.
    All per-run state is local to ``run()``; nothing per-run is stored
    on ``self``.
    """

    REQUEST_ERROR_MESSAGE = (
        "Interactive provider requests are not supported "
        "during autonomous agent execution."
    )

    def __init__(
        self,
        project_root: str | Path,
        *,
        config: VibrantConfig | None = None,
        adapter_factory: Any | None = None,
        on_canonical_event: CanonicalEventHandler | None = None,
        on_agent_record_updated: AgentRecordCallback | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.project_root = find_project_root(project_root)
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.config = config or load_config(start_path=self.project_root)
        self.adapter_factory = adapter_factory or CodexProviderAdapter
        self.on_canonical_event = on_canonical_event
        self.on_agent_record_updated = on_agent_record_updated
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(self.config.agent_timeout_seconds)
        )

    # ------------------------------------------------------------------
    # Abstract method
    # ------------------------------------------------------------------

    @abstractmethod
    def get_agent_type(self) -> AgentType:
        """Return the :class:`AgentType` for this agent class."""

    # ------------------------------------------------------------------
    # Overridable hooks (with sensible defaults)
    # ------------------------------------------------------------------

    def get_thread_runtime_mode(self) -> RuntimeMode:
        """Runtime mode for thread creation.

        Default: parsed from ``config.sandbox_mode`` (typically
        ``WORKSPACE_WRITE``).
        """

        return parse_runtime_mode(self.config.sandbox_mode)

    def get_turn_runtime_mode(self) -> RuntimeMode:
        """Runtime mode for turn execution.

        Default: parsed from ``config.turn_sandbox_policy``, falling back
        to ``config.sandbox_mode``.
        """

        return parse_runtime_mode(
            self.config.turn_sandbox_policy or self.config.sandbox_mode
        )

    def should_auto_reject_requests(self) -> bool:
        """Whether to auto-reject interactive ``request.opened`` events.

        Default: ``True``.  Autonomous agents (CodeAgent, MergeAgent)
        cannot handle interactive provider requests.
        """

        return True

    def get_thread_kwargs(self) -> dict[str, Any]:
        """Additional keyword arguments for ``adapter.start_thread()``.

        Default: model, approval_policy, model_provider, reasoning, and
        extra_config from ``VibrantConfig``.
        """

        return {
            "model": self.config.model,
            "approval_policy": self.config.approval_policy,
            "model_provider": self.config.model_provider,
            "reasoning_effort": self.config.reasoning_effort,
            "reasoning_summary": self.config.reasoning_summary,
            "extra_config": self.config.extra_config,
        }

    def enrich_event(
        self,
        event: CanonicalEvent,
        agent_record: AgentRecord,
    ) -> CanonicalEvent:
        """Add metadata to each canonical event before collection/forwarding.

        Default: sets ``agent_id`` and ``task_id`` on the event dict.
        """

        event.setdefault("agent_id", agent_record.agent_id)
        event.setdefault("task_id", agent_record.task_id)
        return event

    def extract_summary(
        self,
        transcript: str,
        turn_result: Any | None,
    ) -> str | None:
        """Extract a human-readable summary from the run output."""

        if transcript:
            return transcript
        return extract_summary_from_turn_result(turn_result)

    async def on_run_started(self, agent_record: AgentRecord) -> None:
        """Hook called after ``started_at`` is set, before adapter creation.

        Override for pre-run setup (e.g. snapshotting files).
        """

    async def on_run_completed(self, result: AgentRunResult) -> None:
        """Hook called after the run completes, before returning the result.

        Override for post-run processing (e.g. parsing output artifacts).
        """

    # ------------------------------------------------------------------
    # Core lifecycle
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        prompt: str,
        agent_record: AgentRecord,
        cwd: str | None = None,
        resume_thread_id: str | None = None,
    ) -> AgentRunResult:
        """Execute the full agent lifecycle and return a structured result.

        This method is the sole entry point for running an agent.  It
        manages:

        1. Adapter creation and session startup
        2. Thread start or resume
        3. Turn execution with the provided prompt
        4. Event collection (transcript, canonical events)
        5. Timeout enforcement
        6. Error handling and adapter cleanup
        7. Agent record status transitions
           (CONNECTING -> RUNNING -> terminal)

        The caller is responsible for:
        - Building the prompt
        - Building the ``AgentRecord``
        - Persisting the initial record (``engine.upsert_agent_record``)
        - Post-run orchestration (gatekeeper review, merge, retry)

        Args:
            prompt: The text prompt to send as the turn input.
            agent_record: Pre-built record.  This method mutates it
                in-place (status transitions, PID, summary, timestamps).
            cwd: Working directory for the adapter session.  Defaults to
                ``str(self.project_root)``.
            resume_thread_id: If provided, resume this thread instead of
                starting a new one.

        Returns:
            :class:`AgentRunResult` with transcript, events, updated
            agent_record, turn_result, exit_code, pid, and error.
        """

        resolved_cwd = cwd or str(self.project_root)
        events: list[CanonicalEvent] = []
        transcript_chunks: list[str] = []
        turn_finished = asyncio.Event()
        runtime_error: str | None = None
        adapter: Any | None = None

        # ---- event handler closure ----
        async def handle_event(event: CanonicalEvent) -> None:
            nonlocal runtime_error, adapter

            event_copy = dict(event)
            event_copy = self.enrich_event(event_copy, agent_record)
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

            elif event_type == "request.opened" and self.should_auto_reject_requests():
                request_id = event_copy.get("request_id")
                request_kind = str(event_copy.get("request_kind") or "request")
                runtime_error = f"{self.REQUEST_ERROR_MESSAGE} ({request_kind})"
                if adapter is not None and request_id is not None:
                    await adapter.respond_to_request(
                        request_id,
                        error={"code": -32000, "message": runtime_error},
                    )
                turn_finished.set()

            await maybe_forward_event(self.on_canonical_event, dict(event_copy))

        # ---- pre-run ----
        if agent_record.started_at is None:
            agent_record.started_at = datetime.now(timezone.utc)
        await self.on_run_started(agent_record)

        thread_runtime_mode = self.get_thread_runtime_mode()
        turn_runtime_mode = self.get_turn_runtime_mode()
        turn_result: Any | None = None

        try:
            # ---- CONNECTING ----
            agent_record.transition_to(AgentStatus.CONNECTING)
            await self._notify_record_updated(agent_record)

            # ---- create adapter ----
            adapter = self._create_adapter(resolved_cwd, agent_record, handle_event)
            await adapter.start_session(cwd=resolved_cwd)
            agent_record.pid = extract_pid(adapter)
            await self._notify_record_updated(agent_record)

            # ---- thread ----
            thread_kwargs: dict[str, Any] = {"cwd": resolved_cwd, "runtime_mode": thread_runtime_mode}
            thread_kwargs.update(self.get_thread_kwargs())
            if resume_thread_id:
                await adapter.resume_thread(resume_thread_id, **thread_kwargs)
            else:
                await adapter.start_thread(**thread_kwargs)

            # ---- RUNNING ----
            agent_record.transition_to(AgentStatus.RUNNING)
            await self._notify_record_updated(agent_record)

            # ---- turn ----
            turn_result = await adapter.start_turn(
                input_items=[{"type": "text", "text": prompt, "text_elements": []}],
                runtime_mode=turn_runtime_mode,
                approval_policy=self.config.approval_policy,
            )
            await asyncio.wait_for(turn_finished.wait(), timeout=self.timeout_seconds)

        except Exception as exc:
            if runtime_error is None:
                runtime_error = str(exc)
        finally:
            if adapter is not None:
                await stop_adapter_safely(adapter)

        # ---- build result ----
        transcript = "".join(transcript_chunks).strip()
        exit_code = extract_exit_code(adapter)
        pid = extract_pid(adapter) or agent_record.pid

        if runtime_error:
            agent_record.summary = transcript or agent_record.summary
            transition_terminal_agent(
                agent_record,
                AgentStatus.FAILED,
                exit_code=exit_code if exit_code is not None else 1,
                error=runtime_error,
            )
        else:
            agent_record.summary = self.extract_summary(transcript, turn_result)
            transition_terminal_agent(
                agent_record,
                AgentStatus.COMPLETED,
                exit_code=exit_code if exit_code is not None else 0,
            )

        await self._notify_record_updated(agent_record)

        result = AgentRunResult(
            transcript=transcript,
            events=events,
            agent_record=agent_record,
            turn_result=turn_result,
            exit_code=exit_code,
            pid=pid,
            error=runtime_error,
        )
        await self.on_run_completed(result)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _notify_record_updated(self, agent_record: AgentRecord) -> None:
        """Invoke the ``on_agent_record_updated`` callback if set."""

        if self.on_agent_record_updated is None:
            return
        result = self.on_agent_record_updated(agent_record)
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            await result

    def _create_adapter(
        self,
        cwd: str,
        agent_record: AgentRecord,
        on_canonical_event: CanonicalEventHandler,
    ) -> Any:
        """Instantiate a provider adapter with the configured factory."""

        return self.adapter_factory(
            cwd=cwd,
            codex_binary=self.config.codex_binary,
            launch_args=self.config.launch_args or None,
            codex_home=self.config.codex_home,
            agent_record=agent_record,
            on_canonical_event=on_canonical_event,
        )


# ---------------------------------------------------------------------------
# ReadOnlyAgentBase
# ---------------------------------------------------------------------------

class ReadOnlyAgentBase(AgentBase):
    """Agent that runs in read-only mode and cannot modify files.

    Use as base class for agents that only need to inspect the codebase
    (e.g. a ``TestAgent`` that runs test commands and reports results
    but does not modify source files).
    """

    def get_thread_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.READ_ONLY

    def get_turn_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.READ_ONLY
