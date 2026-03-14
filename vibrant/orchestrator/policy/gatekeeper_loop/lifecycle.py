"""Gatekeeper-loop runtime lifecycle mechanics."""

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
)
from vibrant.models.agent import AgentInstanceProviderConfig
from vibrant.models.agent import ProviderResumeHandle
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.invocation_compiler import compile_provider_invocation

from ...basic.stores import AgentInstanceStore, AgentRunStore
from ...basic.conversation import ConversationStreamService
from ...basic.runtime import AgentRuntimeService
from ..shared.capabilities import gatekeeper_binding_preset
from .roles import GATEKEEPER_ROLE, ensure_gatekeeper_instance
from ...types import (
    GatekeeperLifecycleStatus,
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
        instance_store: AgentInstanceStore,
        run_store: AgentRunStore,
        session_loader: Callable[[], GatekeeperSessionSnapshot] | None = None,
        session_saver: Callable[[GatekeeperSessionSnapshot], Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.runtime_service = runtime_service
        self.conversation_service = conversation_service
        self.gatekeeper = gatekeeper or Gatekeeper(self.project_root)
        self.binding_service = binding_service
        self.mcp_host = mcp_host
        self.instance_store = instance_store
        self.run_store = run_store
        self._session_loader = session_loader
        self._session_saver = session_saver
        self._session = self._load_session()
        self._active_handle = None
        self._active_handle_run_id: str | None = None
        self._binding_ids_by_run_id: dict[str, str] = {}
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
        request: GatekeeperRequest,
        submission_id: str,
        resume: bool = True,
    ):
        self._ensure_no_active_submission()
        session = await self.resume_or_start()
        prompt = self.gatekeeper.render_prompt(request)
        agent_record = self.gatekeeper.build_run_record(
            request,
            agent_id=GATEKEEPER_ROLE,
            role=GATEKEEPER_ROLE,
        )
        agent_record.context.prompt_used = prompt
        instance = ensure_gatekeeper_instance(
            self.instance_store,
            provider=AgentInstanceProviderConfig(
                kind=agent_record.provider.kind,
                transport=agent_record.provider.transport,
                runtime_mode=agent_record.provider.runtime_mode,
            ),
        )
        self._persist_run(agent_record)

        conversation_id = session.conversation_id or f"gatekeeper-{uuid4().hex[:12]}"
        self.conversation_service.bind_agent(
            conversation_id=conversation_id,
            agent_id=instance.identity.agent_id,
            task_id=agent_record.identity.task_id,
            provider_thread_id=session.provider_thread_id,
        )

        provider_thread = None
        if resume and session.provider_thread_id:
            provider_thread = self.run_store.provider_thread_handle(instance.identity.agent_id) or ProviderResumeHandle(
                kind=agent_record.provider.kind,
                thread_id=session.provider_thread_id,
            )

        self._session.agent_id = instance.identity.agent_id
        self._session.run_id = agent_record.identity.run_id
        self._session.conversation_id = conversation_id
        self._session.lifecycle_state = GatekeeperLifecycleStatus.STARTING
        self._session.last_error = None
        self._session.updated_at = utc_now()
        self._persist()

        if self.binding_service is None or self.mcp_host is None:
            raise RuntimeError("GatekeeperLifecycleService requires binding_service and mcp_host before submit()")

        await self.mcp_host.ensure_started()
        bound_capabilities = self.binding_service.bind_preset(
            preset=gatekeeper_binding_preset(self.binding_service.mcp_server, agent_record.identity.run_id),
            session_id=agent_record.identity.run_id,
            conversation_id=conversation_id,
        )
        registered_binding = self.mcp_host.register_binding(bound_capabilities)
        self._binding_ids_by_run_id[agent_record.identity.run_id] = registered_binding.binding_id
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
                    on_record_updated=self._persist_run,
                    invocation_plan=invocation_plan,
                )
            else:
                handle = await self.runtime_service.start_run(
                    agent_record=agent_record,
                    prompt=prompt,
                    cwd=str(self.project_root),
                    runtime=self.gatekeeper.runtime,
                    on_record_updated=self._persist_run,
                    invocation_plan=invocation_plan,
                )
        except Exception as exc:
            self._release_binding(agent_record.identity.run_id)
            self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
            self._session.last_error = str(exc)
            self._session.active_turn_id = None
            self._session.updated_at = utc_now()
            self._persist()
            raise

        self._active_handle = handle
        self._active_handle_run_id = agent_record.identity.run_id
        asyncio.create_task(
            self._monitor_handle(agent_record.identity.run_id, handle),
            name=f"gatekeeper-monitor-{submission_id}",
        )
        return handle

    async def interrupt_active_turn(self) -> GatekeeperSessionSnapshot:
        if self._active_handle_run_id is None:
            return self.snapshot()
        await self.runtime_service.interrupt_run(self._active_handle_run_id)
        return self.snapshot()

    async def stop_session(self) -> GatekeeperSessionSnapshot:
        if self._active_handle_run_id is not None:
            await self.runtime_service.kill_run(self._active_handle_run_id)
            self._release_binding(self._active_handle_run_id)
            self._active_handle_run_id = None
            self._active_handle = None
        self._session.lifecycle_state = GatekeeperLifecycleStatus.STOPPED
        self._session.run_id = None
        self._session.active_turn_id = None
        self._session.updated_at = utc_now()
        self._persist()
        return self.snapshot()

    async def restart_session(self, *, reason: str | None = None) -> GatekeeperSessionSnapshot:
        await self.stop_session()
        self._session.agent_id = None
        self._session.run_id = None
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
            run_id=self._session.run_id,
            conversation_id=self._session.conversation_id,
            lifecycle_state=self._session.lifecycle_state,
            provider_thread_id=self._session.provider_thread_id,
            active_turn_id=self._session.active_turn_id,
            resumable=self._session.resumable,
            last_error=self._session.last_error,
            updated_at=self._session.updated_at,
        )

    async def _monitor_handle(self, run_id: str, handle) -> None:
        try:
            result = await handle.wait()
        except Exception as exc:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
            self._session.last_error = str(exc)
            self._session.updated_at = utc_now()
            self._release_binding(run_id)
            self._persist()
            return

        self._session.provider_thread_id = result.provider_thread_id
        self._session.resumable = bool(result.provider_thread_id)
        self._session.run_id = result.agent_record.identity.run_id
        self._session.updated_at = utc_now()
        if result.awaiting_input:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.AWAITING_USER
        elif result.error:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
            self._session.last_error = result.error
        else:
            self._session.lifecycle_state = GatekeeperLifecycleStatus.IDLE
            self._session.last_error = None
        if self._active_handle_run_id == run_id:
            self._active_handle_run_id = None
            self._active_handle = None
        self._release_binding(run_id)
        self._persist()

    async def _on_runtime_event(self, event: CanonicalEvent) -> None:
        run_id = event.get("run_id")
        if not isinstance(run_id, str) or run_id != self._session.run_id:
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

    def _load_session(self) -> GatekeeperSessionSnapshot:
        if self._session_loader is None:
            return GatekeeperSessionSnapshot()
        loaded = self._session_loader()
        return GatekeeperSessionSnapshot(
            agent_id=loaded.agent_id,
            run_id=loaded.run_id,
            conversation_id=loaded.conversation_id,
            lifecycle_state=loaded.lifecycle_state,
            provider_thread_id=loaded.provider_thread_id,
            active_turn_id=loaded.active_turn_id,
            resumable=loaded.resumable,
            last_error=loaded.last_error,
            updated_at=loaded.updated_at,
        )

    def _release_binding(self, run_id: str | None) -> None:
        if run_id is None:
            return
        binding_id = self._binding_ids_by_run_id.pop(run_id, None)
        if binding_id is not None and self.mcp_host is not None:
            self.mcp_host.unregister_binding(binding_id)

    def _ensure_no_active_submission(self) -> None:
        if self._active_handle_run_id is None:
            return
        try:
            snapshot = self.runtime_service.snapshot_handle(self._active_handle_run_id)
        except KeyError:
            self._active_handle_run_id = None
            self._active_handle = None
            return
        raise RuntimeError(f"Gatekeeper already has an active run: {snapshot.run_id}")

    def _persist_run(self, run_record) -> None:
        self.run_store.upsert(run_record)
        instance = self.instance_store.get(run_record.identity.agent_id)
        if instance is None:
            return
        instance.mark_run_updated(run_record)
        self.instance_store.upsert(instance)

    def _persist(self) -> None:
        if self._session_saver is None:
            return
        result = self._session_saver(self.snapshot())
        if inspect.isawaitable(result):
            raise RuntimeError("Gatekeeper session persistence must be synchronous")
