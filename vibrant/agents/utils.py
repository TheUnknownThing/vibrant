"""Shared utilities for agent lifecycle management.

Functions extracted from gatekeeper.py and runtime.py to avoid duplication
across agent implementations.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime, timezone
from typing import Any, Callable

from vibrant.models.agent import AgentRecord, AgentStatus
from vibrant.providers.base import CanonicalEvent, RuntimeMode


async def stop_adapter_safely(adapter: Any) -> None:
    """Stop a provider adapter session, swallowing errors."""
    try:
        await adapter.stop_session()
    except Exception:
        return


def extract_text_from_progress_item(item: Any) -> str:
    """Extract display text from a task.progress event item."""
    if not isinstance(item, dict):
        return ""
    if not _is_assistant_progress_item(item):
        return ""
    if isinstance(item.get("text"), str):
        return item["text"]
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [entry.get("text", "") for entry in content if isinstance(entry, dict)]
        return "".join(part for part in parts if part)
    return ""


def _is_assistant_progress_item(item: dict[str, Any]) -> bool:
    item_type = _normalize_progress_item_token(item.get("type"))
    if item_type in {"agentmessage", "assistantmessage"}:
        return True

    role = _normalize_progress_item_token(item.get("role"))
    if role in {"assistant", "agent", "model"}:
        return True

    author = _normalize_progress_item_token(item.get("author"))
    return author in {"assistant", "agent", "model"}


def _normalize_progress_item_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def extract_error_message(event: CanonicalEvent) -> str:
    """Extract a human-readable error string from a runtime.error event."""
    error_message = event.get("error_message")
    if isinstance(error_message, str) and error_message:
        return error_message
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    if error is None:
        return "Agent runtime error"
    return str(error)


async def maybe_forward_event(
    callback: Callable[[CanonicalEvent], Any] | None,
    event: CanonicalEvent,
) -> None:
    """Forward a canonical event through a callback, supporting sync and async."""
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


def transition_terminal_agent(
    agent_record: AgentRecord,
    status: AgentStatus,
    *,
    exit_code: int,
    error: str | None = None,
) -> None:
    """Safely transition an agent to a terminal status.

    If the transition is not allowed by the lifecycle graph, force-set
    the exit_code and error fields without changing the status.
    """
    if agent_record.can_transition_to(status):
        agent_record.transition_to(status, exit_code=exit_code, error=error)
        return

    agent_record.outcome.exit_code = exit_code
    agent_record.outcome.error = error
    if agent_record.lifecycle.finished_at is None:
        agent_record.lifecycle.finished_at = datetime.now(timezone.utc)


def parse_runtime_mode(value: str | None) -> RuntimeMode:
    """Parse a sandbox mode string into a RuntimeMode enum."""
    normalized = (value or RuntimeMode.WORKSPACE_WRITE.value).strip()
    if not normalized:
        return RuntimeMode.WORKSPACE_WRITE

    key = normalized.replace("-", "_")
    lowered = key.lower()
    mapping = {
        "read_only": RuntimeMode.READ_ONLY,
        "readonly": RuntimeMode.READ_ONLY,
        "workspace_write": RuntimeMode.WORKSPACE_WRITE,
        "workspacewrite": RuntimeMode.WORKSPACE_WRITE,
        "full_access": RuntimeMode.FULL_ACCESS,
        "danger_full_access": RuntimeMode.FULL_ACCESS,
        "dangerfullaccess": RuntimeMode.FULL_ACCESS,
    }
    try:
        return mapping[lowered]
    except KeyError as exc:
        raise ValueError(f"Unsupported runtime mode: {value}") from exc


def extract_pid(adapter: Any) -> int | None:
    """Extract the OS process ID from a provider adapter, if available."""
    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) else None


def extract_exit_code(adapter: Any | None) -> int | None:
    """Extract the process return code from a provider adapter, if available."""
    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    returncode = getattr(process, "returncode", None)
    return returncode if isinstance(returncode, int) else None


def extract_summary_from_turn_result(turn_result: Any) -> str | None:
    """Extract a summary string from the provider's turn result payload."""
    if not isinstance(turn_result, dict):
        return None

    candidates: list[Any] = [turn_result]
    turn_payload = turn_result.get("turn")
    if isinstance(turn_payload, dict):
        candidates.append(turn_payload)

    for candidate in candidates:
        if isinstance(candidate.get("summary"), str) and candidate["summary"].strip():
            return candidate["summary"].strip()
        output_text = candidate.get("outputText") or candidate.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

    return None
