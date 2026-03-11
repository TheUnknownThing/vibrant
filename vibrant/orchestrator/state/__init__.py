"""State and projection helpers for the orchestrator."""

from .projection import build_user_input_requested_event, rebuild_derived_state, sync_status_from_consensus

__all__ = [
    "OrchestratorEngine",
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
    if name in {"OrchestratorStateBackend", "OrchestratorEngine"}:
        from .backend import OrchestratorEngine, OrchestratorStateBackend
        return {
            "OrchestratorStateBackend": OrchestratorStateBackend,
            "OrchestratorEngine": OrchestratorEngine,
        }[name]
    raise AttributeError(name)
