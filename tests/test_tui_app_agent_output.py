from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vibrant.orchestrator.types import AgentStreamEvent, ConversationSummary
from vibrant.tui.app import VibrantApp


class _FakeSubscription:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeControlPlane:
    def __init__(
        self,
        *,
        summaries: list[ConversationSummary],
        frames_by_conversation_id: dict[str, list[AgentStreamEvent]],
    ) -> None:
        self._summaries = summaries
        self._frames_by_conversation_id = frames_by_conversation_id
        self.frame_calls: list[str] = []
        self.subscribe_calls: list[tuple[str, bool]] = []

    def list_conversation_summaries(self) -> list[ConversationSummary]:
        return list(self._summaries)

    def conversation_frames(self, conversation_id: str) -> list[AgentStreamEvent]:
        self.frame_calls.append(conversation_id)
        return list(self._frames_by_conversation_id.get(conversation_id, []))

    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False) -> _FakeSubscription:
        self.subscribe_calls.append((conversation_id, replay))
        return _FakeSubscription()


class _FakeAgentOutput:
    def __init__(self) -> None:
        self.synced_calls: list[tuple[list[str], list[object]]] = []
        self.ingested_events: list[AgentStreamEvent] = []

    def sync_conversations(self, conversations, agents) -> None:
        self.synced_calls.append(([summary.conversation_id for summary in conversations], list(agents)))

    def ingest_stream_event(self, event: AgentStreamEvent) -> None:
        self.ingested_events.append(event)


def _summary(conversation_id: str) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=conversation_id,
        agent_ids=["agent-1"],
        task_ids=["task-1"],
        latest_run_id="run-1",
        updated_at="2026-03-16T00:00:00Z",
    )


def _event(conversation_id: str, sequence: int) -> AgentStreamEvent:
    return AgentStreamEvent(
        conversation_id=conversation_id,
        entry_id=f"evt-{sequence}",
        source_event_id=None,
        sequence=sequence,
        agent_id="agent-1",
        run_id="run-1",
        task_id="task-1",
        turn_id="turn-1",
        item_id=None,
        type="conversation.assistant.message.completed",
        text=f"message {sequence}",
        payload=None,
        created_at=f"2026-03-16T00:00:0{sequence}Z",
    )


def test_refresh_agent_output_registry_keeps_startup_summary_only() -> None:
    summary = _summary("conv-1")
    control_plane = _FakeControlPlane(
        summaries=[summary],
        frames_by_conversation_id={"conv-1": [_event("conv-1", 1)]},
    )
    agent_output = _FakeAgentOutput()
    app = VibrantApp()
    app.orchestrator = SimpleNamespace(control_plane=control_plane)
    app.vibing_screen = lambda: SimpleNamespace(active_tab="task-status", agent_output=agent_output)

    app._refresh_agent_output_registry(SimpleNamespace(agent_records=[]))

    assert agent_output.synced_calls == [(["conv-1"], [])]
    assert control_plane.frame_calls == []
    assert control_plane.subscribe_calls == []
    assert agent_output.ingested_events == []


def test_refresh_agent_output_registry_hydrates_and_subscribes_when_logs_are_visible() -> None:
    summary = _summary("conv-1")
    frame = _event("conv-1", 1)
    control_plane = _FakeControlPlane(
        summaries=[summary],
        frames_by_conversation_id={"conv-1": [frame]},
    )
    agent_output = _FakeAgentOutput()
    app = VibrantApp()
    app.orchestrator = SimpleNamespace(control_plane=control_plane)
    app.vibing_screen = lambda: SimpleNamespace(active_tab="agent-logs", agent_output=agent_output)

    app._refresh_agent_output_registry(SimpleNamespace(agent_records=[]))
    app._refresh_agent_output_registry(SimpleNamespace(agent_records=[]))

    assert agent_output.synced_calls == [(["conv-1"], []), (["conv-1"], [])]
    assert control_plane.frame_calls == []
    assert control_plane.subscribe_calls == [("conv-1", True)]
    assert agent_output.ingested_events == []


def test_app_bar_uses_explicit_active_directory_as_subtitle() -> None:
    app = VibrantApp(cwd="/tmp/vibrant-active-dir")

    assert app.sub_title == "/tmp/vibrant-active-dir"


def test_app_bar_falls_back_to_current_directory_as_subtitle(monkeypatch) -> None:
    monkeypatch.setattr("vibrant.tui.app.os.getcwd", lambda: "/tmp/vibrant-cwd")
    monkeypatch.setattr("vibrant.tui.app.Path.home", lambda: Path("/home/tester"))

    app = VibrantApp()

    assert app.sub_title == "/tmp/vibrant-cwd"
