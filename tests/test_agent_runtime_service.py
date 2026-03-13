from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from vibrant.agents.runtime import AgentHandle, NormalizedRunResult, ProviderResumeHandle, RunState
from vibrant.models.agent import AgentStatus
from vibrant.orchestrator import OrchestratorStateBackend
from vibrant.orchestrator.agents.registry import AgentRegistry
from vibrant.orchestrator.agents.runtime import AgentRuntimeService
from vibrant.orchestrator.agents.store import AgentRecordStore
from vibrant.orchestrator.state import StateStore
from vibrant.project_init import initialize_project


def test_snapshot_handle_marks_completed_handle_inactive(tmp_path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    initialize_project(project_root)

    engine = OrchestratorStateBackend.load(project_root)
    state_store = StateStore(engine)
    agent_store = AgentRecordStore(vibrant_dir=project_root / ".vibrant", state_store=state_store)
    registry = AgentRegistry(agent_store=agent_store, vibrant_dir=project_root / ".vibrant")
    runtime_service = AgentRuntimeService(agent_registry=registry, agent_runtime=SimpleNamespace(start=lambda **_: None))

    record = registry.create_task_agent_record(
        role="gatekeeper",
        task_id="gatekeeper-user_conversation",
        branch=None,
        worktree_path=str(project_root),
        prompt="Test prompt",
    )
    record.transition_to(AgentStatus.CONNECTING)
    record.transition_to(AgentStatus.RUNNING)
    record.transition_to(AgentStatus.COMPLETED, finished_at=datetime.now(timezone.utc))
    record.provider.provider_thread_id = "thread-1"
    registry.upsert(record)

    loop = asyncio.new_event_loop()
    try:
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
        runtime_service._handles[record.identity.agent_id] = handle

        snapshot = runtime_service.snapshot_handle(handle=handle, agent_record=record)

        assert snapshot.runtime.done is True
        assert snapshot.runtime.active is False
    finally:
        loop.close()
