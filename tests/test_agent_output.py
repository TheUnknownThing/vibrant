"""Tests for the Panel B agent output widget."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.tui.widgets.agent_output import AgentOutput, MAX_BUFFER_LINES


class AgentOutputHarness(App):
    def __init__(self, records: list[AgentRecord]) -> None:
        super().__init__()
        self._records = records

    def compose(self) -> ComposeResult:
        yield AgentOutput(id="agent-output")

    async def on_mount(self) -> None:
        widget = self.query_one(AgentOutput)
        widget.sync_agents(self._records)
        widget.focus()


def _agent_record(
    agent_id: str,
    *,
    task_id: str,
    status: AgentStatus = AgentStatus.RUNNING,
    canonical_log: str | None = None,
    native_log: str | None = None,
    started_at: datetime | None = None,
) -> AgentRecord:
    return AgentRecord(
        agent_id=agent_id,
        task_id=task_id,
        type=AgentType.CODE,
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
        provider=AgentProviderMetadata(
            provider_thread_id=f"thread-{agent_id}",
            canonical_event_log=canonical_log,
            native_event_log=native_log,
        ),
    )


@pytest.mark.asyncio
async def test_agent_output_streams_live_canonical_text():
    record = _agent_record("agent-1", task_id="task-001")
    app = AgentOutputHarness([record])

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.ingest_canonical_event(
            {
                "type": "turn.started",
                "timestamp": "2026-03-08T10:00:00Z",
                "agent_id": "agent-1",
                "task_id": "task-001",
                "turn": {"id": "turn-1"},
            }
        )
        widget.ingest_canonical_event(
            {
                "type": "content.delta",
                "timestamp": "2026-03-08T10:00:01Z",
                "agent_id": "agent-1",
                "task_id": "task-001",
                "delta": "hello world",
            }
        )
        await pilot.pause()

        rendered = widget.get_rendered_text()
        assert "turn.started id=turn-1" in rendered
        assert "hello world" in rendered


@pytest.mark.asyncio
async def test_agent_output_f5_cycles_between_agents():
    records = [
        _agent_record("agent-1", task_id="task-001", started_at=datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)),
        _agent_record("agent-2", task_id="task-002", started_at=datetime(2026, 3, 8, 10, 1, tzinfo=timezone.utc)),
    ]
    app = AgentOutputHarness(records)

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        assert widget.active_agent_id == "agent-2"

        await pilot.press("f5")
        await pilot.pause()
        assert widget.active_agent_id == "agent-1"

        await pilot.press("f5")
        await pilot.pause()
        assert widget.active_agent_id == "agent-2"


@pytest.mark.asyncio
async def test_agent_output_s_toggles_scroll_lock():
    record = _agent_record("agent-1", task_id="task-001")
    app = AgentOutputHarness([record])

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        assert widget.auto_follow_enabled is True

        await pilot.press("s")
        await pilot.pause()
        assert widget.auto_follow_enabled is False

        await pilot.press("s")
        await pilot.pause()
        assert widget.auto_follow_enabled is True


@pytest.mark.asyncio
async def test_agent_output_ring_buffer_is_capped_at_10000_lines():
    record = _agent_record("agent-1", task_id="task-001")
    app = AgentOutputHarness([record])

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        for index in range(MAX_BUFFER_LINES + 25):
            widget.ingest_canonical_event(
                {
                    "type": "runtime.error",
                    "timestamp": "2026-03-08T10:00:00Z",
                    "agent_id": "agent-1",
                    "task_id": "task-001",
                    "error": f"line-{index}",
                }
            )
        await pilot.pause()

        rendered = widget.get_rendered_text("agent-1")
        assert widget.get_buffer_line_count("agent-1") == MAX_BUFFER_LINES
        assert "line-0" not in rendered
        assert f"line-{MAX_BUFFER_LINES + 24}" in rendered


@pytest.mark.asyncio
async def test_agent_output_debug_view_tails_native_log(tmp_path: Path):
    native_log = tmp_path / "native.ndjson"
    canonical_log = tmp_path / "canonical.ndjson"
    native_log.write_text(
        json.dumps({"timestamp": "2026-03-08T10:00:00Z", "event": "stderr.line", "data": {"line": "boom"}}) + "\n",
        encoding="utf-8",
    )
    canonical_log.write_text("", encoding="utf-8")

    record = _agent_record(
        "agent-1",
        task_id="task-001",
        native_log=str(native_log),
        canonical_log=str(canonical_log),
    )
    app = AgentOutputHarness([record])

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(0.35)
        widget = app.query_one(AgentOutput)
        widget.action_toggle_debug_view()
        await pilot.pause()

        rendered = widget.get_rendered_text("agent-1", debug=True)
        assert "stderr boom" in rendered
