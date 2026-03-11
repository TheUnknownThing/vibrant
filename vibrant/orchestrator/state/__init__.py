"""State and projection helpers for the orchestrator."""

from .projection import build_user_input_requested_event, rebuild_derived_state, sync_status_from_consensus

__all__ = [
    "OrchestratorStateBackend",
    "StateStore",
    "build_user_input_requested_event",
    "rebuild_derived_state",
    "sync_status_from_consensus",
]

def __getattr__(name: str):
    if name == "StateStore":
        from .store import StateStore
        return StateStore
    if name == "OrchestratorStateBackend":
        from .backend import OrchestratorStateBackend
        return OrchestratorStateBackend
    raise AttributeError(name)
