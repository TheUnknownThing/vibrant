"""Typed MCP surface for the orchestrator control plane."""

from vibrant.mcp.authz import MCPAuthorizationError, MCPPrincipal

from .fastmcp import EmbeddedOAuthProvider, EmbeddedOAuthTokenVerifier, create_orchestrator_fastmcp, create_orchestrator_fastmcp_app
from .server import MCPResourceDefinition, MCPToolDefinition, OrchestratorMCPServer

__all__ = [
    "EmbeddedOAuthProvider",
    "EmbeddedOAuthTokenVerifier",
    "MCPAuthorizationError",
    "MCPPrincipal",
    "MCPResourceDefinition",
    "MCPToolDefinition",
    "OrchestratorMCPServer",
    "create_orchestrator_fastmcp",
    "create_orchestrator_fastmcp_app",
]
