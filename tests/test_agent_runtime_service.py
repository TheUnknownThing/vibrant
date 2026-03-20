from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vibrant.agents.runtime import AgentHandle, NormalizedRunResult, ProviderResumeHandle, RunState
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.orchestrator.basic.runtime.service import AgentRuntimeService


def test_snapshot_handle_reports_completed_run_state_and_thread() -> None:
    record = AgentRecord(
        identity={
            "run_id": "run-task-001",
            "agent_id": "agent-task-001",
            "role": AgentType.GATEKEEPER.value,
            "type": AgentType.GATEKEEPER,
        },
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
                run_id=record.identity.run_id,
                agent_id=record.identity.agent_id,
                role=record.identity.role,
                status=record.lifecycle.status,
                state=RunState.COMPLETED,
                provider_thread=ProviderResumeHandle(thread_id="thread-1"),
                finished_at=record.lifecycle.finished_at,
            )
        )
        handle = AgentHandle(future)
        handle._set_provider_thread(ProviderResumeHandle(thread_id="thread-1"))

        runtime_service = AgentRuntimeService()
        runtime_service._runs[record.identity.run_id] = SimpleNamespace(
            agent_record=record,
            runtime=SimpleNamespace(),
            handle=handle,
            sequence=0,
            events=[],
        )
        runtime_service._active_runs_by_agent_id[record.identity.agent_id] = record.identity.run_id

        snapshot = runtime_service.snapshot_handle(record.identity.run_id)

        assert snapshot.agent_id == "agent-task-001"
        assert snapshot.run_id == "run-task-001"
        assert snapshot.state == RunState.COMPLETED.value
        assert snapshot.provider_thread_id == "thread-1"
        assert snapshot.awaiting_input is False
        assert snapshot.input_requests == []
        assert record.identity.run_id not in runtime_service._runs
        assert record.identity.agent_id not in runtime_service._active_runs_by_agent_id
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_snapshot_handle_rejects_agent_id_aliases() -> None:
    record = AgentRecord(
        identity={
            "run_id": "run-task-002",
            "agent_id": "agent-task-002",
            "role": AgentType.CODE.value,
            "type": AgentType.CODE,
        },
        lifecycle={"status": AgentStatus.RUNNING},
    )

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        future = loop.create_future()
        future.set_result(
            NormalizedRunResult(
                run_id=record.identity.run_id,
                agent_id=record.identity.agent_id,
                role=record.identity.role,
                status=record.lifecycle.status,
                state=RunState.COMPLETED,
                provider_thread=ProviderResumeHandle(),
            )
        )
        handle = AgentHandle(future)

        runtime_service = AgentRuntimeService()
        runtime_service._runs[record.identity.run_id] = SimpleNamespace(
            agent_record=record,
            runtime=SimpleNamespace(),
            handle=handle,
            sequence=0,
            events=[],
        )
        runtime_service._active_runs_by_agent_id[record.identity.agent_id] = record.identity.run_id

        with pytest.raises(KeyError):
            runtime_service.snapshot_handle(record.identity.agent_id)
    finally:
        asyncio.set_event_loop(None)
        loop.close()
