from __future__ import annotations

import json
from pathlib import Path

from vibrant.orchestrator.basic.conversation.store import ConversationStore
from vibrant.orchestrator.basic.conversation.stream import ConversationStreamService


def test_conversation_stream_rebuilds_processed_history(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)

    stream.bind_run(
        conversation_id="gatekeeper-1",
        run_id="gatekeeper-run-1",
    )
    stream.record_host_message(conversation_id="gatekeeper-1", role="user", text="Plan the refactor")
    stream.ingest_canonical(
        {
            "type": "assistant.message.delta",
            "agent_id": "gatekeeper-a",
            "run_id": "gatekeeper-run-1",
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
            "run_id": "gatekeeper-run-1",
            "text": "Working through the redesign.",
            "turn_id": "turn-1",
            "timestamp": "2026-03-13T00:00:01Z",
            "event_id": "evt-2",
        }
    )

    view = stream.rebuild("gatekeeper-1")

    assert view is not None
    assert [entry.role for entry in view.entries] == ["user", "assistant"]
    assert view.run_ids == ["gatekeeper-run-1"]
    assert view.entries[1].text == "Working through the redesign."


def test_conversation_stream_subscriptions_receive_replay(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)
    stream.bind_run(
        conversation_id="conv-1",
        run_id="run-1",
    )
    stream.record_host_message(conversation_id="conv-1", role="user", text="hello")

    seen: list[str] = []
    subscription = stream.subscribe("conv-1", lambda event: seen.append(event.type), replay=True)
    try:
        stream.ingest_canonical(
            {
                "type": "runtime.error",
                "agent_id": "agent-1",
                "run_id": "run-1",
                "error_message": "boom",
                "timestamp": "2026-03-13T00:00:00Z",
                "event_id": "evt-err",
            }
        )
    finally:
        subscription.close()

    assert seen[0] == "conversation.user.message"
    assert seen[-1] == "conversation.runtime.error"


def test_conversation_stream_rebuild_clears_completed_active_turn(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)
    stream.bind_run(
        conversation_id="conv-1",
        run_id="run-1",
    )

    stream.ingest_canonical(
        {
            "type": "turn.started",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "timestamp": "2026-03-13T00:00:00Z",
            "event_id": "evt-start",
        }
    )
    stream.ingest_canonical(
        {
            "type": "turn.completed",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "timestamp": "2026-03-13T00:00:01Z",
            "event_id": "evt-end",
        }
    )

    view = stream.rebuild("conv-1")

    assert view is not None
    assert view.active_turn_id is None


def test_conversation_stream_ignores_agent_only_events_without_run_binding(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)
    stream.bind_run(
        conversation_id="conv-1",
        run_id="run-1",
    )

    projected = stream.ingest_canonical(
        {
            "type": "assistant.message.delta",
            "agent_id": "agent-1",
            "delta": "This should not attach without a run id.",
            "turn_id": "turn-2",
            "timestamp": "2026-03-13T00:00:02Z",
            "event_id": "evt-agent-only",
        }
    )

    assert projected == []


def test_conversation_stream_starts_new_entry_after_invisible_event(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)
    stream.bind_run(
        conversation_id="conv-1",
        run_id="run-1",
    )

    stream.ingest_canonical(
        {
            "type": "assistant.thinking.delta",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "item_id": "reason-1",
            "delta": "First thought.",
            "timestamp": "2026-03-13T00:00:00Z",
            "event_id": "evt-1",
        }
    )
    stream.ingest_canonical(
        {
            "type": "task.progress",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "item": {"id": "progress-1", "type": "reasoning", "text": "Hidden progress"},
            "text": "Hidden progress",
            "timestamp": "2026-03-13T00:00:01Z",
            "event_id": "evt-2",
        }
    )
    stream.ingest_canonical(
        {
            "type": "assistant.thinking.delta",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "item_id": "reason-1",
            "delta": "Second thought.",
            "timestamp": "2026-03-13T00:00:02Z",
            "event_id": "evt-3",
        }
    )

    view = stream.rebuild("conv-1")

    assert view is not None
    assert [entry.kind for entry in view.entries] == ["thinking", "thinking"]
    assert [entry.text for entry in view.entries] == ["First thought.", "Second thought."]


def test_conversation_stream_merges_mcp_tool_usage_into_tool_entry(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    stream = ConversationStreamService(store)
    stream.bind_run(
        conversation_id="conv-1",
        run_id="run-1",
    )

    stream.ingest_canonical(
        {
            "type": "tool.call.started",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "item_id": "tool-1",
            "tool_name": "vibrant.read_resource",
            "arguments": {
                "kind": "mcp.resource.read",
                "binding_id": "binding-1",
                "uri": "resource://roadmap",
            },
            "timestamp": "2026-03-13T00:00:00Z",
            "event_id": "evt-1",
        }
    )
    stream.ingest_canonical(
        {
            "type": "tool.call.completed",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "item_id": "tool-1",
            "tool_name": "vibrant.read_resource",
            "result": {
                "binding_id": "binding-1",
                "resource_name": "roadmap",
                "resource_uri": "resource://roadmap",
                "payload": "hello world",
            },
            "timestamp": "2026-03-13T00:00:01Z",
            "event_id": "evt-2",
        }
    )

    view = stream.rebuild("conv-1")

    assert view is not None
    assert len(view.entries) == 1
    entry = view.entries[0]
    assert entry.kind == "tool_call"
    assert entry.text == ""
    assert entry.payload is not None
    assert entry.payload["arguments"] == {
        "kind": "mcp.resource.read",
        "binding_id": "binding-1",
        "uri": "resource://roadmap",
    }
    assert entry.payload["result"] == {
        "binding_id": "binding-1",
        "resource_name": "roadmap",
        "resource_uri": "resource://roadmap",
        "payload": "hello world",
    }
    assert entry.payload["mcp_usage"] == {
        "tool_name": "vibrant.read_resource",
        "arguments": {
            "kind": "mcp.resource.read",
            "binding_id": "binding-1",
            "uri": "resource://roadmap",
        },
        "result": {
            "binding_id": "binding-1",
            "resource_name": "roadmap",
            "resource_uri": "resource://roadmap",
            "payload": "hello world",
        },
        "kind": "mcp.resource.read",
        "binding_id": "binding-1",
        "resource_uri": "resource://roadmap",
        "resource_name": "roadmap",
        "transport": "mcp",
    }


def test_conversation_store_loads_current_frames(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    frames_path = store.frames_dir / "conv-1.jsonl"
    frames_path.write_text(
        json.dumps(
            {
                "conversation_id": "conv-1",
                "entry_id": "evt-1",
                "source_event_id": None,
                "sequence": 1,
                "agent_id": None,
                "run_id": "run-1",
                "turn_id": None,
                "item_id": None,
                "type": "conversation.user.message",
                "text": "hello",
                "payload": {"role": "user"},
                "created_at": "2026-03-15T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    frames = store.load_frames("conv-1")

    assert len(frames) == 1
    assert frames[0].conversation_id == "conv-1"
    assert frames[0].run_id == "run-1"
