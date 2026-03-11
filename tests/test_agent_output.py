"""Tests for the Panel B agent output widget."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.tui.widgets.agent_output import AgentOutput, MAX_BUFFER_LINES


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


def test_agent_output_streams_live_canonical_text():
    record = _agent_record("agent-1", task_id="task-001")
    widget = AgentOutput()
    widget.sync_agents([record])

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

    rendered = widget.get_rendered_text()
    assert "turn.started id=turn-1" in rendered
    assert "hello world" in rendered


def test_agent_output_f5_cycles_between_agents():
    records = [
        _agent_record("agent-1", task_id="task-001", started_at=datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)),
        _agent_record("agent-2", task_id="task-002", started_at=datetime(2026, 3, 8, 10, 1, tzinfo=timezone.utc)),
    ]
    widget = AgentOutput()
    widget.sync_agents(records)

    assert widget.active_agent_id == "agent-2"

    widget.action_cycle_agent()
    assert widget.active_agent_id == "agent-1"

    widget.action_cycle_agent()
    assert widget.active_agent_id == "agent-2"


def test_agent_output_s_toggles_scroll_lock():
    record = _agent_record("agent-1", task_id="task-001")
    widget = AgentOutput()
    widget.sync_agents([record])

    assert widget.auto_follow_enabled is True

    widget.action_toggle_scroll_lock()
    assert widget.auto_follow_enabled is False

    widget.action_toggle_scroll_lock()
    assert widget.auto_follow_enabled is True


def test_agent_output_ring_buffer_is_capped_at_10000_lines():
    record = _agent_record("agent-1", task_id="task-001")
    widget = AgentOutput()
    widget.sync_agents([record])

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

    rendered = widget.get_rendered_text("agent-1")
    assert widget.get_buffer_line_count("agent-1") == MAX_BUFFER_LINES
    assert "line-0" not in rendered
    assert f"line-{MAX_BUFFER_LINES + 24}" in rendered


def test_agent_output_debug_view_tails_native_log(tmp_path: Path):
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
    widget = AgentOutput()
    widget.sync_agents([record])
    widget.poll_native_logs_now()

    rendered = widget.get_rendered_text("agent-1", debug=True)
    assert "stderr boom" in rendered


def test_agent_output_hides_structured_reasoning_summary_delta():
    record = _agent_record("agent-1", task_id="task-001")
    widget = AgentOutput()
    widget.sync_agents([record])

    widget.ingest_canonical_event(
        {
            "type": "task.progress",
            "timestamp": "2026-03-08T10:00:00Z",
            "agent_id": "agent-1",
            "task_id": "task-001",
            "item": {"type": "reasoning", "text": "considering options"},
        }
    )
    widget.ingest_canonical_event(
        {
            "type": "reasoning.summary.delta",
            "timestamp": "2026-03-08T10:00:01Z",
            "agent_id": "agent-1",
            "task_id": "task-001",
            "delta": "considering options",
            "item_id": "rs-1",
        }
    )

    rendered = widget.get_rendered_text("agent-1")
    assert "considering options" in rendered
    assert "reasoning.summary.delta" not in rendered
