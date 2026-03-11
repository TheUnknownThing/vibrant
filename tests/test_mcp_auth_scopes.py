"""Unit tests for MCP scope resolution helpers."""

from __future__ import annotations

import pytest

from vibrant.mcp.auth import ScopeResolutionError, expand_role_scopes, resolve_scopes, scope_string
from vibrant.mcp.authz import (
    MCPAuthorizationError,
    ORCHESTRATOR_CONSENSUS_READ_SCOPE,
    ORCHESTRATOR_WORKFLOW_WRITE_SCOPE,
    TASKS_WRITE_SCOPE,
    default_role_scopes,
    ensure_scopes,
    has_scopes,
    orchestrator_agent_scopes,
    orchestrator_gatekeeper_scopes,
)


class TestScopeResolution:
    def test_expand_role_scopes_uses_configured_mapping(self):
        role_scopes = default_role_scopes()

        assert expand_role_scopes(role_scopes, ["viewer", "operator"]) == (
            "mcp:access",
            "tasks:read",
            "tasks:run",
        )

    def test_resolve_scopes_adds_baseline_scope(self):
        role_scopes = default_role_scopes()

        resolved = resolve_scopes(
            requested_scopes=["tasks:write"],
            client_allowed_scopes=["mcp:access", "tasks:read", "tasks:write"],
            user_roles=["editor"],
            role_scopes=role_scopes,
            baseline_scopes=["mcp:access"],
        )

        assert resolved == ("mcp:access", "tasks:write")
        assert scope_string(resolved) == "mcp:access tasks:write"

    def test_resolve_scopes_rejects_missing_baseline_grant(self):
        role_scopes = default_role_scopes()

        with pytest.raises(ScopeResolutionError, match="required baseline scopes"):
            resolve_scopes(
                requested_scopes=["tasks:read"],
                client_allowed_scopes=["tasks:read"],
                user_roles=["viewer"],
                role_scopes=role_scopes,
                baseline_scopes=["mcp:access"],
            )

    def test_orchestrator_role_scope_bundles_are_exposed(self):
        assert ORCHESTRATOR_CONSENSUS_READ_SCOPE in orchestrator_gatekeeper_scopes()
        assert ORCHESTRATOR_WORKFLOW_WRITE_SCOPE in orchestrator_gatekeeper_scopes()
        assert TASKS_WRITE_SCOPE not in orchestrator_agent_scopes()

    def test_scope_helpers_enforce_required_scopes(self):
        granted = orchestrator_agent_scopes()

        assert has_scopes(granted, ("mcp:access", ORCHESTRATOR_CONSENSUS_READ_SCOPE)) is True

        with pytest.raises(MCPAuthorizationError, match=TASKS_WRITE_SCOPE):
            ensure_scopes(granted, (TASKS_WRITE_SCOPE,), action="mutate roadmap")
