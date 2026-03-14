"""Workflow transition policy for the Gatekeeper loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vibrant.models.state import OrchestratorStatus

from ...basic.stores import AgentRunStore, AttemptStore, ConsensusStore, QuestionStore, RoadmapStore, WorkflowStateStore
from ...types import WorkflowSnapshot, WorkflowStatus
from ..shared.workflow import (
    apply_workflow_status,
    infer_resume_workflow_status,
    orchestrator_status_from_workflow,
    resume_workflow as resume_workflow_session,
    workflow_status_from_orchestrator,
)


@dataclass(frozen=True, slots=True)
class UITransitionPlan:
    action: Literal["noop", "pause", "resume", "end_planning", "set_status"]
    workflow_status: WorkflowStatus | None = None


def set_workflow_status(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
    status: WorkflowStatus,
) -> WorkflowSnapshot:
    return apply_workflow_status(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        question_store=question_store,
        attempt_store=attempt_store,
        status=status,
    )


def end_planning(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
) -> WorkflowSnapshot:
    return apply_workflow_status(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        question_store=question_store,
        attempt_store=attempt_store,
        status=WorkflowStatus.EXECUTING,
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
    return resume_workflow_session(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        roadmap_store=roadmap_store,
        question_store=question_store,
        attempt_store=attempt_store,
    )


def can_transition_ui_status(current: OrchestratorStatus, next_status: OrchestratorStatus) -> bool:
    if current in {OrchestratorStatus.COMPLETED, OrchestratorStatus.FAILED}:
        return next_status is current
    return next_status in {
        OrchestratorStatus.INIT,
        OrchestratorStatus.PLANNING,
        OrchestratorStatus.EXECUTING,
        OrchestratorStatus.PAUSED,
        OrchestratorStatus.COMPLETED,
        OrchestratorStatus.FAILED,
    }


def plan_ui_transition(current: OrchestratorStatus, next_status: OrchestratorStatus) -> UITransitionPlan:
    if not can_transition_ui_status(current, next_status):
        raise ValueError(f"Workflow cannot transition from {current.value} to {next_status.value}")
    if current is next_status:
        return UITransitionPlan(action="noop")
    if next_status is OrchestratorStatus.PAUSED:
        return UITransitionPlan(action="pause")
    if current is OrchestratorStatus.PAUSED and next_status is OrchestratorStatus.PLANNING:
        return UITransitionPlan(
            action="set_status",
            workflow_status=WorkflowStatus.PLANNING,
        )
    if current is OrchestratorStatus.PAUSED and next_status is OrchestratorStatus.EXECUTING:
        return UITransitionPlan(
            action="set_status",
            workflow_status=WorkflowStatus.EXECUTING,
        )
    if next_status is OrchestratorStatus.EXECUTING and current in {
        OrchestratorStatus.INIT,
        OrchestratorStatus.PLANNING,
    }:
        return UITransitionPlan(action="end_planning")
    return UITransitionPlan(
        action="set_status",
        workflow_status=workflow_status_from_orchestrator(next_status),
    )


def infer_resume_status(consensus, roadmap) -> OrchestratorStatus:
    return orchestrator_status_from_workflow(
        infer_resume_workflow_status(consensus=consensus, roadmap=roadmap)
    )
