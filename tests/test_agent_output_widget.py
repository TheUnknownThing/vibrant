from __future__ import annotations

from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult

from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.tui.widgets.agent_output import AgentOutput


class AgentOutputHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield AgentOutput()


@pytest.mark.asyncio
async def test_agent_output_displays_streaming_reasoning_with_spinner():
    record = AgentRecord(
        identity={"agent_id": "agent-task-001", "task_id": "task-001", "type": AgentType.CODE},
        lifecycle={"status": AgentStatus.RUNNING, "started_at": datetime.now(timezone.utc)},
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_agents([record])
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-001",
                "task_id": "task-001",
                "type": "reasoning.summary.delta",
                "timestamp": "2026-03-11T10:00:01Z",
                "item_id": "reason-1",
                "delta": "Thinking through the refactor",
            }
        )
        await pilot.pause()

        assert widget.get_thoughts_text() == "Thinking through the refactor"
        assert widget.thoughts_running() is True

        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-001",
                "task_id": "task-001",
                "timestamp": "2026-03-11T10:00:02Z",
                "type": "task.progress",
                "item": {
                    "id": "reason-1",
                    "type": "reasoning",
                    "summary": ["Refactor plan finalized"],
                },
            }
        )
        await pilot.pause()

        assert widget.get_thoughts_text() == "Refactor plan finalized"
        assert widget.thoughts_running() is False


@pytest.mark.asyncio
async def test_agent_output_keeps_canonical_log_view_operational(tmp_path: Path):
    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)

        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-002",
                "task_id": "task-002",
                "type": "session.started",
                "timestamp": "2026-03-11T11:00:00Z",
                "cwd": str(tmp_path),
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-002",
                "task_id": "task-002",
                "type": "content.delta",
                "timestamp": "2026-03-11T11:00:01Z",
                "delta": "Here is the full assistant reply",
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-002",
                "task_id": "task-002",
                "type": "task.progress",
                "timestamp": "2026-03-11T11:00:02Z",
                "item": {"type": "agentMessage", "text": "Narrating implementation details"},
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-002",
                "task_id": "task-002",
                "type": "task.progress",
                "timestamp": "2026-03-11T11:00:03Z",
                "item": {"type": "commandExecution", "command": "pytest", "durationMs": 321},
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-002",
                "task_id": "task-002",
                "type": "task.progress",
                "timestamp": "2026-03-11T11:00:04Z",
                "item": {"type": "fileChange", "path": "src/app.py"},
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-002",
                "task_id": "task-002",
                "type": "runtime.error",
                "timestamp": "2026-03-11T11:00:05Z",
                "error": {"message": "boom"},
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-002",
                "task_id": "task-002",
                "type": "task.completed",
                "timestamp": "2026-03-11T11:00:06Z",
            }
        )
        await pilot.pause()

        rendered = widget.get_rendered_text()

        assert "session.started" not in rendered
        assert "Here is the full assistant reply" not in rendered
        assert "Narrating implementation details" not in rendered
        assert "⏳ $ pytest (321ms)" in rendered
        assert "✏ Modified src/app.py" in rendered
        assert "✗ boom" in rendered
        assert "✓ Task completed" in rendered


@pytest.mark.asyncio
async def test_agent_output_debug_view_renders_canonical_payloads():
    record = AgentRecord(
        identity={"agent_id": "agent-task-003", "task_id": "task-003", "type": AgentType.CODE},
        lifecycle={"status": AgentStatus.RUNNING, "started_at": datetime.now(timezone.utc)},
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_agents([record])
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-003",
                "task_id": "task-003",
                "type": "tool.call.started",
                "timestamp": "2026-03-11T12:00:00Z",
                "tool_name": "vibrant.ask_question",
                "arguments": {"prompt": "Need clarification"},
            }
        )
        await pilot.pause()

        debug_text = widget.get_rendered_text(debug=True)

        assert "tool.call.started" in debug_text
        assert "vibrant.ask_question" in debug_text
