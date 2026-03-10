"""Authorization constants and defaults for Vibrant's embedded auth server."""

from __future__ import annotations

MCP_ACCESS_SCOPE = "mcp:access"
TASKS_READ_SCOPE = "tasks:read"
TASKS_WRITE_SCOPE = "tasks:write"
TASKS_RUN_SCOPE = "tasks:run"
ADMIN_SCOPE = "admin"


def default_role_scopes() -> dict[str, tuple[str, ...]]:
    """Return the default role-to-scope mapping used by the auth server."""
    return {
        "viewer": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE),
        "operator": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE, TASKS_RUN_SCOPE),
        "editor": (MCP_ACCESS_SCOPE, TASKS_READ_SCOPE, TASKS_WRITE_SCOPE),
        "admin": (
            MCP_ACCESS_SCOPE,
            TASKS_READ_SCOPE,
            TASKS_WRITE_SCOPE,
            TASKS_RUN_SCOPE,
            ADMIN_SCOPE,
        ),
    }


__all__ = [
    "ADMIN_SCOPE",
    "MCP_ACCESS_SCOPE",
    "TASKS_READ_SCOPE",
    "TASKS_RUN_SCOPE",
    "TASKS_WRITE_SCOPE",
    "default_role_scopes",
]
