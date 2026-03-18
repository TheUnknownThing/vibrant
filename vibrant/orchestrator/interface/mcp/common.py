"""Shared helpers for the redesigned MCP surface."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol, TypeAlias

from vibrant.type_defs import JSONValue


MCPHandler: TypeAlias = Callable[..., object | Awaitable[object]]


READ_SCOPE = "orchestrator.read"
CONSENSUS_WRITE_SCOPE = "orchestrator.consensus.write"
ROADMAP_WRITE_SCOPE = "orchestrator.roadmap.write"
QUESTIONS_WRITE_SCOPE = "orchestrator.questions.write"
WORKFLOW_WRITE_SCOPE = "orchestrator.workflow.write"
REVIEW_WRITE_SCOPE = "orchestrator.review.write"


class MCPError(RuntimeError):
    """Base MCP surface failure."""


class MCPAuthorizationError(MCPError):
    """Raised when a principal lacks the required scope."""


class MCPNotFoundError(MCPError):
    """Raised when a tool or resource name is unknown."""


@dataclass(frozen=True, slots=True)
class MCPPrincipal:
    """Authenticated principal bound to one orchestrator MCP session."""

    principal_id: str
    role: str
    scopes: frozenset[str]

    def allows(self, *required_scopes: str) -> bool:
        return all(scope in self.scopes for scope in required_scopes)


@dataclass(frozen=True, slots=True)
class MCPResourceDefinition:
    name: str
    description: str
    required_scopes: tuple[str, ...]
    handler: MCPHandler


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    name: str
    description: str
    required_scopes: tuple[str, ...]
    handler: MCPHandler


class BackendProtocol(Protocol):
    """Structural protocol for orchestrator backends bound to the MCP server."""


def require_scopes(principal: MCPPrincipal | None, *required_scopes: str) -> None:
    """Validate a principal against a scope set."""

    if principal is None:
        raise MCPAuthorizationError("MCP principal is required")
    if principal.allows(*required_scopes):
        return
    missing = ", ".join(scope for scope in required_scopes if scope not in principal.scopes)
    raise MCPAuthorizationError(f"Missing MCP scopes: {missing}")


def resolve_attr(target: object, dotted_name: str) -> object | None:
    """Resolve a dotted attribute path without throwing on missing values."""

    current = target
    for part in dotted_name.split("."):
        if current is None or not hasattr(current, part):
            return None
        current = getattr(current, part)
    return current


def call_backend(target: object, names: Sequence[str], /, *args: object, **kwargs: object) -> object:
    """Call the first matching backend method from a candidate name list."""

    for name in names:
        candidate = resolve_attr(target, name)
        if callable(candidate):
            return candidate(*args, **kwargs)
    joined = ", ".join(names)
    raise MCPError(f"Backend does not implement any of: {joined}")


def has_backend(target: object, *names: str) -> bool:
    """Return whether any candidate backend hook exists."""

    return any(callable(resolve_attr(target, name)) for name in names)


def serialize_value(value: object) -> JSONValue:
    """Convert dataclasses, enums, paths, and model-like objects to plain values."""

    if hasattr(value, "model_dump"):
        return serialize_value(value.model_dump(mode="json"))
    if hasattr(value, "model_dump_json"):
        return serialize_value(json.loads(value.model_dump_json()))
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: serialize_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): serialize_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [serialize_value(item) for item in value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return [serialize_value(item) for item in value]
    return value
