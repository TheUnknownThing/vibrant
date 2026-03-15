from __future__ import annotations

from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.orchestrator.types import AgentStreamEvent, ConversationSummary
from vibrant.tui.widgets.agent_log import AgentOutput


class AgentOutputHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield AgentOutput()


def _agent_record(
    *,
    run_id: str,
    agent_id: str,
    task_id: str,
    status: AgentStatus,
    started_at: datetime,
    provider_thread_id: str | None = None,
) -> AgentRecord:
    return AgentRecord(
        identity={
            "run_id": run_id,
            "agent_id": agent_id,
            "task_id": task_id,
            "type": AgentType.CODE,
        },
        lifecycle={"status": status, "started_at": started_at},
        provider={"provider_thread_id": provider_thread_id},
    )


def _conversation_summary(
    conversation_id: str,
    *,
    agent_ids: list[str],
    task_ids: list[str],
    latest_run_id: str | None = None,
    updated_at: str = "2026-03-11T10:00:00Z",
) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=conversation_id,
        agent_ids=list(agent_ids),
        task_ids=list(task_ids),
        latest_run_id=latest_run_id,
        updated_at=updated_at,
    )


def _stream_event(
    *,
    conversation_id: str,
    sequence: int,
    event_type: str,
    created_at: str,
    text: str | None = None,
    payload: dict | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    turn_id: str | None = None,
    item_id: str | None = None,
) -> AgentStreamEvent:
    return AgentStreamEvent(
        conversation_id=conversation_id,
        entry_id=f"{conversation_id}:{sequence}",
        source_event_id=None,
        sequence=sequence,
        agent_id=agent_id,
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        item_id=item_id,
        type=event_type,  # type: ignore[arg-type]
        text=text,
        payload=payload,
        created_at=created_at,
    )


def _rendered(widget: AgentOutput) -> str:
    return widget.get_rendered_text()


def _meta_text(widget: AgentOutput) -> str:
    return str(widget.query_one("#agent-output-meta", Static).render())


@pytest.mark.asyncio
async def test_agent_output_coalesces_reasoning_deltas_and_finalizes_in_place():
    record = _agent_record(
        run_id="run-001",
        agent_id="agent-task-001",
        task_id="task-001",
        status=AgentStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    summary = _conversation_summary(
        "attempt-001",
        agent_ids=["agent-task-001"],
        task_ids=["task-001"],
        latest_run_id="run-001",
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_conversations([summary], [record])
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-001",
                sequence=1,
                event_type="conversation.assistant.thinking.delta",
                created_at="2026-03-11T10:00:01Z",
                text="Thinking through",
                agent_id="agent-task-001",
                run_id="run-001",
                task_id="task-001",
                turn_id="turn-1",
                item_id="reason-1",
            )
        )
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-001",
                sequence=2,
                event_type="conversation.assistant.thinking.delta",
                created_at="2026-03-11T10:00:02Z",
                text=" the refactor",
                agent_id="agent-task-001",
                run_id="run-001",
                task_id="task-001",
                turn_id="turn-1",
                item_id="reason-1",
            )
        )
        await pilot.pause()

        stream = widget.query_one("#agent-output-stream", Vertical)
        assert len(list(stream.children)) == 1
        assert widget.get_buffer_line_count("attempt-001") == 1
        assert widget.get_thoughts_text("attempt-001") == "Thinking through the refactor"
        assert widget.thoughts_running("attempt-001") is True

        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-001",
                sequence=3,
                event_type="conversation.progress",
                created_at="2026-03-11T10:00:03Z",
                payload={
                    "item_type": "reasoning",
                    "item": {
                        "id": "reason-1",
                        "type": "reasoning",
                        "summary": ["Refactor plan finalized"],
                    },
                },
                agent_id="agent-task-001",
                run_id="run-001",
                task_id="task-001",
                turn_id="turn-1",
                item_id="reason-1",
            )
        )
        await pilot.pause()

        assert widget.get_buffer_line_count("attempt-001") == 1
        assert widget.get_thoughts_text("attempt-001") == "Thinking through the refactor"
        assert widget.thoughts_running("attempt-001") is True


