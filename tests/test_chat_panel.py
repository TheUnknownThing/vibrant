from __future__ import annotations

from datetime import datetime, timezone

from vibrant.orchestrator.types import AgentOutput, AgentOutputSegment, AgentThinkingState
from vibrant.tui.widgets.chat_panel import _overlay_output
from vibrant.tui.widgets.conversation_renderer import MessageBlock, ReasoningPart, TextPart, ToolCallPart


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
