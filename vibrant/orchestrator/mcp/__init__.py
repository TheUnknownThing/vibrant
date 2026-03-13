"""Typed MCP bridge for the redesigned orchestrator."""

__all__ = [
    "BINDING_HEADER_NAME",
    "OrchestratorFastMCPHost",
    "OrchestratorMCPServer",
]


def __getattr__(name: str):
    if name == "BINDING_HEADER_NAME":
        from .binding_registry import BINDING_HEADER_NAME

        return BINDING_HEADER_NAME
    if name == "OrchestratorFastMCPHost":
        from .fastmcp_host import OrchestratorFastMCPHost

        return OrchestratorFastMCPHost
    if name == "OrchestratorMCPServer":
        from .server import OrchestratorMCPServer

        return OrchestratorMCPServer
    raise AttributeError(name)
