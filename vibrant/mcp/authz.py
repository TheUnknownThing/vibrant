"""Authorization constants and helpers for Vibrant's MCP surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

MCP_ACCESS_SCOPE = "mcp:access"
TASKS_READ_SCOPE = "tasks:read"
TASKS_WRITE_SCOPE = "tasks:write"
TASKS_RUN_SCOPE = "tasks:run"
ADMIN_SCOPE = "admin"
ORCHESTRATOR_CONSENSUS_READ_SCOPE = "orchestrator:consensus:read"
ORCHESTRATOR_CONSENSUS_WRITE_SCOPE = "orchestrator:consensus:write"
ORCHESTRATOR_QUESTIONS_READ_SCOPE = "orchestrator:questions:read"
ORCHESTRATOR_QUESTIONS_WRITE_SCOPE = "orchestrator:questions:write"
ORCHESTRATOR_WORKFLOW_READ_SCOPE = "orchestrator:workflow:read"
ORCHESTRATOR_WORKFLOW_WRITE_SCOPE = "orchestrator:workflow:write"


class MCPAuthorizationError(PermissionError):
    """Raised when the caller lacks the scopes needed for an MCP action."""


@dataclass(frozen=True, slots=True)
class MCPPrincipal:
    """Identity presented to one of Vibrant's MCP surfaces."""

    scopes: tuple[str, ...]
    subject_id: str | None = None


def orchestrator_gatekeeper_scopes() -> tuple[str, ...]:
    """Return the default scope bundle for Gatekeeper control-plane access."""

    return (
        MCP_ACCESS_SCOPE,
        TASKS_READ_SCOPE,
        TASKS_WRITE_SCOPE,
        ORCHESTRATOR_CONSENSUS_READ_SCOPE,
        ORCHESTRATOR_CONSENSUS_WRITE_SCOPE,
        ORCHESTRATOR_QUESTIONS_READ_SCOPE,
        ORCHESTRATOR_QUESTIONS_WRITE_SCOPE,
        ORCHESTRATOR_WORKFLOW_READ_SCOPE,
        ORCHESTRATOR_WORKFLOW_WRITE_SCOPE,
    )


def orchestrator_agent_scopes() -> tuple[str, ...]:
    """Return the default read-only scope bundle for execution agents."""

    return (
        MCP_ACCESS_SCOPE,
        TASKS_READ_SCOPE,
        ORCHESTRATOR_CONSENSUS_READ_SCOPE,
    )


def has_scopes(granted_scopes: Sequence[str], required_scopes: Sequence[str]) -> bool:
    """Return whether all required scopes are present."""

    granted = {scope for scope in granted_scopes if scope}
    return all(scope in granted for scope in required_scopes)


def ensure_scopes(granted_scopes: Sequence[str], required_scopes: Sequence[str], *, action: str) -> None:
    """Raise when the caller lacks any required scope for the requested action."""

    missing = [scope for scope in required_scopes if scope not in {item for item in granted_scopes if item}]
    if missing:
        raise MCPAuthorizationError(f"Missing required scopes for {action}: {', '.join(missing)}")


def default_role_scopes() -> dict[str, tuple[str, ...]]:
    """Return the default role-to-scope mapping used by the auth server."""

    return {
        "viewer": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
        "operator": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE, TASKS_RUN_SCOPE),
        "editor": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE, TASKS_WRITE_SCOPE),
        "gatekeeper": orchestrator_gatekeeper_scopes(),
        "agent": orchestrator_agent_scopes(),
        "admin": (
            MCP_ACCESS_SCOPE,
            TASKS_READ_SCOPE,
            TASKS_WRITE_SCOPE,
            TASKS_RUN_SCOPE,
            ADMIN_SCOPE,
            ORCHESTRATOR_CONSENSUS_READ_SCOPE,
            ORCHESTRATOR_CONSENSUS_WRITE_SCOPE,
            ORCHESTRATOR_QUESTIONS_READ_SCOPE,
            ORCHESTRATOR_QUESTIONS_WRITE_SCOPE,
            ORCHESTRATOR_WORKFLOW_READ_SCOPE,
            ORCHESTRATOR_WORKFLOW_WRITE_SCOPE,
        ),
    }


__all__ = [
    "ADMIN_SCOPE",
    "MCP_ACCESS_SCOPE",
    "MCPAuthorizationError",
    "MCPPrincipal",
    "ORCHESTRATOR_CONSENSUS_READ_SCOPE",
    "ORCHESTRATOR_CONSENSUS_WRITE_SCOPE",
    "ORCHESTRATOR_QUESTIONS_READ_SCOPE",
    "ORCHESTRATOR_QUESTIONS_WRITE_SCOPE",
    "ORCHESTRATOR_WORKFLOW_READ_SCOPE",
    "ORCHESTRATOR_WORKFLOW_WRITE_SCOPE",
    "TASKS_READ_SCOPE",
    "TASKS_RUN_SCOPE",
    "TASKS_WRITE_SCOPE",
    "default_role_scopes",
    "ensure_scopes",
    "has_scopes",
    "orchestrator_agent_scopes",
    "orchestrator_gatekeeper_scopes",
]
