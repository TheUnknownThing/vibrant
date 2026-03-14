"""Workflow transition policy for the Gatekeeper loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vibrant.models.state import OrchestratorStatus

from ...basic import ArtifactsCapability
from ...types import WorkflowSnapshot, WorkflowStatus
from ..shared.workflow import (
    apply_workflow_status,
    infer_resume_workflow_status,
    orchestrator_status_from_workflow,
    workflow_status_from_orchestrator,
)


@dataclass(frozen=True, slots=True)
class UITransitionPlan:
    action: Literal["noop", "pause", "resume", "end_planning", "set_status"]
    workflow_status: WorkflowStatus | None = None


def set_workflow_status(artifacts: ArtifactsCapability, status: WorkflowStatus) -> WorkflowSnapshot:
    return apply_workflow_status(artifacts, status)


def end_planning(artifacts: ArtifactsCapability) -> WorkflowSnapshot:
    return apply_workflow_status(artifacts, WorkflowStatus.EXECUTING)


def resume_workflow(artifacts: ArtifactsCapability) -> WorkflowSnapshot:
    state = artifacts.workflow_state_store.load()
    status = state.resume_status
    if status is None:
        status = infer_resume_workflow_status(
            consensus=artifacts.consensus_store.load(),
            roadmap=artifacts.roadmap_store.load(),
        )
    return apply_workflow_status(artifacts, status)


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