@pytest.mark.asyncio
async def test_agent_output_renders_agent_output_and_progress_blocks():
    record = _agent_record(
        run_id="run-002",
        agent_id="agent-task-002",
        task_id="task-002",
        status=AgentStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    summary = _conversation_summary(
        "attempt-002",
        agent_ids=["agent-task-002"],
        task_ids=["task-002"],
        latest_run_id="run-002",
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_conversations([summary], [record])
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-002",
                sequence=1,
                event_type="conversation.assistant.message.delta",
                created_at="2026-03-11T11:00:01Z",
                text="Here is the full assistant reply",
                agent_id="agent-task-002",
                run_id="run-002",
                task_id="task-002",
                turn_id="turn-2",
                item_id="out-1",
            )
        )
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-002",
                sequence=2,
                event_type="conversation.progress",
                created_at="2026-03-11T11:00:02Z",
                payload={
                    "item_type": "commandExecution",
                    "item": {
                        "id": "cmd-1",
                        "type": "commandExecution",
                        "command": "pytest",
                        "status": "inProgress",
                        "text": "running tests",
                    },
                },
                agent_id="agent-task-002",
                run_id="run-002",
                task_id="task-002",
                turn_id="turn-2",
                item_id="cmd-1",
            )
        )
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-002",
                sequence=3,
                event_type="conversation.progress",
                created_at="2026-03-11T11:00:03Z",
                payload={
                    "item_type": "fileChange",
                    "item": {"type": "fileChange", "path": "src/app.py"},
                },
                agent_id="agent-task-002",
                run_id="run-002",
                task_id="task-002",
                turn_id="turn-2",
            )
        )
        await pilot.pause()

        rendered = _rendered(widget)

        assert "Agent output" in rendered
        assert "Here is the full assistant reply" in rendered
        assert "$ pytest" in rendered
        assert "src/app.py" in rendered


@pytest.mark.asyncio
async def test_agent_output_debug_view_renders_stream_payloads():
    record = _agent_record(
        run_id="run-003",
        agent_id="agent-task-003",
        task_id="task-003",
        status=AgentStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    summary = _conversation_summary(
        "attempt-003",
        agent_ids=["agent-task-003"],
        task_ids=["task-003"],
        latest_run_id="run-003",
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_conversations([summary], [record])
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-003",
                sequence=1,
                event_type="conversation.tool_call.started",
                created_at="2026-03-11T12:00:00Z",
                text="vibrant.ask_question",
                payload={
                    "tool_name": "vibrant.ask_question",
                    "arguments": {"prompt": "Need clarification"},
                },
                agent_id="agent-task-003",
                run_id="run-003",
                task_id="task-003",
                turn_id="turn-3",
                item_id="tool-1",
            )
        )
        await pilot.pause()

        debug_text = widget.get_rendered_text("attempt-003", debug=True)

        assert "conversation.tool_call.started" in debug_text
        assert "vibrant.ask_question" in debug_text


@pytest.mark.asyncio
async def test_agent_output_starts_new_reasoning_block_after_kind_change():
    record = _agent_record(
        run_id="run-004",
        agent_id="agent-task-004",
        task_id="task-004",
        status=AgentStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    summary = _conversation_summary(
        "attempt-004",
        agent_ids=["agent-task-004"],
        task_ids=["task-004"],
        latest_run_id="run-004",
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_conversations([summary], [record])
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-004",
                sequence=1,
                event_type="conversation.assistant.thinking.delta",
                created_at="2026-03-11T12:10:00Z",
                text="First reasoning block",
                agent_id="agent-task-004",
                run_id="run-004",
                task_id="task-004",
                turn_id="turn-4",
                item_id="reason-4",
            )
        )
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-004",
                sequence=2,
                event_type="conversation.tool_call.started",
                created_at="2026-03-11T12:10:01Z",
                text="vibrant.request_user_decision",
                payload={"tool_name": "vibrant.request_user_decision"},
                agent_id="agent-task-004",
                run_id="run-004",
                task_id="task-004",
                turn_id="turn-4",
                item_id="tool-4",
            )
        )
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-004",
                sequence=3,
                event_type="conversation.progress",
                created_at="2026-03-11T12:10:02Z",
                payload={
                    "item_type": "reasoning",
                    "item": {
                        "id": "reason-4",
                        "type": "reasoning",
                        "summary": ["Second reasoning block"],
                    },
                },
                agent_id="agent-task-004",
                run_id="run-004",
                task_id="task-004",
                turn_id="turn-4",
                item_id="reason-4",
            )
        )
        await pilot.pause()

        stream = widget.query_one("#agent-output-stream", Vertical)
        assert len(list(stream.children)) == 2
        assert widget.get_buffer_line_count("attempt-004") == 2
        assert widget.get_thoughts_text("attempt-004") == "First reasoning block"
        assert widget.thoughts_running("attempt-004") is True
        rendered = _rendered(widget)
        assert "First reasoning block" in rendered
        assert "vibrant.request_user_decision" in rendered
        assert "Second reasoning block" not in rendered


