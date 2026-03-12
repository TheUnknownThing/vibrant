from __future__ import annotations

import pytest

from vibrant.agents.base import AgentBase, AgentRunResult
from vibrant.agents.runtime import BaseAgentRuntime, RunState
from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentRecord, AgentStatus
from vibrant.orchestrator.agents.registry import AgentRegistry
from vibrant.orchestrator.agents.store import AgentRecordStore
from vibrant.orchestrator import OrchestratorStateBackend
from vibrant.orchestrator.state import StateStore
from vibrant.project_init import initialize_project


class _NotifyAgent(AgentBase):
    def get_agent_role(self) -> str:
        return "code"


class _RuntimeCallbackAgent:
    def __init__(self) -> None:
        self.on_agent_record_updated = None
        self.on_canonical_event = None
        self._live_adapter = None

    async def run(
        self,
        *,
        prompt: str,
        agent_record: AgentRecord,
        cwd: str | None = None,
        resume_thread_id: str | None = None,
    ) -> AgentRunResult:
        agent_record.transition_to(AgentStatus.CONNECTING)
        if self.on_agent_record_updated is not None:
            self.on_agent_record_updated(agent_record)
        return AgentRunResult(agent_record=agent_record)


def test_agent_base_notify_record_updated_raises_callback_errors(tmp_path) -> None:
    agent = _NotifyAgent(
        project_root=tmp_path,
        config=VibrantConfig(),
        adapter_factory=object,
        on_agent_record_updated=lambda record: (_ for _ in ()).throw(RuntimeError("persist failed")),
    )

    with pytest.raises(RuntimeError, match="persist failed"):
        agent._notify_record_updated(
            AgentRecord(identity={"agent_id": "agent-1", "task_id": "task-1", "role": "code"})
        )


@pytest.mark.asyncio
async def test_base_agent_runtime_surfaces_on_record_updated_failures() -> None:
    runtime = BaseAgentRuntime(_RuntimeCallbackAgent())
    record = AgentRecord(identity={"agent_id": "agent-1", "task_id": "task-1", "role": "code"})

    def _raise(_record: AgentRecord) -> None:
        raise RuntimeError("persist failed")

    handle = await runtime.start(agent_record=record, prompt="run", on_record_updated=_raise)
    result = await handle.wait()

    assert result.state is RunState.FAILED
    assert result.error == "persist failed"


def test_agent_registry_callback_surfaces_upsert_failures(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    initialize_project(project_root)

    engine = OrchestratorStateBackend.load(project_root)
    state_store = StateStore(engine)
    agent_store = AgentRecordStore(vibrant_dir=project_root / ".vibrant", state_store=state_store)
    registry = AgentRegistry(agent_store=agent_store, vibrant_dir=project_root / ".vibrant")

    def _fail_upsert(*args, **kwargs):
        raise RuntimeError("disk write failed")

    monkeypatch.setattr(registry, "upsert", _fail_upsert)
    callback = registry.make_record_callback()

    with pytest.raises(RuntimeError, match="disk write failed"):
        callback(AgentRecord(identity={"agent_id": "agent-1", "task_id": "task-1", "role": "code"}))
