"""Agent-output projection helpers for orchestrator consumers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from vibrant.agents.utils import extract_error_message, extract_text_from_progress_item
from vibrant.models.agent import AgentRecord
from vibrant.providers.base import CanonicalEvent

from .types import AgentOutput, AgentOutputError, AgentOutputSegment, AgentProgressItem


class AgentOutputProjectionService:
    """Project canonical agent events into TUI-friendly output state."""

    def __init__(self) -> None:
        self._outputs: dict[str, AgentOutput] = {}

    def output_for_agent(self, agent_id: str) -> AgentOutput | None:
        """Return the latest projected output for one agent."""
        return self._outputs.get(agent_id)

    def output_for_record(self, record: AgentRecord) -> AgentOutput | None:
        """Return the latest projected output for one persisted agent record."""
        return self.output_for_agent(record.identity.agent_id)

    def ingest(self, event: CanonicalEvent | dict[str, Any]) -> AgentOutput | None:
        """Apply one canonical event to the agent-output projection."""
        agent_id = event.get("agent_id")
        task_id = event.get("task_id")
        if not isinstance(agent_id, str) or not agent_id:
            return None
        if not isinstance(task_id, str) or not task_id:
            return None

        output = self._outputs.get(agent_id)
        if output is None:
            output = AgentOutput(agent_id=agent_id, task_id=task_id)
            self._outputs[agent_id] = output
        else:
            output.task_id = task_id

        timestamp = _coerce_timestamp(event.get("timestamp"))
        if timestamp is not None:
            output.updated_at = timestamp

        turn_id = _event_turn_id(event)
        if turn_id is not None:
            output.turn_id = turn_id

        event_type = str(event.get("type") or "")
        if event_type == "turn.started":
            output.status = "running"
            output.phase = "running"
            output.partial_text = ""
            output.error = None
            return output

        if event_type == "reasoning.summary.delta":
            delta = str(event.get("delta") or "")
            if delta:
                output.thinking.text = f"{output.thinking.text}{delta}"
            output.thinking.status = "streaming"
            item_id = event.get("item_id")
            output.thinking.item_id = item_id if isinstance(item_id, str) and item_id else None
            output.thinking.timestamp = timestamp
            output.phase = "thinking"
            return output

        if event_type == "task.progress":
            item = event.get("item")
            if _is_reasoning_item(item):
                summary = _reasoning_summary_text(item)
                if summary:
                    output.thinking.text = summary
                output.thinking.status = "completed"
                item_id = item.get("id") if isinstance(item, dict) else None
                output.thinking.item_id = item_id if isinstance(item_id, str) and item_id else output.thinking.item_id
                output.thinking.timestamp = timestamp
                output.phase = "running"
                return output

            progress_text = extract_text_from_progress_item(item)
            if progress_text:
                output.progress.append(
                    AgentProgressItem(
                        message=progress_text,
                        item_type=_item_type(item),
                        timestamp=timestamp,
                    )
                )
            return output

        if event_type == "content.delta":
            delta = str(event.get("delta") or "")
            if delta:
                output.partial_text = f"{output.partial_text}{delta}"
            output.status = "running"
            output.phase = "responding"
            return output

        if event_type == "turn.completed":
            if output.partial_text:
                output.segments.append(
                    AgentOutputSegment(
                        kind="response",
                        text=output.partial_text,
                        timestamp=timestamp,
                    )
                )
            output.partial_text = ""
            output.status = "completed"
            output.phase = "idle"
            return output

        if event_type == "runtime.error":
            output.error = AgentOutputError(
                message=extract_error_message(event),
                raw=event.get("error") if isinstance(event.get("error"), dict) else None,
                timestamp=timestamp,
            )
            output.status = "failed"
            output.phase = "error"
            return output

        return output


def _coerce_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _event_turn_id(event: dict[str, Any]) -> str | None:
    value = event.get("turn_id")
    if isinstance(value, str) and value:
        return value
    turn = event.get("turn")
    if isinstance(turn, dict):
        turn_id = turn.get("id")
        if isinstance(turn_id, str) and turn_id:
            return turn_id
    return None


def _is_reasoning_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    item_type = item.get("type")
    return isinstance(item_type, str) and item_type.strip().lower() == "reasoning"


def _reasoning_summary_text(item: dict[str, Any]) -> str:
    summary = item.get("summary")
    if isinstance(summary, str):
        return summary
    if isinstance(summary, list):
        return " ".join(str(entry) for entry in summary if str(entry).strip())
    text = item.get("text")
    if isinstance(text, str):
        return text
    return ""


def _item_type(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    return item_type if isinstance(item_type, str) and item_type else None


__all__ = ["AgentOutputProjectionService"]
