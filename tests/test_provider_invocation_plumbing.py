"""Focused tests for provider invocation-plan runtime handoff."""

from __future__ import annotations

from typing import Any

import pytest

from vibrant.agents.base import AgentBase, AgentRunResult
from vibrant.agents.runtime import BaseAgentRuntime, RunState
from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentRecord, AgentType, ProviderResumeHandle
from vibrant.providers.invocation import ProviderInvocationPlan


def _make_agent_record() -> AgentRecord:
    return AgentRecord(
        identity={
            "run_id": "run-task-001",
            "agent_id": "agent-task-001",
            "role": AgentType.CODE.value,
            "type": AgentType.CODE,
        }
    )


class _FakeAdapter:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self._on_canonical_event = kwargs["on_canonical_event"]
        self.client = None

    async def start_session(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    async def start_thread(self, **kwargs: Any) -> dict[str, Any]:
        return {"thread": {"id": "thread-001"}, **kwargs}

    async def resume_thread(self, thread_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"thread": {"id": thread_id}, **kwargs}

    async def start_turn(self, **kwargs: Any) -> dict[str, Any]:
        await self._on_canonical_event({"type": "turn.completed"})
        return {"summary": "done"}

    async def stop_session(self) -> None:
        return None

    async def respond_to_request(self, request_id: int | str, **kwargs: Any) -> None:
        return None


class _TestAgent(AgentBase):
    def get_agent_type(self) -> AgentType:
        return AgentType.CODE


class _RuntimeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.on_agent_record_updated = None
        self.on_canonical_event = None
        self._live_adapter = None

    async def run(self, **kwargs: Any) -> AgentRunResult:
        self.calls.append(dict(kwargs))
        return AgentRunResult(
            transcript="finished",
            agent_record=kwargs["agent_record"],
        )


@pytest.mark.asyncio
async def test_agent_base_run_passes_project_launch_args_and_invocation_plan() -> None:
    captured: dict[str, Any] = {}

    def adapter_factory(**kwargs: Any) -> _FakeAdapter:
        captured.update(kwargs)
        return _FakeAdapter(**kwargs)

    agent = _TestAgent(
        project_root="/tmp/project",
        config=VibrantConfig(launch_args=["--verbose"]),
        adapter_factory=adapter_factory,
    )
    invocation_plan = ProviderInvocationPlan(
        launch_args=["--config", "mcp_servers.local.command='uvx fastmcp'"]
    )

    result = await agent.run(
        prompt="Ship it",
        agent_record=_make_agent_record(),
        invocation_plan=invocation_plan,
    )

    assert captured["launch_args"] == ["--verbose"]
    assert captured["invocation_plan"] is invocation_plan
    assert result.error is None
    assert result.agent_record is not None
    assert result.agent_record.outcome.summary == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_base_agent_runtime_forwards_invocation_plan(resume: bool) -> None:
    runtime_agent = _RuntimeAgent()
    runtime = BaseAgentRuntime(runtime_agent)
    invocation_plan = ProviderInvocationPlan(launch_args=["--config", "foo='bar'"])
    agent_record = _make_agent_record()

    if resume:
        handle = await runtime.resume_run(
            agent_record=agent_record,
            prompt="Continue",
            provider_thread=ProviderResumeHandle(kind="codex", thread_id="thread-123"),
            invocation_plan=invocation_plan,
        )
    else:
        handle = await runtime.start(
            agent_record=agent_record,
            prompt="Start",
            invocation_plan=invocation_plan,
        )

    result = await handle.wait()

    assert runtime_agent.calls[0]["invocation_plan"] is invocation_plan
    assert runtime_agent.calls[0]["resume_thread_id"] == ("thread-123" if resume else None)
    assert result.state is RunState.COMPLETED
    assert result.run_id == "run-task-001"
    assert result.agent_id == "agent-task-001"
    assert result.role == AgentType.CODE.value
