from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Static

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


@pytest.mark.asyncio
async def test_agent_output_starts_new_reasoning_widget_after_visible_output():
    record = AgentRecord(
        identity={"agent_id": "agent-task-004", "task_id": "task-004", "type": AgentType.CODE},
        lifecycle={"status": AgentStatus.RUNNING, "started_at": datetime.now(timezone.utc)},
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_agents([record])
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-004",
                "task_id": "task-004",
                "type": "reasoning.summary.delta",
                "timestamp": "2026-03-11T12:10:00Z",
                "item_id": "reason-4",
                "delta": "First reasoning block",
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-004",
                "task_id": "task-004",
                "type": "tool.call.started",
                "timestamp": "2026-03-11T12:10:01Z",
                "tool_name": "vibrant.request_user_decision",
            }
        )
        widget.ingest_canonical_event(
            {
                "agent_id": "agent-task-004",
                "task_id": "task-004",
                "timestamp": "2026-03-11T12:10:02Z",
                "type": "task.progress",
                "item": {
                    "id": "reason-4",
                    "type": "reasoning",
                    "summary": ["Second reasoning block"],
                },
            }
        )
        await pilot.pause()

        stream = widget.query_one("#agent-output-stream", Vertical)
        entries = list(stream.children)

        assert len(entries) == 3
        assert isinstance(entries[0], Collapsible)
        assert isinstance(entries[1], Static)
        assert isinstance(entries[2], Collapsible)
        assert "First reasoning block" in str(entries[0].query_one(".agent-output-reasoning-body", Static).render())
        assert "🛠 vibrant.request_user_decision started" in str(entries[1].render())
        assert "Second reasoning block" in str(entries[2].query_one(".agent-output-reasoning-body", Static).render())
        assert widget.get_thoughts_text() == "Second reasoning block"
        assert widget.thoughts_running() is False


@pytest.mark.asyncio
async def test_agent_output_syncs_runtime_snapshot_like_agents():
    older_start = datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc)
    newer_start = datetime(2026, 3, 11, 9, 5, tzinfo=timezone.utc)
    completed = SimpleNamespace(
        identity=SimpleNamespace(agent_id="agent-task-010", task_id="task-010"),
        runtime=SimpleNamespace(status=AgentStatus.COMPLETED.value, started_at=older_start),
        provider=SimpleNamespace(thread_id="thread-010"),
    )
    running = SimpleNamespace(
        identity=SimpleNamespace(agent_id="agent-task-011", task_id="task-011"),
        runtime=SimpleNamespace(status=AgentStatus.RUNNING.value, started_at=newer_start),
        provider=SimpleNamespace(thread_id="thread-011"),
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_agents([completed, running])
        await pilot.pause()

        assert widget.active_agent_id == "agent-task-011"
        assert widget.get_rendered_text() == "Operational activity will appear here…"
