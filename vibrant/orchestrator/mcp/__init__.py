"""Typed MCP bridge for the redesigned orchestrator."""

__all__ = [
    "BINDING_HEADER_NAME",
    "OrchestratorFastMCPHost",
    "OrchestratorMCPServer",
]


def __getattr__(name: str):
    if name == "BINDING_HEADER_NAME":
        from ..interface.mcp.binding_registry import BINDING_HEADER_NAME

        return BINDING_HEADER_NAME
    if name == "OrchestratorFastMCPHost":
        from ..interface.mcp import OrchestratorFastMCPHost

        return OrchestratorFastMCPHost
    if name == "OrchestratorMCPServer":
        from ..interface.mcp import OrchestratorMCPServer

        return OrchestratorMCPServer
    raise AttributeError(name)
