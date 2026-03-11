from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.tui.widgets.agent_output import AgentOutput


class AgentOutputHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield AgentOutput()


def _append_ndjson(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


@pytest.mark.asyncio
async def test_agent_output_displays_streaming_reasoning_with_spinner(tmp_path: Path):
    native_log = tmp_path / "native.ndjson"
    _append_ndjson(
        native_log,
        {
            "timestamp": "2026-03-11T10:00:00Z",
            "event": "jsonrpc.notification.received",
            "data": {
                "method": "item/started",
                "params": {"item": {"id": "reason-1", "type": "reasoning"}},
            },
        },
    )
    _append_ndjson(
        native_log,
        {
            "timestamp": "2026-03-11T10:00:01Z",
            "event": "jsonrpc.notification.received",
            "data": {
                "method": "item/reasoning/textDelta",
                "params": {"itemId": "reason-1", "delta": "Thinking through the refactor"},
            },
        },
    )

    record = AgentRecord(
        identity={"agent_id": "agent-task-001", "task_id": "task-001", "type": AgentType.CODE},
        lifecycle={"status": AgentStatus.RUNNING, "started_at": datetime.now(timezone.utc)},
        provider={"native_event_log": str(native_log)},
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_agents([record])
        await pilot.pause()

        assert widget.get_thoughts_text() == "Thinking through the refactor"
        assert widget.thoughts_running() is True

        _append_ndjson(
            native_log,
            {
                "timestamp": "2026-03-11T10:00:02Z",
                "event": "jsonrpc.notification.received",
                "data": {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": "reason-1",
                            "type": "reasoning",
                            "summary": ["Refactor plan finalized"],
                        }
                    },
                },
            },
        )
        widget.poll_native_logs_now()
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
