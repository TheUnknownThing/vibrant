from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vibrant.agents.merge_agent import MergeAgent
from vibrant.config import VibrantConfig
from vibrant.models.agent import AgentRunRecord


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
