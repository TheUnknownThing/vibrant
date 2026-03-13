"""Role-specific result payloads emitted by agent runtimes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RoleResultPayload(BaseModel):
    """Base class for role-specific run meaning."""

    model_config = ConfigDict(extra="forbid")

    role: str
    succeeded: bool = False
    awaiting_input: bool = False
    summary: str | None = None
    error: str | None = None
    exit_code: int | None = None


class GenericRoleResult(RoleResultPayload):
    """Fallback payload for roles without richer semantics."""

    transcript: str = ""


class CodeRoleResult(RoleResultPayload):
    """Semantic result for a code-execution run."""

    role: Literal["code"] = "code"
    transcript: str = ""
    command_count: int = 0


class TestRoleResult(RoleResultPayload):
    """Semantic result for a validation/test run."""

    role: Literal["test"] = "test"
    transcript: str = ""
    command_count: int = 0
    validation_passed: bool = False


class MergeRoleResult(RoleResultPayload):
    """Semantic result for a merge-resolution run."""

    role: Literal["merge"] = "merge"
    transcript: str = ""
    command_count: int = 0


class GatekeeperToolCall(BaseModel):
    """Structured MCP/tool action observed during a Gatekeeper run."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    tool_name: str
    status: Literal["succeeded", "failed"]
    result: Any | None = None
    error: Any | None = None


class GatekeeperRoleResult(RoleResultPayload):
    """Semantic result for a Gatekeeper run."""

    role: Literal["gatekeeper"] = "gatekeeper"
    pending_questions: list[str] = Field(default_factory=list)
    tool_calls: list[GatekeeperToolCall] = Field(default_factory=list)
    suggested_decision: str | None = Field(
        default=None,
        description=(
            "Deprecated fallback hint inferred from tool activity. "
            "Prefer durable orchestrator/task state when resolving Gatekeeper decisions."
        ),
    )
    planning_completed: bool = False
    consensus_updated: bool = False
    roadmap_updated: bool = False


AnyRoleResult = GenericRoleResult | CodeRoleResult | TestRoleResult | MergeRoleResult | GatekeeperRoleResult


def build_generic_role_result(
    *,
    role: str,
    transcript: str,
    summary: str | None,
    error: str | None,
    exit_code: int | None,
    awaiting_input: bool,
) -> GenericRoleResult:
    """Build a fallback role result from generic runtime facts."""

    return GenericRoleResult(
        role=role,
        succeeded=error is None and not awaiting_input,
        awaiting_input=awaiting_input,
        summary=summary,
        error=error,
        exit_code=exit_code,
        transcript=transcript,
    )


def build_code_role_result(
    *,
    role: str,
    transcript: str,
    summary: str | None,
    error: str | None,
    exit_code: int | None,
    awaiting_input: bool,
    events: Sequence[Mapping[str, Any]] | None,
) -> CodeRoleResult | TestRoleResult | GenericRoleResult:
    """Build a role payload for code-like execution roles."""

    command_count = _count_command_events(events)
    common = {
        "succeeded": error is None and not awaiting_input,
        "awaiting_input": awaiting_input,
        "summary": summary,
        "error": error,
        "exit_code": exit_code,
        "transcript": transcript,
        "command_count": command_count,
    }
    if role == "test":
        return TestRoleResult(
            role="test",
            validation_passed=error is None and not awaiting_input,
            **common,
        )
    if role == "code":
        return CodeRoleResult(role="code", **common)
    return build_generic_role_result(
        role=role,
        transcript=transcript,
        summary=summary,
        error=error,
        exit_code=exit_code,
        awaiting_input=awaiting_input,
    )


def build_merge_role_result(
    *,
    transcript: str,
    summary: str | None,
    error: str | None,
    exit_code: int | None,
    awaiting_input: bool,
    events: Sequence[Mapping[str, Any]] | None,
) -> MergeRoleResult:
    """Build a role payload for merge runs."""

    return MergeRoleResult(
        succeeded=error is None and not awaiting_input,
        awaiting_input=awaiting_input,
        summary=summary,
        error=error,
        exit_code=exit_code,
        transcript=transcript,
        command_count=_count_command_events(events),
    )


def build_gatekeeper_role_result(
    *,
    summary: str | None,
    error: str | None,
    exit_code: int | None,
    awaiting_input: bool,
    input_requests: Sequence[object] | None,
    events: Sequence[Mapping[str, Any]] | None,
) -> GatekeeperRoleResult:
    """Build a role payload for Gatekeeper runs from tool activity.

    ``suggested_decision`` is retained as a deprecated best-effort fallback for
    callers that cannot yet resolve the decision from durable orchestrator state.
    """

    tool_calls = _extract_gatekeeper_tool_calls(events)
    pending_questions = _extract_pending_questions(input_requests)
    return GatekeeperRoleResult(
        succeeded=error is None and not awaiting_input,
        awaiting_input=awaiting_input,
        summary=summary,
        error=error,
        exit_code=exit_code,
        pending_questions=pending_questions,
        tool_calls=tool_calls,
        suggested_decision=_suggest_gatekeeper_decision(
            tool_calls=tool_calls,
            pending_questions=pending_questions,
            error=error,
        ),
        planning_completed=any(call.tool_name == "vibrant.end_planning_phase" for call in tool_calls),
        consensus_updated=any(call.tool_name == "vibrant.update_consensus" for call in tool_calls),
        roadmap_updated=any(call.tool_name == "vibrant.update_roadmap" for call in tool_calls),
    )


