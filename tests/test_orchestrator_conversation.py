from __future__ import annotations

from pathlib import Path

from vibrant.orchestrator.conversation.store import ConversationStore
from vibrant.orchestrator.conversation.stream import ConversationStreamService


def test_conversation_stream_rebuilds_processed_history(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)

    stream.bind_agent(conversation_id="gatekeeper-1", agent_id="gatekeeper-a", task_id=None)
    stream.record_host_message(conversation_id="gatekeeper-1", role="user", text="Plan the refactor")
    stream.ingest_canonical(
        {
            "type": "assistant.message.delta",
            "agent_id": "gatekeeper-a",
            "delta": "Working through the redesign.",
            "turn_id": "turn-1",
            "timestamp": "2026-03-13T00:00:00Z",
            "event_id": "evt-1",
        }
    )
    stream.ingest_canonical(
        {
            "type": "assistant.message.completed",
            "agent_id": "gatekeeper-a",
            "text": "Working through the redesign.",
            "turn_id": "turn-1",
            "timestamp": "2026-03-13T00:00:01Z",
            "event_id": "evt-2",
        }
    )

    view = stream.rebuild("gatekeeper-1")

    assert view is not None
    assert [entry.role for entry in view.entries] == ["user", "assistant"]
    assert view.entries[1].text == "Working through the redesign."


def test_conversation_stream_subscriptions_receive_replay(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)
    stream.bind_agent(conversation_id="conv-1", agent_id="agent-1", task_id="task-1")
    stream.record_host_message(conversation_id="conv-1", role="user", text="hello")

    seen: list[str] = []
    subscription = stream.subscribe("conv-1", lambda event: seen.append(event.type), replay=True)
    try:
        stream.ingest_canonical(
            {
                "type": "runtime.error",
                "agent_id": "agent-1",
                "error_message": "boom",
                "timestamp": "2026-03-13T00:00:00Z",
                "event_id": "evt-err",
            }
        )
    finally:
        subscription.close()

    assert seen[0] == "conversation.user.message"
    assert seen[-1] == "conversation.runtime.error"
