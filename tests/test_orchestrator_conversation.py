from __future__ import annotations

import json
from pathlib import Path

from vibrant.orchestrator.basic.conversation.store import ConversationStore
from vibrant.orchestrator.basic.conversation.stream import ConversationStreamService
from vibrant.orchestrator.types import AttemptRecord, AttemptStatus


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


def test_conversation_store_normalizes_legacy_binding_ids_and_backfills_attempt_runs(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / ".vibrant")
    store.index_path.write_text(
        json.dumps(
            {
                "conv-legacy": {
                    "conversation_id": "conv-legacy",
                    "binding_ids": ["run-legacy"],
                    "active_turn_id": None,
                    "updated_at": "2026-03-14T00:00:00Z",
                    "next_sequence": 1,
                },
                "conv-attempt": {
                    "conversation_id": "conv-attempt",
                    "run_ids": [],
                    "active_turn_id": None,
                    "updated_at": "2026-03-14T00:00:00Z",
                    "next_sequence": 1,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    store.normalize_manifests(
        attempt_records=[
            AttemptRecord(
                attempt_id="attempt-1",
                task_id="task-1",
                status=AttemptStatus.RUNNING,
                workspace_id="workspace-1",
                code_run_id="run-attempt",
                validation_run_ids=[],
                merge_run_id=None,
                task_definition_version=1,
                conversation_id="conv-attempt",
                created_at="2026-03-14T00:00:00Z",
                updated_at="2026-03-14T00:00:00Z",
            )
        ]
    )

    index = json.loads(store.index_path.read_text(encoding="utf-8"))

    assert index["conv-legacy"]["run_ids"] == ["run-legacy"]
    assert "binding_ids" not in index["conv-legacy"]
    assert index["conv-attempt"]["run_ids"] == ["run-attempt"]
