"""Interface-layer adapters."""

__all__ = [
    "BasicQueryAdapter",
    "InterfaceControlPlane",
    "OrchestratorBackend",
    "PolicyCommandAdapter",
]


def __getattr__(name: str):
    if name == "BasicQueryAdapter":
        from .basic import BasicQueryAdapter

        return BasicQueryAdapter
    if name == "InterfaceControlPlane":
        from .control_plane import InterfaceControlPlane

        return InterfaceControlPlane
    if name == "OrchestratorBackend":
        from .backend import OrchestratorBackend

        return OrchestratorBackend
    if name == "PolicyCommandAdapter":
        from .policy import PolicyCommandAdapter

        return PolicyCommandAdapter
    raise AttributeError(name)
