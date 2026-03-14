"""Runtime capability wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .service import AgentRuntimeService
from ...types import CanonicalEventHandler, RuntimeExecutionResult, RuntimeHandleSnapshot


@dataclass(slots=True)
class AgentRuntimeCapability:
    """Expose generic runtime mechanics without policy decisions."""

    service: AgentRuntimeService

    async def start_run(self, **kwargs: Any) -> Any:
        return await self.service.start_run(**kwargs)

    async def resume_run(self, **kwargs: Any) -> Any:
        return await self.service.resume_run(**kwargs)

    async def wait_for_run(self, run_id: str) -> RuntimeExecutionResult:
        return await self.service.wait_for_run(run_id)

    async def interrupt_run(self, run_id: str) -> RuntimeHandleSnapshot:
        return await self.service.interrupt_run(run_id)

    async def kill_run(self, run_id: str) -> RuntimeHandleSnapshot:
        return await self.service.kill_run(run_id)

    def snapshot_handle(self, run_id: str) -> RuntimeHandleSnapshot:
        return self.service.snapshot_handle(run_id)

    def subscribe_canonical_events(
        self,
        callback: CanonicalEventHandler,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
        event_types: Sequence[str] | None = None,
    ) -> Any:
        return self.service.subscribe_canonical_events(
            callback,
            agent_id=agent_id,
            run_id=run_id,
            event_types=event_types,
        )


__all__ = ["AgentRuntimeCapability", "AgentRuntimeService"]