def serialize_role_result(payload: RoleResultPayload | None) -> dict[str, Any] | None:
    """Serialize a role payload for persistence."""

    if payload is None:
        return None
    return payload.model_dump(mode="json")


def parse_role_result(value: object) -> AnyRoleResult | None:
    """Parse a persisted role payload back into a typed model."""

    if isinstance(value, RoleResultPayload):
        return value
    if not isinstance(value, Mapping):
        return None

    role = value.get("role")
    model: type[RoleResultPayload]
    if role == "code":
        model = CodeRoleResult
    elif role == "test":
        model = TestRoleResult
    elif role == "merge":
        model = MergeRoleResult
    elif role == "gatekeeper":
        model = GatekeeperRoleResult
    else:
        model = GenericRoleResult

    try:
        return model.model_validate(dict(value))
    except ValidationError:
        return None


def _count_command_events(events: Sequence[Mapping[str, Any]] | None) -> int:
    if not events:
        return 0
    count = 0
    for event in events:
        if str(event.get("type") or "") != "task.progress":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping):
            continue
        item_type = _normalize_token(item.get("type"))
        if item_type == "commandexecution":
            count += 1
    return count


def _normalize_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _extract_pending_questions(input_requests: Sequence[object] | None) -> list[str]:
    if not input_requests:
        return []
    questions: list[str] = []
    for request in input_requests:
        message = getattr(request, "message", None)
        if isinstance(message, str) and message:
            questions.append(message)
    return questions


def _extract_gatekeeper_tool_calls(events: Sequence[Mapping[str, Any]] | None) -> list[GatekeeperToolCall]:
    if not events:
        return []

    calls: list[GatekeeperToolCall] = []
    for event in events:
        if str(event.get("type") or "") != "request.resolved":
            continue
        tool_name = _extract_tool_name(event)
        if tool_name is None:
            continue
        request_id = str(event.get("request_id") or "")
        calls.append(
            GatekeeperToolCall(
                request_id=request_id,
                tool_name=tool_name,
                status="failed" if event.get("error") else "succeeded",
                result=event.get("result"),
                error=event.get("error"),
            )
        )
    return calls


def _extract_tool_name(event: Mapping[str, Any]) -> str | None:
    method = event.get("method")
    if isinstance(method, str) and method.startswith("vibrant."):
        return method

    for container_name in ("params", "result"):
        container = event.get(container_name)
        if not isinstance(container, Mapping):
            continue
        for key in ("name", "tool_name", "toolName", "method"):
            value = container.get(key)
            if isinstance(value, str) and value.startswith("vibrant."):
                return value
        arguments = container.get("arguments")
        if isinstance(arguments, Mapping):
            for key in ("name", "tool_name", "toolName", "method"):
                value = arguments.get(key)
                if isinstance(value, str) and value.startswith("vibrant."):
                    return value
    return None


def _suggest_gatekeeper_decision(
    *,
    tool_calls: Sequence[GatekeeperToolCall],
    pending_questions: Sequence[str],
    error: str | None,
) -> str | None:
    if pending_questions:
        return "needs_input"

    for call in tool_calls:
        if call.status != "succeeded":
            continue
        if call.tool_name == "vibrant.review_task_outcome":
            decision = _decision_from_review_result(call.result)
            if decision is not None:
                return decision
        if call.tool_name == "vibrant.mark_task_for_retry":
            return "retry"
        if call.tool_name in {"vibrant.request_user_decision", "vibrant.set_pending_questions"}:
            return "needs_input"
        if call.tool_name == "vibrant.end_planning_phase":
            return "planned"

    if error:
        return "rejected"
    return None


def _decision_from_review_result(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    status = value.get("status")
    if not isinstance(status, str):
        return None
    normalized = status.strip().lower()
    if normalized == "accepted":
        return "accepted"
    if normalized in {"queued", "in_progress", "failed"}:
        return "retry"
    if normalized == "escalated":
        return "escalated"
    return normalized or None


__all__ = [
    "AnyRoleResult",
    "CodeRoleResult",
    "GatekeeperRoleResult",
    "GatekeeperToolCall",
    "GenericRoleResult",
    "MergeRoleResult",
    "RoleResultPayload",
    "TestRoleResult",
    "build_code_role_result",
    "build_gatekeeper_role_result",
    "build_generic_role_result",
    "build_merge_role_result",
    "parse_role_result",
    "serialize_role_result",
]
