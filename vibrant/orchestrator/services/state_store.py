"""State-store service backed by the existing orchestrator engine."""

from __future__ import annotations

from vibrant.gatekeeper import GatekeeperRunResult
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.state import OrchestratorState, OrchestratorStatus
from vibrant.orchestrator.engine import OrchestratorEngine


class StateStore:
    """Thin service boundary around durable engine-backed orchestrator state."""

    def __init__(self, engine: OrchestratorEngine) -> None:
        self.engine = engine

    @property
    def state(self) -> OrchestratorState:
        return self.engine.state

    @property
    def consensus(self) -> ConsensusDocument | None:
        return self.engine.consensus

    def refresh(self) -> None:
        self.engine.refresh_from_disk()

    def apply_gatekeeper_result(self, result: GatekeeperRunResult) -> list[dict[str, object]]:
        return self.engine.apply_gatekeeper_result(result)

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        return self.engine.can_transition_to(next_status)

    def transition_to(self, next_status: OrchestratorStatus) -> None:
        self.engine.transition_to(next_status)
