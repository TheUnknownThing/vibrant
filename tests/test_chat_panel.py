from __future__ import annotations

from vibrant.orchestrator.types import AgentStreamEvent, QuestionPriority, QuestionRecord, QuestionStatus
from vibrant.tui.widgets.chat_panel import ChatPanel
from vibrant.tui.widgets.conversation_view import ConversationView


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
