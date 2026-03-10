"""Shared agent utilities extracted from gatekeeper and lifecycle modules.

These functions are used across agent implementations and the orchestration
layer.  Promoting them to a shared module eliminates the fragile cross-import
of private names between ``vibrant.gatekeeper.gatekeeper`` and
``vibrant.orchestrator.lifecycle``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Callable

from vibrant.models.agent import AgentRecord, AgentStatus
from vibrant.providers.base import CanonicalEvent, RuntimeMode


# ---------------------------------------------------------------------------
# Adapter lifecycle helpers
# ---------------------------------------------------------------------------

async def stop_adapter_safely(adapter: Any) -> None:
    """Stop a provider adapter, swallowing exceptions."""

    try:
        await adapter.stop_session()
    except Exception:
        return


# ---------------------------------------------------------------------------
# Canonical-event data extraction
# ---------------------------------------------------------------------------

def extract_text_from_progress_item(item: Any) -> str:
    """Extract readable text from an ``item/completed`` canonical payload."""

    if not isinstance(item, dict):
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


def extract_error_message(event: CanonicalEvent) -> str:
    """Extract a human-readable error string from a ``runtime.error`` event."""

    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    if error is None:
        return "Agent runtime error"
    return str(error)


# ---------------------------------------------------------------------------
# Event forwarding
# ---------------------------------------------------------------------------

async def maybe_forward_event(
    callback: Callable[[CanonicalEvent], Any] | None,
    event: CanonicalEvent,
) -> None:
    """Invoke *callback* with *event* if it is set, awaiting coroutines."""

    if callback is None:
        return
    result = callback(event)
    if asyncio.iscoroutine(result) or inspect.isawaitable(result):
        await result


# ---------------------------------------------------------------------------
# Agent-record transition helper
# ---------------------------------------------------------------------------

def transition_terminal_agent(
    agent_record: AgentRecord,
    status: AgentStatus,
    *,
    exit_code: int,
    error: str | None = None,
) -> None:
    """Move *agent_record* to a terminal status, tolerating already-terminal records."""

    if agent_record.can_transition_to(status):
        agent_record.transition_to(status, exit_code=exit_code, error=error)
        return

    # Already in a terminal status – just update auxiliary fields.
    agent_record.exit_code = exit_code
    agent_record.error = error
    if agent_record.finished_at is None:
        agent_record.finished_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# RuntimeMode parsing
# ---------------------------------------------------------------------------

def parse_runtime_mode(value: str | None) -> RuntimeMode:
    """Parse a string value into a :class:`RuntimeMode` enum member."""

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


# ---------------------------------------------------------------------------
# Adapter introspection helpers
# ---------------------------------------------------------------------------

def extract_pid(adapter: Any) -> int | None:
    """Best-effort PID extraction from a provider adapter."""

    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) else None


def extract_exit_code(adapter: Any | None) -> int | None:
    """Best-effort exit-code extraction from a provider adapter."""

    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    returncode = getattr(process, "returncode", None)
    return returncode if isinstance(returncode, int) else None


# ---------------------------------------------------------------------------
# Turn-result summary extraction
# ---------------------------------------------------------------------------

def extract_summary_from_turn_result(turn_result: Any) -> str | None:
    """Extract a human-readable summary from a ``turn/completed`` result dict."""

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


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def timestamp_now() -> str:
    """Return an ISO-8601 UTC timestamp string (second precision, ``Z`` suffix)."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
