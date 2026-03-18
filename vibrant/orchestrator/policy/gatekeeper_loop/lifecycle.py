"""Gatekeeper-loop runtime lifecycle mechanics."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from vibrant.agents.gatekeeper import (
    Gatekeeper,
    GatekeeperRequest,
)
from vibrant.models.agent import AgentInstanceProviderConfig, AgentRunRecord, ProviderResumeHandle
from vibrant.agents.runtime import AgentHandle, NormalizedRunResult
from vibrant.providers.base import CanonicalEvent
from vibrant.providers.invocation_compiler import compile_provider_invocation
from vibrant.type_defs import JSONMapping, JSONValue

from ...basic.session import carry_forward_resume_handle
from ...basic.stores import AgentInstanceStore, AgentRunStore
from ...basic.stores.gatekeeper_session import project_gatekeeper_session
from ...basic.conversation import ConversationStreamService
from ...basic.runtime import AgentRuntimeService
from ..shared.capabilities import gatekeeper_binding_preset
from .roles import GATEKEEPER_ROLE, ensure_gatekeeper_instance
from ...types import (
    GatekeeperLifecycleStatus,
    GatekeeperSessionSnapshot,
    RuntimeHandleSnapshot,
    utc_now,
)

if TYPE_CHECKING:
    from ...basic.binding import AgentSessionBindingService
    from ...interface.mcp import OrchestratorFastMCPHost


class GatekeeperLifecycleService:
    """Own the runtime lifecycle of the single Gatekeeper session."""

    def __init__(
        self,
        project_root: Path,
        *,
        runtime_service: AgentRuntimeService,
        conversation_service: ConversationStreamService,
        gatekeeper: Gatekeeper | None = None,
        binding_service: AgentSessionBindingService | None = None,
        mcp_host: OrchestratorFastMCPHost | None = None,
        instance_store: AgentInstanceStore,
        run_store: AgentRunStore,
        session_loader: Callable[[], GatekeeperSessionSnapshot] | None = None,
        session_saver: Callable[[GatekeeperSessionSnapshot], None] | None = None,
    ) -> None:
        self.project_root = project_root
        self.runtime_service = runtime_service
        self.conversation_service = conversation_service
        self.gatekeeper = gatekeeper or Gatekeeper(self.project_root)
        self._binding_service = binding_service
        self._mcp_host = mcp_host
        self.instance_store = instance_store
        self.run_store = run_store
        self._session_loader = session_loader
        self._session_saver = session_saver
        self._session = self._load_session()
        self._active_handle = None
        self._active_handle_run_id: str | None = None
        self._binding_ids_by_run_id: dict[str, str] = {}
        self._subscription = self.runtime_service.subscribe_canonical_events(self._on_runtime_event)

    def attach_mcp_bridge(
        self,
        *,
        binding_service: AgentSessionBindingService,
        mcp_host: OrchestratorFastMCPHost,
    ) -> None:
        self._binding_service = binding_service
        self._mcp_host = mcp_host

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
            self._session.last_error = None
            self._session.updated_at = utc_now()
            self._persist()
        return self.snapshot()

    async def resume_session(self) -> GatekeeperSessionSnapshot:
        return await self.resume_or_start()

    async def submit(
        self,
        *,
        request: GatekeeperRequest,
        submission_id: str,
        resume: bool = True,
    ):
        self._ensure_no_active_submission()
        session = await self.resume_or_start()
        provider_thread = None
        if resume:
            provider_thread = self._resolve_resume_handle(session=session)
        prompt = (
            self.gatekeeper.render_resume_prompt(request)
            if provider_thread is not None
            else self.gatekeeper.render_prompt(request)
        )
        logical_run_id = session.run_id if resume and session.run_id else None
        previous_record = self.run_store.get(logical_run_id) if logical_run_id is not None else None
        agent_record = self.gatekeeper.build_run_record(
            request,
            agent_id=GATEKEEPER_ROLE,
            role=GATEKEEPER_ROLE,
            run_id=logical_run_id,
        )
        carry_forward_resume_handle(
            agent_record.provider,
            previous_record.provider if previous_record is not None else None,
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
        self.conversation_service.bind_run(
            conversation_id=conversation_id,
            run_id=agent_record.identity.run_id,
        )

        self._session.agent_id = instance.identity.agent_id
        self._session.run_id = agent_record.identity.run_id
        self._session.conversation_id = conversation_id
        self._session.lifecycle_state = GatekeeperLifecycleStatus.STARTING
        self._session.last_error = None
        self._session.updated_at = utc_now()
        self._persist()

        binding_service, mcp_host = self._require_mcp_bridge()
        use_inprocess_mcp = bool(getattr(self.gatekeeper.agent.adapter_factory, "supports_inprocess_mcp", False))
        if not use_inprocess_mcp:
            await mcp_host.ensure_started()
        bound_capabilities = binding_service.bind_preset(
            preset=gatekeeper_binding_preset(binding_service.mcp_server, agent_record.identity.run_id),
            run_id=agent_record.identity.run_id,
            conversation_id=conversation_id,
        )
        registered_binding = mcp_host.register_binding(bound_capabilities)
        self._binding_ids_by_run_id[agent_record.identity.run_id] = registered_binding.binding_id
        invocation_plan = compile_provider_invocation(
            agent_record.provider.kind,
            bound_capabilities.access,
        )
        invocation_plan.debug_metadata["mcp_asgi_app"] = mcp_host.http_app()
        if use_inprocess_mcp:
            mcp_access = invocation_plan.debug_metadata.get("mcp_access")
            if isinstance(mcp_access, dict) and not mcp_access.get("endpoint_url"):
                mcp_access["endpoint_url"] = "http://127.0.0.1/mcp"
            elif isinstance(mcp_access, list):
                for descriptor in mcp_access:
                    if isinstance(descriptor, dict) and not descriptor.get("endpoint_url"):
                        descriptor["endpoint_url"] = "http://127.0.0.1/mcp"

        try:
            if provider_thread is not None:
                handle = await self.runtime_service.resume_run(
                    agent_record=agent_record,
                    prompt=prompt,
                    provider_thread=provider_thread,
                    cwd=self.project_root,
                    runtime=self.gatekeeper.runtime,
                    on_record_updated=self._persist_run,
                    invocation_plan=invocation_plan,
                )
            else:
                handle = await self.runtime_service.start_run(
                    agent_record=agent_record,
                    prompt=prompt,
                    cwd=self.project_root,
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

    async def pause_session(self, *, reason: str | None = None) -> GatekeeperSessionSnapshot:
        if self._active_handle_run_id is not None:
            self.runtime_service.annotate_run(self._active_handle_run_id, stop_reason="paused")
            await self.runtime_service.kill_run(self._active_handle_run_id)
            self._release_binding(self._active_handle_run_id)
            self._active_handle_run_id = None
            self._active_handle = None
        self._session.lifecycle_state = GatekeeperLifecycleStatus.STOPPED
        self._session.active_turn_id = None
        self._session.last_error = reason
        self._session.updated_at = utc_now()
        self._persist()
        return self.snapshot()

    async def respond_to_request(
        self,
        run_id: str,
        request_id: int | str,
        *,
        result: JSONValue | None = None,
        error: JSONMapping | None = None,
    ) -> RuntimeHandleSnapshot:
        handle = self._active_handle
        if handle is None or self._active_handle_run_id != run_id:
            raise KeyError(f"Gatekeeper active handle not available for run {run_id}")

        await handle.respond_to_request(request_id, result=result, error=error)
        self._session.lifecycle_state = (
            GatekeeperLifecycleStatus.AWAITING_USER
            if handle.awaiting_input
            else GatekeeperLifecycleStatus.RUNNING
        )
        self._session.updated_at = utc_now()
        self._persist()
        return RuntimeHandleSnapshot(
            agent_id=self._session.agent_id or run_id,
            run_id=run_id,
            state=handle.state.value,
            provider_thread_id=handle.provider_thread.thread_id,
            awaiting_input=handle.awaiting_input,
            input_requests=handle.input_requests,
        )

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
        run_record = self._run_record_for_session(self._session)
        return project_gatekeeper_session(
            GatekeeperSessionSnapshot(
                agent_id=self._session.agent_id,
                run_id=self._session.run_id,
                conversation_id=self._session.conversation_id,
                lifecycle_state=self._session.lifecycle_state,
                provider_thread_id=self._session.provider_thread_id,
                active_turn_id=self._session.active_turn_id,
                resumable=self._session.resumable,
                last_error=self._session.last_error,
                updated_at=self._session.updated_at,
            ),
            run_record=run_record,
        )

    async def _monitor_handle(self, run_id: str, handle: AgentHandle) -> None:
        try:
            result: NormalizedRunResult = await handle.wait()
        except Exception as exc:
            if self._is_paused_run(run_id):
                self._session.lifecycle_state = GatekeeperLifecycleStatus.STOPPED
            else:
                self._session.lifecycle_state = GatekeeperLifecycleStatus.FAILED
                self._session.last_error = str(exc)
            self._session.updated_at = utc_now()
            self._release_binding(run_id)
            self._persist()
            return

        self._session.provider_thread_id = result.provider_thread.thread_id
        self._session.resumable = result.provider_thread.resumable
        self._session.run_id = result.run_id
        self._session.updated_at = utc_now()
        if self._is_paused_run(run_id):
            self._session.lifecycle_state = GatekeeperLifecycleStatus.STOPPED
            self._session.last_error = None
        elif result.awaiting_input:
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
        if self._session.lifecycle_state is GatekeeperLifecycleStatus.STOPPED and self._is_paused_run(run_id):
            provider_thread_id = event.get("provider_thread_id")
            if isinstance(provider_thread_id, str) and provider_thread_id:
                self._session.provider_thread_id = provider_thread_id
                self._session.resumable = True
            self._session.updated_at = utc_now()
            self._persist()
            return

        event_type = str(event.get("type") or "")
        if event_type == "turn.started":
            turn_id = event.get("turn_id")
            self._session.active_turn_id = turn_id if isinstance(turn_id, str) else None
            self._session.lifecycle_state = GatekeeperLifecycleStatus.RUNNING
        elif event_type == "turn.completed":
            self._session.active_turn_id = None
            if self._session.lifecycle_state not in {
                GatekeeperLifecycleStatus.AWAITING_USER,
                GatekeeperLifecycleStatus.FAILED,
            }:
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
        projected = project_gatekeeper_session(
            loaded,
            run_record=self._run_record_for_session(loaded),
        )
        return GatekeeperSessionSnapshot(
            agent_id=projected.agent_id,
            run_id=projected.run_id,
            conversation_id=projected.conversation_id,
            lifecycle_state=projected.lifecycle_state,
            provider_thread_id=projected.provider_thread_id,
            active_turn_id=projected.active_turn_id,
            resumable=projected.resumable,
            last_error=projected.last_error,
            updated_at=projected.updated_at,
        )

    def _release_binding(self, run_id: str | None) -> None:
        if run_id is None:
            return
        binding_id = self._binding_ids_by_run_id.pop(run_id, None)
        if binding_id is not None and self._mcp_host is not None:
            self._mcp_host.unregister_binding(binding_id)

    def _require_mcp_bridge(self) -> tuple[AgentSessionBindingService, OrchestratorFastMCPHost]:
        if self._binding_service is None or self._mcp_host is None:
            raise RuntimeError("GatekeeperLifecycleService requires MCP binding services before submit()")
        return self._binding_service, self._mcp_host

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
        instance.mark_run_updated(
            agent_id=run_record.identity.agent_id,
            run_id=run_record.identity.run_id,
            status=run_record.lifecycle.status,
        )
        self.instance_store.upsert(instance)

    def _persist(self) -> None:
        if self._session_saver is None:
            return
        result = self._session_saver(self.snapshot())
        if inspect.isawaitable(result):
            raise RuntimeError("Gatekeeper session persistence must be synchronous")

    def _resolve_resume_handle(
        self,
        *,
        session: GatekeeperSessionSnapshot,
    ) -> ProviderResumeHandle | None:
        if session.run_id is None:
            return None
        return self.run_store.resume_handle_for_run(session.run_id)

    def _run_record_for_session(self, session: GatekeeperSessionSnapshot) -> AgentRunRecord | None:
        if session.run_id is None:
            return None
        return self.run_store.get(session.run_id)

    def _is_paused_run(self, run_id: str) -> bool:
        record = self.run_store.get(run_id)
        return bool(record is not None and record.lifecycle.stop_reason == "paused")
