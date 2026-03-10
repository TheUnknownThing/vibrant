"""AgentBase hierarchy and AgentRunResult.

AgentBase manages a single agent run through the provider adapter lifecycle:
create adapter -> start session -> start/resume thread -> start turn ->
collect events/transcript -> wait for completion -> stop adapter -> return result.

It does NOT handle worktree management, gatekeeper review, merging, or retries.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.providers.base import CanonicalEvent, RuntimeMode

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

REQUEST_ERROR_MESSAGE = (
    "Interactive provider requests are not supported during autonomous task execution."
)


@dataclass(slots=True)
class AgentRunResult:
    """Outcome of a single agent run through the adapter lifecycle."""

    transcript: str = ""
    events: list[CanonicalEvent] = field(default_factory=list)
    agent_record: AgentRecord | None = None
    turn_result: Any | None = None
    exit_code: int | None = None
    pid: int | None = None
    error: str | None = None


class AgentBase(ABC):
    """Abstract base for all agent types.

    Manages the full provider adapter lifecycle for a single run.
    Subclasses override hooks to customise runtime modes, thread kwargs,
    event enrichment, and summary extraction.
    """

    def __init__(
        self,
        project_root: str | Path,
        config: VibrantConfig,
        *,
        adapter_factory: Any,
        on_canonical_event: Callable[[CanonicalEvent], Any] | None = None,
        on_agent_record_updated: Callable[[AgentRecord], Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.config = config
        self.adapter_factory = adapter_factory
        self.on_canonical_event = on_canonical_event
        self.on_agent_record_updated = on_agent_record_updated
        self.timeout_seconds = timeout_seconds or float(config.agent_timeout_seconds)

    # ------------------------------------------------------------------
    # Abstract / required methods
    # ------------------------------------------------------------------

    @abstractmethod
    def get_agent_type(self) -> AgentType:
        """Return the agent type for this implementation."""

    # ------------------------------------------------------------------
    # Overridable hooks (sensible defaults)
    # ------------------------------------------------------------------

    def get_thread_runtime_mode(self) -> RuntimeMode:
        """Runtime mode used when opening/resuming the provider thread."""
        return parse_runtime_mode(self.config.sandbox_mode)

    def get_turn_runtime_mode(self) -> RuntimeMode:
        """Runtime mode used when starting a provider turn."""
        return parse_runtime_mode(
            self.config.turn_sandbox_policy or self.config.sandbox_mode
        )

    def should_auto_reject_requests(self) -> bool:
        """Whether to auto-reject interactive provider requests.

        Returns True for autonomous agents (default). Override to False
        if the agent should support interactive approvals.
        """
        return True

    def get_thread_kwargs(self) -> dict[str, Any]:
        """Extra keyword arguments forwarded to ``adapter.start_thread``."""
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
        event: dict[str, Any],
        agent_record: AgentRecord,
    ) -> dict[str, Any]:
        """Add agent-specific metadata to a canonical event before forwarding."""
        event.setdefault("agent_id", agent_record.agent_id)
        event.setdefault("task_id", agent_record.task_id)
        return event

    def extract_summary(
        self,
        transcript: str,
        turn_result: Any | None,
    ) -> str | None:
        """Derive an agent summary from the transcript or turn result."""
        if transcript:
            return transcript
        return extract_summary_from_turn_result(turn_result)

    def on_run_started(self, agent_record: AgentRecord) -> None:
        """Hook called when the adapter session starts. No-op by default."""

    def on_run_completed(self, result: AgentRunResult) -> None:
        """Hook called after the run finishes (success or failure). No-op by default."""

    # ------------------------------------------------------------------
    # Core run method
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        prompt: str,
        agent_record: AgentRecord,
        cwd: str | Path | None = None,
        resume_thread_id: str | None = None,
    ) -> AgentRunResult:
        """Execute a full adapter lifecycle and return the result.

        This is the sole public entry point. It:
        1. Creates the adapter and starts a session.
        2. Opens or resumes a thread.
        3. Starts a turn with the given prompt.
        4. Collects events and transcript until completion or error.
        5. Stops the adapter.
        6. Returns an ``AgentRunResult``.
        """
        working_dir = str(cwd or agent_record.worktree_path or self.project_root)
        events: list[CanonicalEvent] = []
        transcript_chunks: list[str] = []
        turn_finished = asyncio.Event()
        runtime_error: str | None = None
        adapter: Any | None = None

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
                runtime_error = f"{REQUEST_ERROR_MESSAGE} ({request_kind})"
                if adapter is not None and request_id is not None:
                    await adapter.respond_to_request(
                        request_id,
                        error={"code": -32000, "message": runtime_error},
                    )
                turn_finished.set()

            await maybe_forward_event(self.on_canonical_event, event_copy)

        agent_record.started_at = datetime.now(timezone.utc)
        self._notify_record_updated(agent_record)
        self.on_run_started(agent_record)

        thread_runtime_mode = self.get_thread_runtime_mode()
        turn_runtime_mode = self.get_turn_runtime_mode()
        turn_result: Any | None = None

        try:
            agent_record.transition_to(AgentStatus.CONNECTING)
            self._notify_record_updated(agent_record)

            adapter = self.adapter_factory(
                cwd=working_dir,
                codex_binary=self.config.codex_binary,
                codex_home=self.config.codex_home,
                agent_record=agent_record,
                on_canonical_event=handle_event,
            )
            await adapter.start_session(cwd=working_dir)
            agent_record.pid = extract_pid(adapter)
            self._notify_record_updated(agent_record)

            thread_kwargs = self.get_thread_kwargs()
            thread_kwargs["cwd"] = working_dir
            thread_kwargs["runtime_mode"] = thread_runtime_mode

            if resume_thread_id:
                await adapter.resume_thread(resume_thread_id, **thread_kwargs)
            else:
                await adapter.start_thread(**thread_kwargs)

            agent_record.transition_to(AgentStatus.RUNNING)
            self._notify_record_updated(agent_record)

            turn_result = await adapter.start_turn(
                input_items=[{"type": "text", "text": prompt, "text_elements": []}],
                runtime_mode=turn_runtime_mode,
                approval_policy=self.config.approval_policy,
            )
            await asyncio.wait_for(
                turn_finished.wait(), timeout=self.timeout_seconds
            )
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
        else:
            agent_record.summary = self.extract_summary(transcript, turn_result)
            transition_terminal_agent(
                agent_record,
                AgentStatus.COMPLETED,
                exit_code=exit_code if exit_code is not None else 0,
            )

        self._notify_record_updated(agent_record)

        result = AgentRunResult(
            transcript=transcript,
            events=events,
            agent_record=agent_record,
            turn_result=turn_result,
            exit_code=agent_record.exit_code,
            pid=agent_record.pid,
            error=runtime_error,
        )
        self.on_run_completed(result)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _notify_record_updated(self, agent_record: AgentRecord) -> None:
        if self.on_agent_record_updated is not None:
            try:
                self.on_agent_record_updated(agent_record)
            except Exception:
                logger.debug("on_agent_record_updated callback failed", exc_info=True)


class ReadOnlyAgentBase(AgentBase):
    """Agent base that locks runtime modes to READ_ONLY.

    Suitable for agents that should never modify the workspace (e.g., TestAgent).
    """

    def get_thread_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.READ_ONLY

    def get_turn_runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.READ_ONLY
