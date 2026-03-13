"""Typed MCP bridge for the redesigned orchestrator."""

__all__ = ["OrchestratorMCPServer"]


def __getattr__(name: str):
    if name == "OrchestratorMCPServer":
        from .server import OrchestratorMCPServer

        return OrchestratorMCPServer
    raise AttributeError(name)
