"""Unit tests for MCP scope resolution helpers."""

from __future__ import annotations

import pytest

from vibrant.mcp.auth import ScopeResolutionError, expand_role_scopes, resolve_scopes, scope_string
from vibrant.mcp.authz import default_role_scopes


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
