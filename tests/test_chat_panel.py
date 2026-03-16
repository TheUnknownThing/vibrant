from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vibrant.orchestrator.types import (
    AgentConversationEntry,
    AgentConversationView,
    AgentStreamEvent,
    QuestionPriority,
    QuestionRecord,
    QuestionStatus,
)
from vibrant.tui.widgets.chat_panel import ChatPanel
from vibrant.tui.widgets.conversation_view import (
    ConversationRegion,
    ConversationView,
    MessageBlockWidget,
    ReasoningPart,
    TextPart,
    ToolCallPart,
    _render_blocks,
)


class ChatPanelHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield ChatPanel()


def test_conversation_view_applies_streamed_gatekeeper_messages():
    view = ConversationView()
    view.ingest_stream_event(
        AgentStreamEvent(
            conversation_id="gatekeeper-1",
            entry_id="evt-1",
            source_event_id=None,
            sequence=1,
            agent_id=None,
            run_id=None,
            task_id=None,
            turn_id=None,
            item_id=None,
            type="conversation.user.message",
            text="Plan the refactor",
            payload={"role": "user"},
            created_at="2026-03-13T00:00:00Z",
        )
    )
    view.ingest_stream_event(
        AgentStreamEvent(
            conversation_id="gatekeeper-1",
            entry_id="evt-2",
            source_event_id=None,
            sequence=2,
            agent_id="gatekeeper-agent",
            run_id="gatekeeper-run-1",
            task_id=None,
            turn_id="turn-1",
            item_id=None,
            type="conversation.assistant.message.delta",
            text="Working through the redesign.",
            payload=None,
            created_at="2026-03-13T00:00:01Z",
        )
    )
    view.ingest_stream_event(
        AgentStreamEvent(
            conversation_id="gatekeeper-1",
            entry_id="evt-3",
            source_event_id=None,
            sequence=3,
            agent_id="gatekeeper-agent",
            run_id="gatekeeper-run-1",
            task_id=None,
            turn_id="turn-1",
            item_id=None,
            type="conversation.assistant.message.completed",
            text="Working through the redesign.",
            payload=None,
            created_at="2026-03-13T00:00:02Z",
        )
    )

    assert view.current_conversation_id == "gatekeeper-1"
    assert view.entry_count == 2
    assert view._conversation is not None
    assert [entry.role for entry in view._conversation.entries] == ["user", "assistant"]
    assert view._conversation.entries[1].text == "Working through the redesign."
    assert view._conversation.entries[1].finished_at == "2026-03-13T00:00:02Z"


def test_conversation_view_records_request_events_as_status_entries():
    view = ConversationView()
    view.ingest_stream_event(
        AgentStreamEvent(
            conversation_id="gatekeeper-1",
            entry_id="evt-1",
            source_event_id=None,
            sequence=1,
            agent_id="gatekeeper-agent",
            run_id="gatekeeper-run-1",
            task_id=None,
            turn_id="turn-1",
            item_id=None,
            type="conversation.request.opened",
            text=None,
            payload=None,
            created_at="2026-03-13T00:00:03Z",
        )
    )

    assert view.current_conversation_id == "gatekeeper-1"
    assert view.entry_count == 1
    assert view._conversation is not None
    assert view._conversation.entries[0].role == "system"
    assert view._conversation.entries[0].kind == "status"
    assert view._conversation.entries[0].text == "User input requested"


def test_conversation_view_splits_staggered_thinking_around_tool_output():
    view = ConversationView()
    for sequence, event_type, text, payload in (
        (1, "conversation.assistant.thinking.delta", "First thought.", None),
        (
            2,
            "conversation.tool_call.completed",
            "rg output",
            {"tool_name": "rg", "result": "matched line"},
        ),
        (3, "conversation.assistant.thinking.delta", "Second thought.", None),
    ):
        view.ingest_stream_event(
            AgentStreamEvent(
                conversation_id="gatekeeper-1",
                entry_id=f"evt-{sequence}",
                source_event_id=None,
                sequence=sequence,
                agent_id="gatekeeper-agent",
                run_id="gatekeeper-run-1",
                task_id=None,
                turn_id="turn-1",
                item_id=None,
                type=event_type,
                text=text,
                payload=payload,
                created_at=f"2026-03-13T00:00:0{sequence}Z",
            )
        )

    assert view._conversation is not None
    assert [entry.kind for entry in view._conversation.entries] == ["thinking", "tool_call", "thinking"]
    assert view._conversation.entries[0].text == "First thought."
    assert view._conversation.entries[2].text == "Second thought."


