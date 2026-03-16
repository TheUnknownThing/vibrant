"""Gatekeeper planning command helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.task import TaskInfo

from ...types import QuestionPriority, WorkflowSnapshot, WorkflowStatus
from .questions import normalize_question_scope
from .transitions import end_planning as end_planning_transition
from .transitions import resume_workflow as resume_workflow_transition
from .transitions import set_workflow_status

if TYPE_CHECKING:
    from .loop import GatekeeperUserLoop


def request_user_decision(
    loop: GatekeeperUserLoop,
    text: str,
    *,
    priority: QuestionPriority = QuestionPriority.BLOCKING,
    blocking_scope: str = "planning",
    task_id: str | None = None,
    source_agent_id: str | None = None,
    source_role: str = "gatekeeper",
    source_conversation_id: str | None = None,
    source_turn_id: str | None = None,
):
    return loop.question_store.create(
        text=text,
        priority=priority,
        source_role=source_role,
        source_agent_id=source_agent_id,
        source_conversation_id=source_conversation_id,
        source_turn_id=source_turn_id,
        blocking_scope=normalize_question_scope(blocking_scope),
        task_id=task_id,
    )


def withdraw_question(loop: GatekeeperUserLoop, question_id: str, *, reason: str | None = None):
    return loop.question_store.withdraw(question_id, reason=reason)


def transition_workflow(loop: GatekeeperUserLoop, status: WorkflowStatus) -> WorkflowSnapshot:
    return set_workflow_status(
        workflow_state_store=loop.workflow_state_store,
        agent_run_store=loop.agent_run_store,
        consensus_store=loop.consensus_store,
        question_store=loop.question_store,
        attempt_store=loop.attempt_store,
        status=status,
    )


def end_planning(loop: GatekeeperUserLoop) -> WorkflowSnapshot:
    return end_planning_transition(
        workflow_state_store=loop.workflow_state_store,
        agent_run_store=loop.agent_run_store,
        consensus_store=loop.consensus_store,
        question_store=loop.question_store,
        attempt_store=loop.attempt_store,
    )


def resume_workflow(loop: GatekeeperUserLoop) -> WorkflowSnapshot:
    return resume_workflow_transition(
        workflow_state_store=loop.workflow_state_store,
        agent_run_store=loop.agent_run_store,
        consensus_store=loop.consensus_store,
        roadmap_store=loop.roadmap_store,
        question_store=loop.question_store,
        attempt_store=loop.attempt_store,
    )


def add_task(loop: GatekeeperUserLoop, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
    loop.roadmap_store.add_task(task, index=index)
    created = loop.roadmap_store.get_task(task.id)
    if created is None:
        raise KeyError(task.id)
    return created


def update_task_definition(loop: GatekeeperUserLoop, task_id: str, **patch: object) -> TaskInfo:
    return loop.roadmap_store.update_task_definition(task_id, patch)


def reorder_tasks(loop: GatekeeperUserLoop, ordered_task_ids: list[str]) -> RoadmapDocument:
    return loop.roadmap_store.reorder_tasks(ordered_task_ids)


def replace_roadmap(
    loop: GatekeeperUserLoop,
    *,
    tasks: list[TaskInfo],
    project: str | None = None,
) -> RoadmapDocument:
    return loop.roadmap_store.replace(tasks=tasks, project=project or loop.project_name)


def update_consensus(
    loop: GatekeeperUserLoop,
    *,
    status: ConsensusStatus | str | None = None,
    context: str | None = None,
) -> ConsensusDocument:
    document = loop.consensus_store.load() or ConsensusDocument(project=loop.project_name)
    if context is not None:
        document.context = context
    if status is not None:
        document.status = status if isinstance(status, ConsensusStatus) else ConsensusStatus(str(status).upper())
    return loop.consensus_store.write(document)
def write_consensus_document(loop: GatekeeperUserLoop, document: ConsensusDocument) -> ConsensusDocument:
    return loop.consensus_store.write(document)
