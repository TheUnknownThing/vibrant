from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vibrant.agents.code_agent import CodeAgent
from vibrant.agents.merge_agent import MergeAgent
from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentRunRecord
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.types import WorkspaceHandle


def test_agent_run_record_requires_explicit_run_id() -> None:
    with pytest.raises(ValidationError):
        AgentRunRecord.model_validate(
            {
                "identity": {
                    "agent_id": "legacy-agent-only",
                    "role": "merge",
                }
            }
        )


def test_merge_agent_build_run_record_keeps_run_and_agent_ids_distinct(tmp_path: Path) -> None:
    agent = MergeAgent(
        tmp_path,
        VibrantConfig(),
        adapter_factory=lambda *args, **kwargs: None,
    )

    record = agent.build_run_record(task_id="task-1", branch="task/task-1")

    assert record.identity.agent_id == "merge-task-1"
    assert record.identity.run_id.startswith("run-merge-task-1-")
    assert record.identity.run_id != record.identity.agent_id
    assert "task_id" not in record.context.model_dump()


def test_code_agent_build_run_record_derives_provider_log_paths(tmp_path: Path) -> None:
    agent = CodeAgent(
        tmp_path,
        VibrantConfig(),
        adapter_factory=lambda *args, **kwargs: None,
    )
    task = TaskInfo(id="task-1", title="Add feature", branch="task/task-1", skills=["python"])
    workspace = WorkspaceHandle(
        workspace_id="workspace-1",
        task_id="task-1",
        path=str(tmp_path / "worktree"),
        branch="task/task-1",
        base_branch="main",
    )

    record = agent.build_run_record(task=task, worktree=workspace, prompt="Implement it")

    assert record.provider.native_event_log == str(
        tmp_path / ".vibrant" / "logs" / "providers" / "native" / f"{record.identity.run_id}.ndjson"
    )
    assert record.provider.canonical_event_log == str(
        tmp_path / ".vibrant" / "logs" / "providers" / "canonical" / f"{record.identity.run_id}.ndjson"
    )
