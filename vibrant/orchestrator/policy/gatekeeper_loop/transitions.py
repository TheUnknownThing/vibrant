"""Workflow transition policy for the Gatekeeper loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...basic.stores import AgentRunStore, AttemptStore, ConsensusStore, QuestionStore, RoadmapStore, WorkflowStateStore
from ...types import WorkflowSnapshot, WorkflowStatus
from ..workflow import (
    WorkflowPolicy,
    infer_resume_workflow_status,
)


@dataclass(frozen=True, slots=True)
class UITransitionPlan:
    action: Literal["noop", "pause", "resume", "begin_planning", "end_planning"]


def begin_planning(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
) -> WorkflowSnapshot:
    return WorkflowPolicy(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        question_store=question_store,
        attempt_store=attempt_store,
    ).begin_planning()


def end_planning(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
) -> WorkflowSnapshot:
    return WorkflowPolicy(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        question_store=question_store,
        attempt_store=attempt_store,
    ).end_planning()


def resume_workflow(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    roadmap_store: RoadmapStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
) -> WorkflowSnapshot:
    return WorkflowPolicy(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        roadmap_store=roadmap_store,
        question_store=question_store,
        attempt_store=attempt_store,
    ).resume()


def can_transition_ui_status(current: WorkflowStatus, next_status: WorkflowStatus) -> bool:
    if current in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
        return next_status is current
    return next_status in {
        WorkflowStatus.INIT,
        WorkflowStatus.PLANNING,
        WorkflowStatus.EXECUTING,
        WorkflowStatus.PAUSED,
    }


def plan_ui_transition(current: WorkflowStatus, next_status: WorkflowStatus) -> UITransitionPlan:
    if not can_transition_ui_status(current, next_status):
        raise ValueError(f"Workflow cannot transition from {current.value} to {next_status.value}")
    if current is next_status:
        return UITransitionPlan(action="noop")
    if next_status is WorkflowStatus.PAUSED:
        return UITransitionPlan(action="pause")
    if current is WorkflowStatus.INIT and next_status is WorkflowStatus.PLANNING:
        return UITransitionPlan(action="begin_planning")
    if current is WorkflowStatus.PAUSED and next_status is WorkflowStatus.PLANNING:
        return UITransitionPlan(action="resume")
    if current is WorkflowStatus.PAUSED and next_status is WorkflowStatus.EXECUTING:
        return UITransitionPlan(action="resume")
    if next_status is WorkflowStatus.EXECUTING and current in {
        WorkflowStatus.INIT,
        WorkflowStatus.PLANNING,
    }:
        return UITransitionPlan(action="end_planning")
    raise ValueError(f"Workflow cannot transition from {current.value} to {next_status.value}")


def infer_resume_status(roadmap) -> WorkflowStatus:
    return infer_resume_workflow_status(roadmap=roadmap)
