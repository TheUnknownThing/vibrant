"""Workflow orchestration service."""

from __future__ import annotations

from vibrant.models.consensus import ConsensusStatus
from vibrant.models.state import OrchestratorStatus
from vibrant.models.task import TaskStatus

from .consensus import ConsensusService
from .roadmap import RoadmapService
from .state_store import StateStore


class WorkflowService:
    """Own workflow transition and completion rules."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        roadmap_service: RoadmapService,
        consensus_service: ConsensusService,
    ) -> None:
        self.state_store = state_store
        self.roadmap_service = roadmap_service
        self.consensus_service = consensus_service

    def begin_execution_if_needed(self) -> None:
        status = self.state_store.state.status
        if status is OrchestratorStatus.PAUSED:
            self.state_store.transition_to(OrchestratorStatus.EXECUTING)
        elif status in {OrchestratorStatus.PLANNING, OrchestratorStatus.INIT} and self.state_store.can_transition_to(
            OrchestratorStatus.EXECUTING
        ):
            self.state_store.transition_to(OrchestratorStatus.EXECUTING)

    def maybe_complete_workflow(self) -> bool:
        roadmap = self.roadmap_service.document
        if roadmap is None or not roadmap.tasks:
            return False
        if any(task.status is not TaskStatus.ACCEPTED for task in roadmap.tasks):
            return False
        if self.state_store.state.pending_questions or self.state_store.state.active_agents:
            return False

        current = self.consensus_service.current()
        if current is not None and current.status is not ConsensusStatus.COMPLETED:
            self.consensus_service.set_status(ConsensusStatus.COMPLETED)

        if self.state_store.state.status is not OrchestratorStatus.COMPLETED:
            self.state_store.transition_to(OrchestratorStatus.COMPLETED)
        return True
