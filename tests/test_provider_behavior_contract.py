"""Contract tests for provider behavior the runtime and orchestrator consume."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.e2e.fixture_provider import FixtureProviderAdapter
from vibrant.agents.base import AgentBase
from vibrant.agents.runtime import BaseAgentRuntime, RunState
from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentRecord, AgentType
from vibrant.project_init import initialize_project


def _make_agent_record(*, run_id: str, agent_type: AgentType = AgentType.CODE) -> AgentRecord:
    return AgentRecord(
        identity={
            "run_id": run_id,
            "agent_id": f"agent-{run_id}",
            "role": agent_type.value,
            "type": agent_type,
        },
        context={"worktree_path": "/tmp/project"},
    )


class _FixtureAgent(AgentBase):
    def get_agent_type(self) -> AgentType:
        return AgentType.CODE


class _InteractiveFixtureAgent(_FixtureAgent):
    def should_auto_reject_requests(self) -> bool:
        return False


async def _wait_for_input(runtime_handle, *, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not runtime_handle.awaiting_input:
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_fixture_provider_exposes_minimal_success_contract(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    agent = _FixtureAgent(
        project_root=tmp_path,
        config=VibrantConfig(),
        adapter_factory=FixtureProviderAdapter,
    )
    runtime = BaseAgentRuntime(agent)
    agent_record = _make_agent_record(run_id="run-contract-success")
    agent_record.context.worktree_path = str(tmp_path)

    handle = await runtime.start(
        agent_record=agent_record,
        prompt="Implement a deterministic change.\n[mock:long]",
        cwd=str(tmp_path),
    )
    result = await handle.wait()

    event_types = [str(event.get("type") or "") for event in result.events]

    assert result.state is RunState.COMPLETED
    assert result.error is None
    assert result.provider_thread.resumable
    assert result.provider_thread.thread_id == agent_record.provider.provider_thread_id
    assert result.provider_events_ref == agent_record.provider.canonical_event_log
    assert "session.started" in event_types
    assert "thread.started" in event_types
    assert "turn.started" in event_types
    assert "content.delta" in event_types
    assert "turn.completed" in event_types
    assert "runtime.error" not in event_types


@pytest.mark.asyncio
async def test_fixture_provider_request_flow_matches_runtime_contract(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    agent = _InteractiveFixtureAgent(
        project_root=tmp_path,
        config=VibrantConfig(),
        adapter_factory=FixtureProviderAdapter,
    )
    runtime = BaseAgentRuntime(agent)
    agent_record = _make_agent_record(run_id="run-contract-request", agent_type=AgentType.GATEKEEPER)
    agent_record.context.worktree_path = str(tmp_path)

    handle = await runtime.start(
        agent_record=agent_record,
        prompt="Need one decision before continuing.\n[mock:question]",
        cwd=str(tmp_path),
    )
    await _wait_for_input(handle)

    assert handle.awaiting_input is True
    assert len(handle.input_requests) == 1
    assert handle.input_requests[0].request_kind == "user-input"

    await handle.respond_to_request(
        handle.input_requests[0].request_id,
        result={"answer": "Proceed with OAuth first."},
    )
    result = await handle.wait()

    event_types = [str(event.get("type") or "") for event in result.events]

    assert result.state is RunState.COMPLETED
    assert result.error is None
    assert "request.opened" in event_types
    assert "request.resolved" in event_types
    assert "turn.completed" in event_types