@pytest.mark.asyncio
async def test_agent_output_updates_meta_when_switching_conversations_for_same_agent():
    older_start = datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc)
    newer_start = datetime(2026, 3, 11, 9, 5, tzinfo=timezone.utc)
    completed = _agent_record(
        run_id="run-old",
        agent_id="agent-task-020",
        task_id="task-020",
        status=AgentStatus.COMPLETED,
        started_at=older_start,
        provider_thread_id="thread-old",
    )
    running = _agent_record(
        run_id="run-new",
        agent_id="agent-task-020",
        task_id="task-020",
        status=AgentStatus.RUNNING,
        started_at=newer_start,
        provider_thread_id="thread-new",
    )
    old_summary = _conversation_summary(
        "attempt-old",
        agent_ids=["agent-task-020"],
        task_ids=["task-020"],
        latest_run_id="run-old",
        updated_at="2026-03-11T09:00:00Z",
    )
    new_summary = _conversation_summary(
        "attempt-new",
        agent_ids=["agent-task-020"],
        task_ids=["task-020"],
        latest_run_id="run-new",
        updated_at="2026-03-11T09:05:00Z",
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_conversations([old_summary, new_summary], [completed, running])
        await pilot.pause()

        assert widget.active_conversation_id == "attempt-new"
        meta = _meta_text(widget)
        assert "attempt-new" in meta
        assert "run-new" in meta
        assert "running" in meta

        widget.action_cycle_agent()
        await pilot.pause()

        assert widget.active_conversation_id == "attempt-old"
        meta = _meta_text(widget)
        assert "attempt-old" in meta
        assert "run-old" in meta
        assert "completed" in meta


@pytest.mark.asyncio
async def test_agent_output_appends_output_delta_into_existing_widget():
    record = _agent_record(
        run_id="run-006",
        agent_id="agent-task-006",
        task_id="task-006",
        status=AgentStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    summary = _conversation_summary(
        "attempt-006",
        agent_ids=["agent-task-006"],
        task_ids=["task-006"],
        latest_run_id="run-006",
    )

    app = AgentOutputHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentOutput)
        widget.sync_conversations([summary], [record])
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-006",
                sequence=1,
                event_type="conversation.assistant.message.delta",
                created_at="2026-03-11T12:20:00Z",
                text="Hello, ",
                agent_id="agent-task-006",
                run_id="run-006",
                task_id="task-006",
                turn_id="turn-6",
                item_id="out-6",
            )
        )
        widget.ingest_stream_event(
            _stream_event(
                conversation_id="attempt-006",
                sequence=2,
                event_type="conversation.assistant.message.delta",
                created_at="2026-03-11T12:20:01Z",
                text="world",
                agent_id="agent-task-006",
                run_id="run-006",
                task_id="task-006",
                turn_id="turn-6",
                item_id="out-6",
            )
        )
        await pilot.pause()

        stream = widget.query_one("#agent-output-stream", Vertical)
        assert len(list(stream.children)) == 1
        assert widget.get_buffer_line_count("attempt-006") == 1
        assert "Hello, world" in _rendered(widget)
