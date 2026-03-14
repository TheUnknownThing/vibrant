"""Interface-layer MCP implementation."""

__all__ = [
    "OrchestratorFastMCPHost",
    "OrchestratorMCPServer",
]


def __getattr__(name: str):
    if name == "OrchestratorFastMCPHost":
        from .fastmcp_host import OrchestratorFastMCPHost

        return OrchestratorFastMCPHost
    if name == "OrchestratorMCPServer":
        from .server import OrchestratorMCPServer

        return OrchestratorMCPServer
    raise AttributeError(name)
