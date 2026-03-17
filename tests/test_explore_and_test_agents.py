from __future__ import annotations

from pathlib import Path

from vibrant.agents.explore_agent import ExploreAgent
from vibrant.agents.test_agent import TestAgent
from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentType
from vibrant.orchestrator.policy.task_loop.testing import build_test_agent_invocation_plan


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
    assert record.provider.runtime_mode == "read-only"
    assert record.identity.run_id.startswith("run-test-task-2-")


def test_test_agent_invocation_plan_includes_pycua_stdio_when_enabled(tmp_path: Path) -> None:
    (tmp_path / "tools" / "pyCUA").mkdir(parents=True)
    cfg = VibrantConfig.model_validate({"extra-config": {"test_agent_enable_pycua": True}})

    plan = build_test_agent_invocation_plan(
        project_root=tmp_path,
        config=cfg,
        run_id="run-test-123",
    )

    assert plan.provider_kind == cfg.provider_kind
    assert any("mcp_servers.pycua.command=\"uv\"" == arg for arg in plan.launch_args)
    assert any("mcp_servers.pycua.args=[\"run\", \"--directory\", \"tools/pyCUA\", \"pycua\"]" == arg for arg in plan.launch_args)
