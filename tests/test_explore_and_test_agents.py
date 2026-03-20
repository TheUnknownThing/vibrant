from __future__ import annotations

from pathlib import Path

import pytest

from vibrant.agents.explore_agent import ExploreAgent
from vibrant.agents.runtime import BaseAgentRuntime, RunState
from vibrant.agents.test_agent import TestAgent
from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentType
from vibrant.orchestrator.policy.task_loop.testing import build_test_agent_invocation_plan
from vibrant.prompts import build_test_prompt


def test_explore_agent_build_run_record_is_read_only(tmp_path: Path) -> None:
    agent = ExploreAgent(tmp_path, VibrantConfig(), adapter_factory=lambda *args, **kwargs: None)

    record = agent.build_run_record(
        task_id="task-1",
        branch="task/task-1",
        workspace_path=str(tmp_path / "worktree"),
        prompt="Explore architecture",
        vibrant_dir=tmp_path / ".vibrant",
    )

    assert record.identity.type is AgentType.EXPLORE
    assert record.provider.runtime_mode == "read-only"
    assert record.identity.run_id.startswith("run-explore-task-1-")


def test_test_agent_build_run_record_is_read_only(tmp_path: Path) -> None:
    agent = TestAgent(tmp_path, VibrantConfig(), adapter_factory=lambda *args, **kwargs: None)

    record = agent.build_run_record(
        task_id="task-2",
        branch="task/task-2",
        workspace_path=str(tmp_path / "worktree"),
        prompt="Run validation commands",
        vibrant_dir=tmp_path / ".vibrant",
    )

    assert record.identity.type is AgentType.TEST
    assert record.provider.runtime_mode == "workspace-write"
    assert record.identity.run_id.startswith("run-test-task-2-")


def test_test_agent_invocation_plan_includes_pycua_stdio_when_enabled(tmp_path: Path) -> None:
    (tmp_path / "tools" / "pyCUA").mkdir(parents=True)
    cfg = VibrantConfig.model_validate({"extra-config": {"test_agent_enable_pycua": True}})

    plan = build_test_agent_invocation_plan(
        config=cfg,
        run_id="run-test-123",
    )

    assert plan.provider_kind == cfg.provider_kind
    assert any("mcp_servers.pycua.command=\"uv\"" == arg for arg in plan.launch_args)
    assert any("mcp_servers.pycua.args=[\"run\", \"--directory\", \"tools/pyCUA\", \"pycua\"]" == arg for arg in plan.launch_args)


def test_build_test_prompt_does_not_require_ordered_validation_commands() -> None:
    prompt = build_test_prompt(
        project="demo",
        task_id="task-3",
        branch="task/task-3",
        code_summary="Implemented the requested change.",
    )

    assert "Run the validation commands in order" not in prompt
    assert "Select the validation steps that best verify the implementation" in prompt
    assert "End your response with exactly one `<vibrant_summary>...</vibrant_summary>` block." in prompt
    assert "captured as the validation summary sent to the gatekeeper" in prompt


class _TaggedSummaryAdapter:
    def __init__(self, **kwargs) -> None:
        self._on_canonical_event = kwargs["on_canonical_event"]
        self.client = None

    async def start_session(self, **kwargs):
        return dict(kwargs)

    async def start_thread(self, **kwargs):
        return {"thread": {"id": "thread-test-001"}, **kwargs}

    async def start_turn(self, **kwargs):
        del kwargs
        await self._on_canonical_event(
            {
                "type": "content.delta",
                "delta": (
                    "Validation complete.\n"
                    "<vibrant_summary>\n"
                    "Validation passed after running uv run pytest.\n"
                    "</vibrant_summary>\n"
                    "Detailed notes that should not reach the gatekeeper."
                ),
            }
        )
        await self._on_canonical_event({"type": "turn.completed"})
        return {}

    async def stop_session(self) -> None:
        return None

    async def respond_to_request(self, request_id, **kwargs) -> None:
        del request_id, kwargs
        return None


@pytest.mark.asyncio
async def test_test_agent_summary_prefers_tagged_summary_block(tmp_path: Path) -> None:
    runtime = BaseAgentRuntime(
        TestAgent(
            tmp_path,
            VibrantConfig(),
            adapter_factory=lambda **kwargs: _TaggedSummaryAdapter(**kwargs),
        )
    )

    record = TestAgent(
        tmp_path,
        VibrantConfig(),
        adapter_factory=lambda **kwargs: _TaggedSummaryAdapter(**kwargs),
    ).build_run_record(
        task_id="task-4",
        branch="task/task-4",
        workspace_path=str(tmp_path),
        prompt="Run validation commands",
        vibrant_dir=tmp_path / ".vibrant",
    )

    handle = await runtime.start(
        agent_record=record,
        prompt="Start",
    )
    result = await handle.wait()

    assert result.state is RunState.COMPLETED
    assert result.summary == "Validation passed after running uv run pytest."
