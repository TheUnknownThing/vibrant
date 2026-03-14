"""Gatekeeper runtime lifecycle service."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from vibrant.agents.gatekeeper import (
    Gatekeeper,
    GatekeeperRequest,
    GatekeeperTrigger,
)
from vibrant.models.agent import ProviderResumeHandle
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.invocation_compiler import compile_provider_invocation
from vibrant.prompts import build_user_answer_trigger_description

from ..conversation.stream import ConversationStreamService
from ..runtime.service import AgentRuntimeService
from ..types import (
    GatekeeperLifecycleStatus,
    GatekeeperMessageKind,
    GatekeeperSessionSnapshot,
    utc_now,
)


class GatekeeperLifecycleService:
    """Own the runtime lifecycle of the single Gatekeeper session."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        runtime_service: AgentRuntimeService,
        conversation_service: ConversationStreamService,
        gatekeeper: Gatekeeper | None = None,
        binding_service: Any | None = None,
        mcp_host: Any | None = None,
        session_loader: Callable[[], GatekeeperSessionSnapshot] | None = None,
        session_saver: Callable[[GatekeeperSessionSnapshot], Any] | None = None,
        on_record_updated: Callable[[Any], Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.runtime_service = runtime_service
        self.conversation_service = conversation_service
        self.gatekeeper = gatekeeper or Gatekeeper(self.project_root)
        self.binding_service = binding_service
        self.mcp_host = mcp_host
        self._session_loader = session_loader
        self._session_saver = session_saver
        self._on_record_updated = on_record_updated
        self._session = self._load_session()
        self._active_handle = None
        self._active_handle_agent_id: str | None = None
        self._binding_ids_by_agent_id: dict[str, str] = {}
        self._subscription = self.runtime_service.subscribe_canonical_events(self._on_runtime_event)

    @property
    def busy(self) -> bool:
        return self._session.lifecycle_state in {
            GatekeeperLifecycleStatus.STARTING,
            GatekeeperLifecycleStatus.RUNNING,
        }

    async def ensure_session(self) -> GatekeeperSessionSnapshot:
        if self._session.conversation_id is None:
            self._session.conversation_id = f"gatekeeper-{uuid4().hex[:12]}"
            self._session.updated_at = utc_now()
            self._persist()
        return self.snapshot()

    async def resume_or_start(self) -> GatekeeperSessionSnapshot:
        await self.ensure_session()
        if self._session.lifecycle_state is GatekeeperLifecycleStatus.STOPPED:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.IDLE
            self._session.updated_at = utc_now()
            self._persist()
        return self.snapshot()

    async def submit(
        self,
        *,
        message_kind: GatekeeperMessageKind,
        text: str,
        submission_id: str,
        resume: bool = True,
        trigger_description: str | None = None,
        agent_summary: str | None = None,
    ):
        session = await self.resume_or_start()
        request = self._build_request(
            message_kind=message_kind,
            text=text,
            trigger_description=trigger_description,
            agent_summary=agent_summary,
        )
        prompt = self.gatekeeper.render_prompt(request)
        agent_record = self.gatekeeper.build_agent_record(request)
        agent_record.context.prompt_used = prompt

        conversation_id = session.conversation_id or f"gatekeeper-{uuid4().hex[:12]}"
        self.conversation_service.bind_agent(
            conversation_id=conversation_id,
            agent_id=agent_record.identity.agent_id,
            task_id=agent_record.identity.task_id,
            provider_thread_id=session.provider_thread_id,
        )

        provider_thread = None
        if resume and session.provider_thread_id:
            provider_thread = ProviderResumeHandle(
                kind=agent_record.provider.kind,
                thread_id=session.provider_thread_id,
            )

        self._session.agent_id = agent_record.identity.agent_id
        self._session.conversation_id = conversation_id
        self._session.lifecycle_state = GatekeeperLifecycleStatus.STARTING
        self._session.last_error = None
        self._session.updated_at = utc_now()
        self._persist()

        if self.binding_service is None or self.mcp_host is None:
            raise RuntimeError("GatekeeperLifecycleService requires binding_service and mcp_host before submit()")

        await self.mcp_host.ensure_started()
        bound_capabilities = self.binding_service.bind_gatekeeper(
            session_id=agent_record.identity.agent_id,
            conversation_id=conversation_id,
        )
        registered_binding = self.mcp_host.register_binding(bound_capabilities)
        self._binding_ids_by_agent_id[agent_record.identity.agent_id] = registered_binding.binding_id
        invocation_plan = compile_provider_invocation(
            agent_record.provider.kind,
            bound_capabilities.access,
        )

        try:
            if provider_thread is not None:
                handle = await self.runtime_service.resume_run(
                    agent_record=agent_record,
                    prompt=prompt,
                    provider_thread=provider_thread,
                    cwd=str(self.project_root),
                    runtime=self.gatekeeper.runtime,
                    on_record_updated=self._on_record_updated,
                    invocation_plan=invocation_plan,
                )
            else:
                handle = await self.runtime_service.start_run(
                    agent_record=agent_record,
                    prompt=prompt,
                    cwd=str(self.project_root),
                    runtime=self.gatekeeper.runtime,
                    on_record_updated=self._on_record_updated,
                    invocation_plan=invocation_plan,
                )
        except Exception as exc:
            self._release_binding(agent_record.identity.agent_id)
            self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
            self._session.last_error = str(exc)
            self._session.active_turn_id = None
            self._session.updated_at = utc_now()
            self._persist()
            raise

        self._active_handle = handle
        self._active_handle_agent_id = agent_record.identity.agent_id
        asyncio.create_task(
            self._monitor_handle(agent_record.identity.agent_id, handle),
            name=f"gatekeeper-monitor-{submission_id}",
        )
        return handle

    async def interrupt_active_turn(self) -> GatekeeperSessionSnapshot:
        if self._active_handle_agent_id is None:
            return self.snapshot()
        await self.runtime_service.interrupt_run(self._active_handle_agent_id)
        return self.snapshot()

    async def stop_session(self) -> GatekeeperSessionSnapshot:
        if self._active_handle_agent_id is not None:
            await self.runtime_service.kill_run(self._active_handle_agent_id)
            self._release_binding(self._active_handle_agent_id)
            self._active_handle_agent_id = None
            self._active_handle = None
        self._session.lifecycle_state = GatekeeperLifecycleStatus.STOPPED
        self._session.active_turn_id = None
        self._session.updated_at = utc_now()
        self._persist()
        return self.snapshot()

    async def restart_session(self, *, reason: str | None = None) -> GatekeeperSessionSnapshot:
        await self.stop_session()
        self._session.agent_id = None
        self._session.provider_thread_id = None
        self._session.resumable = False
        self._session.lifecycle_state = GatekeeperLifecycleStatus.NOT_STARTED
        self._session.last_error = reason
        self._session.updated_at = utc_now()
        self._persist()
        return await self.resume_or_start()

    def snapshot(self) -> GatekeeperSessionSnapshot:
        return GatekeeperSessionSnapshot(
            agent_id=self._session.agent_id,
            conversation_id=self._session.conversation_id,
            lifecycle_state=self._session.lifecycle_state,
            provider_thread_id=self._session.provider_thread_id,
            active_turn_id=self._session.active_turn_id,
            resumable=self._session.resumable,
            last_error=self._session.last_error,
            updated_at=self._session.updated_at,
        )

    async def _monitor_handle(self, agent_id: str, handle) -> None:
        try:
            result = await handle.wait()
        except Exception as exc:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
            self._session.last_error = str(exc)
            self._session.updated_at = utc_now()
            self._release_binding(agent_id)
            self._persist()
            return

        self._session.provider_thread_id = result.provider_thread_id
        self._session.resumable = bool(result.provider_thread_id)
        self._session.updated_at = utc_now()
        if result.awaiting_input:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.AWAITING_USER
        elif result.error:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
            self._session.last_error = result.error
        else:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.IDLE
            self._session.last_error = None
        if self._active_handle_agent_id == agent_id:
            self._active_handle_agent_id = None
            self._active_handle = None
        self._release_binding(agent_id)
        self._persist()

    async def _on_runtime_event(self, event: CanonicalEvent) -> None:
        agent_id = event.get("agent_id")
        if not isinstance(agent_id, str) or agent_id != self._session.agent_id:
            return

        event_type = str(event.get("type") or "")
        if event_type == "turn.started":
            turn_id = event.get("turn_id")
            self._session.active_turn_id = turn_id if isinstance(turn_id, str) else None
            self._session.lifecycle_state = GatekeeperLifecycleStatus.RUNNING
        elif event_type == "turn.completed":
            self._session.active_turn_id = None
            if self._session.lifecycle_state is not GatekeeperLifecycleStatus.AWAITING_USER:
                self._session.lifecycle_state = GatekeeperLifecycleStatus.IDLE
        elif event_type in {"request.opened", "user-input.requested"}:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.AWAITING_USER
        elif event_type == "runtime.error":
            self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
            message = event.get("error_message")
            self._session.last_error = message if isinstance(message, str) else "Gatekeeper runtime error"

        provider_thread_id = event.get("provider_thread_id")
        if isinstance(provider_thread_id, str) and provider_thread_id:
            self._session.provider_thread_id = provider_thread_id
            self._session.resumable = True
        self._session.updated_at = utc_now()
        self._persist()

    def _build_request(
        self,
        *,
        message_kind: GatekeeperMessageKind,
        text: str,
        trigger_description: str | None = None,
        agent_summary: str | None = None,
    ) -> GatekeeperRequest:
        if message_kind is GatekeeperMessageKind.USER_ANSWER:
            return GatekeeperRequest(
                trigger=GatekeeperTrigger.USER_CONVERSATION,
                trigger_description=trigger_description
                or build_user_answer_trigger_description(question="User decision", answer=text),
                agent_summary=agent_summary or text,
            )
        if message_kind is GatekeeperMessageKind.REVIEW:
            return GatekeeperRequest(
                trigger=GatekeeperTrigger.TASK_COMPLETION,
                trigger_description=trigger_description or text,
                agent_summary=agent_summary or text,
            )
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description=trigger_description or text,
            agent_summary=agent_summary or text,
        )

    def _load_session(self) -> GatekeeperSessionSnapshot:
        if self._session_loader is None:
            return GatekeeperSessionSnapshot()
        loaded = self._session_loader()
        return GatekeeperSessionSnapshot(
            agent_id=loaded.agent_id,
            conversation_id=loaded.conversation_id,
            lifecycle_state=loaded.lifecycle_state,
            provider_thread_id=loaded.provider_thread_id,
            active_turn_id=loaded.active_turn_id,
            resumable=loaded.resumable,
            last_error=loaded.last_error,
            updated_at=loaded.updated_at,
        )

    def _release_binding(self, agent_id: str | None) -> None:
        if agent_id is None:
            return
        binding_id = self._binding_ids_by_agent_id.pop(agent_id, None)
        if binding_id is not None and self.mcp_host is not None:
            self.mcp_host.unregister_binding(binding_id)

    def _persist(self) -> None:
        if self._session_saver is None:
            return
        result = self._session_saver(self.snapshot())
        if inspect.isawaitable(result):
            raise RuntimeError("Gatekeeper session persistence must be synchronous")
