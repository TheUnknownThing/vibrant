from __future__ import annotations

from datetime import datetime, timezone

from vibrant.orchestrator.types import AgentOutput, AgentOutputSegment, AgentThinkingState
from vibrant.tui.widgets.chat_panel import _block_from_run, _overlay_output
from vibrant.tui.widgets.conversation_renderer import MessageBlock, ReasoningPart, TextPart, ToolCallPart
from vibrant.orchestrator.types import (
    AgentRunSnapshot,
    AgentSnapshotIdentity,
    AgentRunContextSnapshot,
    AgentRunOutcomeSnapshot,
    AgentRunRetrySnapshot,
    RunEnvelope,
    RunLifecycleSnapshot,
    RunProviderSnapshot,
    RunRuntimeSnapshot,
    RunWorkspaceSnapshot,
)


def test_overlay_output_uses_committed_segments_after_completion() -> None:
    block = MessageBlock(
        message_id="run-1",
        role="assistant",
        parts=[
            ReasoningPart(status="completed", content=TextPart("Reasoned through the options.")),
            ToolCallPart(tool_name="functions.exec_command", status="success"),
            TextPart("Persisted final answer."),
        ],
        timestamp=datetime.now(timezone.utc),
    )
    output = AgentOutput(
        agent_id="gatekeeper-project",
        task_id="gatekeeper-user_conversation",
        status="completed",
        partial_text="",
        segments=[AgentOutputSegment(kind="response", text="Persisted final answer.")],
        thinking=AgentThinkingState(text="Reasoned through the options.", status="completed"),
    )

    enriched = _overlay_output(block, output=output, message_id="run-1")

    assert enriched is not None
    rendered_text_parts = [part.text for part in enriched.parts if isinstance(part, TextPart)]
    assert rendered_text_parts == ["Persisted final answer."]


def test_block_from_run_keeps_precise_lifecycle_timestamp_over_coarse_event_timestamp() -> None:
    started_at = datetime(2026, 3, 13, 12, 0, 0, 800000, tzinfo=timezone.utc)
    run = AgentRunSnapshot(
        run_id="run-1",
        agent_id="gatekeeper-project",
        task_id="gatekeeper-user_conversation",
        role="gatekeeper",
        lifecycle=RunLifecycleSnapshot(status="completed", started_at=started_at, finished_at=started_at),
        runtime=RunRuntimeSnapshot(state="completed", active=False, done=True, awaiting_input=False),
        workspace=RunWorkspaceSnapshot(),
        provider=RunProviderSnapshot(),
        envelope=RunEnvelope(state="completed"),
        payload=None,
        identity=AgentSnapshotIdentity(agent_id="gatekeeper-project", task_id="gatekeeper-user_conversation", role="gatekeeper"),
        context=AgentRunContextSnapshot(),
        outcome=AgentRunOutcomeSnapshot(summary="Final reply."),
        retry=AgentRunRetrySnapshot(),
        state="completed",
        summary="Final reply.",
        error=None,
    )
    events = [
        {
            "type": "content.delta",
            "timestamp": "2026-03-13T12:00:00Z",
            "delta": "Final reply.",
        }
    ]

    block = _block_from_run(run, events)

    assert block is not None
    assert block.timestamp == started_at
