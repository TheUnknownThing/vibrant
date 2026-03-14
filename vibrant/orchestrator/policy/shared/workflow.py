"""Shared workflow policy helpers."""

from __future__ import annotations

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus

from ...basic.artifacts import build_workflow_snapshot
from ...basic.stores import AgentRunStore, AttemptStore, ConsensusStore, QuestionStore, RoadmapStore, WorkflowStateStore
from ...types import WorkflowSnapshot, WorkflowStatus


def workflow_to_consensus_status(status: WorkflowStatus) -> ConsensusStatus:
    return {
        WorkflowStatus.INIT: ConsensusStatus.INIT,
        WorkflowStatus.PLANNING: ConsensusStatus.PLANNING,
        WorkflowStatus.EXECUTING: ConsensusStatus.EXECUTING,
        WorkflowStatus.PAUSED: ConsensusStatus.PAUSED,
        WorkflowStatus.COMPLETED: ConsensusStatus.COMPLETED,
        WorkflowStatus.FAILED: ConsensusStatus.FAILED,
    }[status]


def orchestrator_status_from_workflow(status: WorkflowStatus) -> OrchestratorStatus:
    return {
        WorkflowStatus.INIT: OrchestratorStatus.INIT,
        WorkflowStatus.PLANNING: OrchestratorStatus.PLANNING,
        WorkflowStatus.EXECUTING: OrchestratorStatus.EXECUTING,
        WorkflowStatus.PAUSED: OrchestratorStatus.PAUSED,
        WorkflowStatus.COMPLETED: OrchestratorStatus.COMPLETED,
        WorkflowStatus.FAILED: OrchestratorStatus.FAILED,
    }[status]


def workflow_status_from_orchestrator(status: OrchestratorStatus) -> WorkflowStatus:
    return {
        OrchestratorStatus.INIT: WorkflowStatus.INIT,
        OrchestratorStatus.PLANNING: WorkflowStatus.PLANNING,
        OrchestratorStatus.EXECUTING: WorkflowStatus.EXECUTING,
        OrchestratorStatus.PAUSED: WorkflowStatus.PAUSED,
        OrchestratorStatus.COMPLETED: WorkflowStatus.COMPLETED,
        OrchestratorStatus.FAILED: WorkflowStatus.FAILED,
    }[status]


def normalize_orchestrator_status(status: object) -> OrchestratorStatus | None:
    if isinstance(status, OrchestratorStatus):
        return status
    if isinstance(status, str):
        normalized = status.strip().lower()
        try:
            return OrchestratorStatus(normalized)
        except ValueError:
            return None
    return None


def infer_resume_workflow_status(
    *,
    consensus: ConsensusDocument | None,
    roadmap: RoadmapDocument | None,
) -> WorkflowStatus:
    if consensus is not None:
        mapped = {
            ConsensusStatus.PLANNING: WorkflowStatus.PLANNING,
            ConsensusStatus.EXECUTING: WorkflowStatus.EXECUTING,
            ConsensusStatus.PAUSED: WorkflowStatus.EXECUTING,
        }.get(consensus.status)
        if mapped is not None:
            return mapped
    if roadmap is not None and roadmap.tasks:
        return WorkflowStatus.EXECUTING
    return WorkflowStatus.PLANNING


def apply_workflow_status(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
    status: WorkflowStatus,
) -> WorkflowSnapshot:
    current_state = workflow_state_store.load()
    next_resume_status = (
        current_state.workflow_status
        if status is WorkflowStatus.PAUSED and current_state.workflow_status is not WorkflowStatus.PAUSED
        else current_state.resume_status
        if status is WorkflowStatus.PAUSED
        else None
    )
    workflow_state_store.update_workflow_status(
        status,
        resume_status=next_resume_status,
    )
    consensus_store.set_status_projection(workflow_to_consensus_status(status))
    return build_workflow_snapshot(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        question_store=question_store,
        attempt_store=attempt_store,
    )


def resume_workflow(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    roadmap_store: RoadmapStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
) -> WorkflowSnapshot:
    state = workflow_state_store.load()
    status = state.resume_status
    if status is None:
        status = infer_resume_workflow_status(
            consensus=consensus_store.load(),
            roadmap=roadmap_store.load(),
        )
    return apply_workflow_status(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        question_store=question_store,
        attempt_store=attempt_store,
        status=status,
    )


def is_execution_workflow_status(status: WorkflowStatus) -> bool:
    return status is WorkflowStatus.EXECUTING


def is_terminal_workflow_status(status: WorkflowStatus) -> bool:
    return status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}
