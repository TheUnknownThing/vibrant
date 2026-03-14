from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from vibrant.agents.runtime import AgentHandle, NormalizedRunResult, ProviderResumeHandle, RunState
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.orchestrator.runtime.service import AgentRuntimeService


def test_snapshot_handle_reports_completed_run_state_and_thread() -> None:
    record = AgentRecord(
        identity={"agent_id": "agent-task-001", "task_id": "task-001", "type": AgentType.GATEKEEPER},
        lifecycle={
            "status": AgentStatus.COMPLETED,
            "started_at": datetime.now(timezone.utc),
            "finished_at": datetime.now(timezone.utc),
        },
        provider={"provider_thread_id": "thread-1"},
    )

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        future = loop.create_future()
        future.set_result(
            NormalizedRunResult(
                agent_record=record,
                state=RunState.COMPLETED,
                provider_thread=ProviderResumeHandle(thread_id="thread-1"),
                finished_at=record.lifecycle.finished_at,
            )
        )
        handle = AgentHandle(future)
        handle._set_provider_thread(ProviderResumeHandle(thread_id="thread-1"))

        runtime_service = AgentRuntimeService()
        runtime_service._runs[record.identity.agent_id] = SimpleNamespace(
            agent_record=record,
            runtime=SimpleNamespace(),
            handle=handle,
            sequence=0,
            events=[],
        )

        snapshot = runtime_service.snapshot_handle(record.identity.agent_id)

        assert snapshot.agent_id == "agent-task-001"
        assert snapshot.state == RunState.COMPLETED.value
        assert snapshot.provider_thread_id == "thread-1"
        assert snapshot.awaiting_input is False
        assert snapshot.input_requests == []
    finally:
        asyncio.set_event_loop(None)
        loop.close()