def test_chat_panel_uses_question_records_for_summary():
    panel = ChatPanel()
    panel.set_gatekeeper_state(
        status="planning",
        question_records=[
            QuestionRecord(
                question_id="q-1",
                text="What should happen after login?",
                priority=QuestionPriority.BLOCKING,
                source_role="gatekeeper",
                source_agent_id="gatekeeper-agent",
                source_conversation_id="gatekeeper-1",
                source_turn_id="turn-1",
                blocking_scope="planning",
                status=QuestionStatus.RESOLVED,
                answer="Take the user to the dashboard.",
            ),
            QuestionRecord(
                question_id="q-2",
                text="Should the roadmap include mobile support?",
                priority=QuestionPriority.BLOCKING,
                source_role="gatekeeper",
                source_agent_id="gatekeeper-agent",
                source_conversation_id="gatekeeper-1",
                source_turn_id="turn-2",
                blocking_scope="planning",
                status=QuestionStatus.PENDING,
            ),
        ],
        flash=False,
    )

    summary = panel.get_question_summary_text()

    assert "What should happen after login?" in summary
    assert "Take the user to the dashboard." in summary
    assert "Should the roadmap include mobile support?" in summary
    assert "awaiting your answer" in summary


def test_chat_panel_summary_shows_recent_withdrawn_questions() -> None:
    panel = ChatPanel()
    panel.set_gatekeeper_state(
        status="executing",
        question_records=[
            QuestionRecord(
                question_id="q-1",
                text="Legacy question",
                priority=QuestionPriority.BLOCKING,
                source_role="gatekeeper",
                source_agent_id="gatekeeper-agent",
                source_conversation_id="gatekeeper-1",
                source_turn_id="turn-1",
                blocking_scope="planning",
                status=QuestionStatus.RESOLVED,
                answer="Ignore it.",
            ),
            QuestionRecord(
                question_id="q-2",
                text="Keep desktop only?",
                priority=QuestionPriority.BLOCKING,
                source_role="gatekeeper",
                source_agent_id="gatekeeper-agent",
                source_conversation_id="gatekeeper-1",
                source_turn_id="turn-2",
                blocking_scope="workflow",
                status=QuestionStatus.RESOLVED,
                answer="No, include mobile.",
            ),
            QuestionRecord(
                question_id="q-3",
                text="Do we need offline mode?",
                priority=QuestionPriority.NORMAL,
                source_role="gatekeeper",
                source_agent_id="gatekeeper-agent",
                source_conversation_id="gatekeeper-1",
                source_turn_id="turn-3",
                blocking_scope="workflow",
                status=QuestionStatus.PENDING,
            ),
            QuestionRecord(
                question_id="q-4",
                text="Should we add import/export in v1?",
                priority=QuestionPriority.NORMAL,
                source_role="gatekeeper",
                source_agent_id="gatekeeper-agent",
                source_conversation_id="gatekeeper-1",
                source_turn_id="turn-4",
                blocking_scope="workflow",
                status=QuestionStatus.WITHDRAWN,
            ),
        ],
        flash=False,
    )

    summary = panel.get_question_summary_text()

    assert "Legacy question" not in summary
    assert "Keep desktop only?" in summary
    assert "Do we need offline mode?" in summary
    assert "Should we add import/export in v1?" in summary
    assert "Status: no longer needed" in summary


