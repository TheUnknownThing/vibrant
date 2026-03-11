from __future__ import annotations

from vibrant.orchestrator.agent_output import AgentOutputProjectionService
from vibrant.orchestrator.types import AgentOutput


def test_agent_output_defaults_include_thinking_state() -> None:
    output = AgentOutput(agent_id="agent-1", task_id="task-1")

    assert output.thinking.text == ""
    assert output.thinking.status == "idle"
    assert output.thinking.item_id is None
    assert output.thinking.timestamp is None


def test_projection_tracks_reasoning_summary_deltas_separately_from_response() -> None:
    service = AgentOutputProjectionService()

    output = service.ingest(
        {
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "turn.started",
            "timestamp": "2026-03-11T12:00:00Z",
            "turn": {"id": "turn-1"},
        }
    )
    assert output is not None

    output = service.ingest(
        {
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "reasoning.summary.delta",
            "timestamp": "2026-03-11T12:00:01Z",
            "item_id": "reason-1",
            "delta": "Thinking through the refactor",
        }
    )

    assert output is not None
    assert output.partial_text == ""
    assert output.thinking.text == "Thinking through the refactor"
    assert output.thinking.status == "streaming"
    assert output.thinking.item_id == "reason-1"
    assert output.progress == []

    output = service.ingest(
        {
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "task.progress",
            "timestamp": "2026-03-11T12:00:02Z",
            "item": {"type": "reasoning", "id": "reason-1", "summary": ["Refactor plan finalized"]},
        }
    )

    assert output is not None
    assert output.thinking.text == "Refactor plan finalized"
    assert output.thinking.status == "completed"
    assert output.thinking.item_id == "reason-1"

    output = service.ingest(
        {
            "agent_id": "agent-1",
            "task_id": "task-1",
            "type": "content.delta",
            "timestamp": "2026-03-11T12:00:03Z",
            "delta": "Implemented the projection changes.",
        }
    )

    assert output is not None
    assert output.partial_text == "Implemented the projection changes."
    assert output.thinking.text == "Refactor plan finalized"