@pytest.mark.asyncio
async def test_chat_panel_renders_conversation_with_renderer_blocks() -> None:
    conversation = AgentConversationView(
        conversation_id="gatekeeper-1",
        agent_ids=["gatekeeper-agent"],
        task_ids=[],
        active_turn_id="turn-1",
        entries=[
            AgentConversationEntry(
                role="user",
                kind="message",
                turn_id="turn-1",
                text="Plan the refactor",
                payload={"role": "user"},
                started_at="2026-03-13T00:00:00Z",
                finished_at="2026-03-13T00:00:00Z",
            ),
            AgentConversationEntry(
                role="assistant",
                kind="thinking",
                turn_id="turn-1",
                text="Comparing both branches before I merge.",
                payload=None,
                started_at="2026-03-13T00:00:01Z",
                finished_at=None,
            ),
            AgentConversationEntry(
                role="tool",
                kind="tool_call",
                turn_id="turn-1",
                text="git diff",
                payload={"tool_name": "git diff", "result": "diff --git a/file.py b/file.py"},
                started_at="2026-03-13T00:00:02Z",
                finished_at="2026-03-13T00:00:03Z",
            ),
            AgentConversationEntry(
                role="assistant",
                kind="message",
                turn_id="turn-1",
                text="I found the **risky** changes.",
                payload=None,
                started_at="2026-03-13T00:00:04Z",
                finished_at="2026-03-13T00:00:04Z",
            ),
        ],
        updated_at="2026-03-13T00:00:04Z",
    )

    app = ChatPanelHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ChatPanel)

        panel.bind_conversation(conversation)
        await pilot.pause()

        region = panel.query_one(ConversationRegion)
        blocks = list(region.query(MessageBlockWidget))

        assert len(blocks) == 2
        assert blocks[0].has_class("user-msg") is True
        assert blocks[1].has_class("assistant-msg") is True
        assert blocks[1].query_one(ReasoningPart).plain_text().startswith("Reasoning...")
        assert blocks[1].query_one(ToolCallPart).plain_text() == "Tool · git diff · done"
        assert list(blocks[1].query(TextPart))[-1].source == "I found the **risky** changes."


def test_render_blocks_groups_assistant_turn_parts_and_omits_turn_status() -> None:
    conversation = AgentConversationView(
        conversation_id="gatekeeper-1",
        agent_ids=["gatekeeper-agent"],
        task_ids=[],
        active_turn_id="turn-1",
        entries=[
            AgentConversationEntry(
                role="user",
                kind="message",
                turn_id="turn-1",
                text="Plan the refactor",
                payload={"role": "user"},
                started_at="2026-03-13T00:00:00Z",
                finished_at="2026-03-13T00:00:00Z",
            ),
            AgentConversationEntry(
                role="system",
                kind="status",
                turn_id="turn-1",
                text="Turn started",
                payload=None,
                started_at="2026-03-13T00:00:00Z",
                finished_at="2026-03-13T00:00:00Z",
            ),
            AgentConversationEntry(
                role="assistant",
                kind="thinking",
                turn_id="turn-1",
                text="Comparing both branches before I merge.",
                payload=None,
                started_at="2026-03-13T00:00:01Z",
                finished_at=None,
            ),
            AgentConversationEntry(
                role="tool",
                kind="tool_call",
                turn_id="turn-1",
                text="git diff",
                payload={"tool_name": "git diff", "result": "diff --git a/file.py b/file.py"},
                started_at="2026-03-13T00:00:02Z",
                finished_at="2026-03-13T00:00:03Z",
            ),
            AgentConversationEntry(
                role="assistant",
                kind="message",
                turn_id="turn-1",
                text="I found the risky changes.",
                payload=None,
                started_at="2026-03-13T00:00:04Z",
                finished_at="2026-03-13T00:00:04Z",
            ),
            AgentConversationEntry(
                role="system",
                kind="status",
                turn_id="turn-1",
                text="Turn completed",
                payload=None,
                started_at="2026-03-13T00:00:05Z",
                finished_at="2026-03-13T00:00:05Z",
            ),
        ],
        updated_at="2026-03-13T00:00:05Z",
    )

    blocks = _render_blocks(conversation)

    assert len(blocks) == 2
    assert blocks[0].role == "user"
    assert blocks[1].role == "assistant"
    assert len(blocks[1].parts) == 4
    assert isinstance(blocks[1].parts[0], ReasoningPart)
    assert isinstance(blocks[1].parts[1], ToolCallPart)
    assert blocks[1].parts[2].plain_text() == "diff --git a/file.py b/file.py"
    assert blocks[1].parts[3].plain_text() == "I found the risky changes."
